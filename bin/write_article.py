#!/usr/bin/env python3
"""用 due.json + article.json 组版并编译 margin-ruby-reader 讲义 PDF。

用法:
  write_article.py --run-dir DIR                # 用 DIR/article.json（pi 的产物）
  write_article.py --run-dir DIR --fallback     # 忽略 article.json，用 API 例句兜底成文
退出码: 0 成功 / 2 内容问题(可重试) / 3 排版或编译失败
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import twlib as T  # noqa: E402

AGENT = T.AGENT_DIR


def load_due(run_dir):
    p = os.path.join(run_dir, "due.json")
    with open(p, encoding="utf-8") as f:
        due = json.load(f)
    return due, [w["word"] for w in due.get("words", [])]


def fallback_article(due):
    """兜底：用 API 例句拼成一篇「例句串读」文，保证每个目标词都出现。"""
    paras, buf = [], []
    for w in due.get("words", []):
        word, sent = w["word"], ""
        for cand in w.get("examples") or []:
            if T.inflect_regex(word).search(cand):
                sent = cand.strip()
                break
        if not sent:
            sent = f'The word "{word}" is useful in daily English.'
        buf.append(sent)
        if len(buf) == 4:
            paras.append(" ".join(buf))
            buf = []
    if buf:
        paras.append(" ".join(buf))
    return {
        "title": "Words in Use",
        "slug": "words-in-use",
        "paragraphs": paras,
        "glosses": {w["word"]: (w.get("fallback_gloss") or "") for w in due.get("words", [])},
        "_fallback": True,
    }


def validate(article, targets):
    """返回 (ok, missing, unused, problems)"""
    problems = []
    if not isinstance(article, dict):
        return False, targets, [], ["article.json 不是对象"]
    for k in ("title", "slug", "paragraphs"):
        if not article.get(k):
            problems.append(f"缺字段 {k}")
    paras = article.get("paragraphs") or []
    if not isinstance(paras, list) or not all(isinstance(p, str) for p in paras):
        return False, targets, [], ["paragraphs 必须是字符串数组"]
    body = " ".join(paras)
    if re.search(r"[#\[\]*_$@<>~`]", body):
        problems.append("正文含 typst/Markdown 特殊字符")
    missing = [w for w in targets if not T.inflect_regex(w).search(body)]
    unused = []
    return (not problems and not missing), missing, unused, problems


def build_typ(due, article, run_dir):
    targets = [w["word"] for w in due["words"]]
    glosses = dict(article.get("glosses") or {})
    for w in due["words"]:  # 缺 gloss / 空 gloss 用清洗过的释义兜底
        if not (glosses.get(w["word"]) or "").strip():
            glosses[w["word"]] = w.get("fallback_gloss") or ""

    used = set()
    marked = []
    for para in article["paragraphs"]:
        txt, _ = T.mark_paragraph(para, targets, glosses, used)
        marked.append(txt)

    title = T.esc_content(article["title"])
    today = due.get("date") or ""
    head = (
        '#import "template.typ": margin-ruby-reader, ruby, vocabulary, word-detail, word-focus\n\n'
        f'#show: margin-ruby-reader.with(\n  title: "{title}",\n  date: "{today}",\n)\n\n'
        + "\n\n".join(marked)
        + "\n\n#pagebreak()\n\n"
    )

    body = ['#word-focus(title: "Word list", note: none)[']
    for w in due["words"]:
        body.append("\n".join([
            "  #word-detail(",
            f'    [{T.esc_content(w["word"])}],',
            f'    pronunciation: [{T.esc_content(w.get("phonetic") or "")}],',
            f'    part: [{T.esc_content(w.get("part") or "")}],',
            f'    meaning: [{T.esc_content(w.get("meaning") or "")}],',
            "  )",
            "",
        ]))
    body.append("]")

    typ_path = os.path.join(run_dir, "reader.typ")
    with open(typ_path, "w", encoding="utf-8") as f:
        f.write(head + "\n".join(body))

    wf = T.load_workflow()
    shutil.copy(os.path.join(AGENT, wf.get("template", "assets/template.typ")),
                os.path.join(run_dir, "template.typ"))
    return typ_path, used, targets


def compile_pdf(run_dir, due, article):
    wf = T.load_workflow()
    fonts = os.path.join(AGENT, wf.get("fonts_dir", "assets/fonts"))
    r = subprocess.run(
        ["typst", "compile", "reader.typ", "reader.pdf",
         "--font-path", fonts, "--ignore-system-fonts"],
        cwd=run_dir, capture_output=True, text=True, timeout=600,
    )
    pdf = os.path.join(run_dir, "reader.pdf")
    if r.returncode != 0 or not os.path.exists(pdf):
        return None, (r.stdout + r.stderr)[-1500:]
    slug = re.sub(r"[^a-z0-9]+", "-", (article.get("slug") or "reader").lower()).strip("-")
    final = os.path.join(AGENT, "out", f"typewords-{due.get('date')}-{slug}.pdf")
    shutil.copy(pdf, final)
    return final, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--article", default=None)
    ap.add_argument("--fallback", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    run_dir = os.path.abspath(a.run_dir)

    due, targets = load_due(run_dir)
    if not targets:
        print("no target words in due.json")
        return 2

    fallback = a.fallback
    if fallback:
        article = fallback_article(due)
    else:
        apath = a.article or os.path.join(run_dir, "article.json")
        if not os.path.exists(apath):
            print(f"MISSING {apath}")
            return 2
        with open(apath, encoding="utf-8") as f:
            article = json.load(f)

    ok, missing, unused, problems = validate(article, targets)
    if not ok and not fallback:
        print("INVALID article:", "; ".join(problems) or "ok")
        if missing:
            print("missing words:", " ".join(missing))
        return 2

    typ_path, used, targets = build_typ(due, article, run_dir)
    pdf, err = compile_pdf(run_dir, due, article)
    if not pdf:
        print("COMPILE FAILED:", err)
        return 3

    pages = T.pdf_pages(pdf)
    result = {
        "ok": True, "pdf": pdf, "typ": typ_path, "pages": pages,
        "marked": sorted(used), "missing": [w for w in targets if w not in used],
        "glosses": len([g for g in (article.get("glosses") or {}).values() if g]),
        "fallback": bool(article.get("_fallback")),
    }
    if not a.quiet:
        print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())