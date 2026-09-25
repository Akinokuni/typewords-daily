#!/usr/bin/env python3
"""端到端硬校验（不信任 pi 自述）：PDF / 字体 / 词覆盖 / OSS 落地 / known 状态。

用法: verify.py --run-dir DIR [--expect-print] [--expect-mark]
输出 JSON: {ok, checks:[{name, ok, detail}], failures:[], warnings:[]}
"""
import argparse
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import twlib as T  # noqa: E402

ALLOWED_FONTS = re.compile(r"(LibertinusSerif|SourceHanSansSC)")
FORBIDDEN_FONTS = re.compile(r"(IPAGothic|Noto Sans CJK|WenQuanYi|DejaVu)", re.I)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--pdf", default=None)
    ap.add_argument("--expect-print", action="store_true")
    ap.add_argument("--expect-mark", action="store_true")
    ap.add_argument("--expect-study", action="store_true")
    a = ap.parse_args()
    run_dir = os.path.abspath(a.run_dir)

    checks, failures, warnings = [], [], []

    def check(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": str(detail)[:300]})
        if not ok:
            failures.append(name)
        return ok

    with open(os.path.join(run_dir, "due.json"), encoding="utf-8") as f:
        due = json.load(f)
    targets = [w["word"] for w in due.get("words", [])]

    pdf = a.pdf or os.path.join(run_dir, "reader.pdf")
    exists = os.path.isfile(pdf) and os.path.getsize(pdf) > 10000
    check("pdf_exists", exists, f"{pdf} {os.path.getsize(pdf) if os.path.isfile(pdf) else 0}B")
    if not exists:
        _emit(checks, failures, warnings)
        return 1

    pages = T.pdf_pages(pdf)
    check("pdf_pages", bool(pages) and pages >= 1, f"pages={pages}")

    fonts = T.pdf_fonts(pdf)
    bad = [f for f in fonts if not ALLOWED_FONTS.search(f)]
    forbidden = [f for f in fonts if FORBIDDEN_FONTS.search(f)]
    check("fonts_embedded", not bad and not forbidden, f"fonts={fonts}")

    # 词覆盖：直接从 PDF 文本层抽查（软阈值 60%，正常应 100%）
    try:
        txt = subprocess.run(["pdftotext", pdf, "-"], capture_output=True,
                             text=True, timeout=60).stdout
    except Exception as e:
        txt = ""
        warnings.append(f"pdftotext failed: {e!r}")
    hit = [w for w in targets if T.inflect_regex(w).search(txt)]
    frac = len(hit) / len(targets) if targets else 0
    check("words_in_pdf_text", frac >= 0.6,
          f"{len(hit)}/{len(targets)} = {frac:.0%}")
    if 0.6 <= frac < 1:
        warnings.append("PDF 文本层未命中词：" + " ".join(w for w in targets if w not in hit))

    if a.expect_print:
        pj = os.path.join(run_dir, "print.json")
        data = {}
        if os.path.isfile(pj):
            with open(pj, encoding="utf-8") as f:
                data = json.load(f)
        check("print_published", bool(data.get("success")), data.get("message", "no print.json"))
        check("oss_object_verified", bool(data.get("oss_verified")),
              f"etag={data.get('etag')} size={data.get('size')}")
        ack = data.get("ack") or {}
        payload = {}
        try:
            payload = json.loads(ack.get("payload") or "{}")
        except Exception:
            payload = {}
        status = str(payload.get("status") or "")
        jid = payload.get("job_id")
        if ack.get("source") == "printer":
            ok = (jid == data.get("job_id")) and status in ("downloading", "printing", "success")
            check("printer_ack", ok,
                  f"status={status} job={jid} latency={ack.get('latency_s')}s topic={ack.get('topic')}")
            if ok and status == "downloading":
                warnings.append("回执止于 downloading：已确认打印机领取，未见到 success（status 非 retained，可能漏包）")
        elif ack:
            check("printer_ack", False, f"回执来源可疑 src={ack.get('source')}（疑似自身下发回声）")
        else:
            warnings.append("未收到打印机 status 回执（出纸未经确认：客户端可能离线，或错过非 retained 消息）")

    if a.expect_mark:
        mj = os.path.join(run_dir, "marked.json")
        data = {}
        if os.path.isfile(mj):
            with open(mj, encoding="utf-8") as f:
                data = json.load(f)
        check("mark_reported_ok", bool(data.get("ok")), f"failed={data.get('failed')}")
        unverified = []
        for w in targets:
            try:
                d = T.api_get("/words/" + w)
                if not (d.get("flags") or {}).get("known"):
                    unverified.append(w)
            except Exception as e:
                unverified.append(f"{w}({e!r})")
        check("known_on_server", not unverified, f"not known: {unverified}")

    if a.expect_study:
        sj = os.path.join(run_dir, "study.json")
        data = {}
        if os.path.isfile(sj):
            with open(sj, encoding="utf-8") as f:
                data = json.load(f)
        check("study_reported_ok", bool(data.get("ok")),
              f"rating={data.get('rating')} rated={len(data.get('rated') or [])} "
              f"errors={data.get('verify_errors')}")
        per_word = ((data.get("next_due") or {}).get("per_word") or {})
        check("study_covers_all_words", len(data.get("rated") or []) == len(targets),
              f"{len(data.get('rated') or [])}/{len(targets)}")
        # 服务端复核：每个词都落了 FSRS 卡（有下次到期时间），且没有被误标 known
        bad_due, still_new, marked_known = [], [], []
        for w in targets:
            try:
                d = T.api_get("/words/" + w)
            except Exception as e:
                bad_due.append(f"{w}({e!r})")
                continue
            due = (d.get("fsrs") or {}).get("due")
            if not due:
                still_new.append(w)
            else:
                want = per_word.get(w)
                if want and str(due)[:19] != str(want)[:19]:
                    bad_due.append(f"{w} {due}!={want}")
            if (d.get("flags") or {}).get("known"):
                marked_known.append(w)
        check("fsrs_card_on_server", not still_new and not bad_due,
              f"no_card={still_new} mismatch={bad_due}")
        check("known_left_untouched", not marked_known, f"被标 known: {marked_known}")
        if data.get("advanced"):
            warnings.append(f"lastLearnIndex 推进：{data['advanced']}")

    _emit(checks, failures, warnings)
    return 1 if failures else 0


def _emit(checks, failures, warnings):
    print(json.dumps({"ok": not failures, "checks": checks,
                      "failures": failures, "warnings": warnings},
                     ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(main())