#!/usr/bin/env python3
"""打印 = 学了一遍：把当天讲义收录的词写进 TypeWords 的 FSRS 卡片（不标 known）。

与 mark_known.py 的本质区别（用户口径的关键）：
  known   = 「掌握」→ 会被 App 的新词算法永久排除，且删掉 FSRS 卡（等于把这批词从复习体系里拿走）
  studied = 「学了一遍」→ 只写 `fsrsData` 卡片（ts-fsrs 打分），词仍在复习池里，
            之后 App / 服务端按 FSRS 到期时间自动把它推到「到期复习」里 → 天天滚动复习

实现与 App 完全一致（App 源码：store.fsrsData[word] = new FSRS(store.fsrsParameters)
.next(card ?? createEmptyCard(), now, grade).card），参数直接读服务端保存的 setting，
打分默认 good（= 今天认真过了一遍，无错误；见 config/workflow.yaml 的 study_rating）。

用法: study_record.py --run-dir DIR [--rating good] [--dry-run] [--advance-index] [--force]
输出 JSON（单行）: {ok, rating, rated[], next_due, verify_errors[], advanced, skipped, dry_run}
"""
import argparse
import datetime
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import twlib as T  # noqa: E402

AGENT = T.AGENT_DIR
NODE = os.environ.get("TW_NODE_BIN", "/root/.local/bin/node")
NEXT_MJS = os.path.join(AGENT, "tools", "fsrs", "next.mjs")
FSRS_CWD = os.path.join(AGENT, "tools", "fsrs")   # ts-fsrs 装在这里（node_modules）

# 服务端没返回 setting 时的兜底参数（= App 默认值，FSRS-6）
DEFAULT_PARAMS = {
    "request_retention": 0.9,
    "maximum_interval": 36500,
    "w": [0.212, 1.2931, 2.3065, 8.2956, 6.4133, 0.8334, 3.0194, 0.001, 1.8722, 0.1666,
          0.796, 1.4835, 0.0614, 0.2629, 1.6483, 0.6014, 1.8729, 0.5425, 0.0912, 0.0658, 0.1542],
    "enable_fuzz": False,
    "enable_short_term": True,
    "learning_steps": ["1m", "10m"],
    "relearning_steps": ["10m"],
}


def fsrs_params():
    """取 App 保存的 FSRS 参数（证据优先：服务端 setting），取不到用默认。"""
    try:
        _env, val = T.get_store("setting", timeout=60)
        p = (val or {}).get("fsrsParameters") or {}
        if p.get("w"):
            return p, "server"
    except Exception as e:
        print(f"WARN setting unavailable: {e!r}", file=sys.stderr)
    return DEFAULT_PARAMS, "default"


def next_cards(items, params, rating, now_iso):
    """调 node + ts-fsrs（与 App 同一个库）为每个词算出新卡片。"""
    payload = json.dumps({"params": params, "rating": rating, "now": now_iso, "items": items})
    r = subprocess.run([NODE, NEXT_MJS], input=payload, cwd=FSRS_CWD,
                       capture_output=True, text=True, timeout=120)
    if not r.stdout.strip():
        raise RuntimeError(f"fsrs 打分器无输出 rc={r.returncode} err={r.stderr[-400:]}")
    out = json.loads(r.stdout.strip().splitlines()[-1])
    if out.get("error"):
        raise RuntimeError(f"fsrs 打分器报错: {out['error']}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--rating", default="good", choices=["again", "hard", "good", "easy"])
    ap.add_argument("--dry-run", action="store_true", help="只算不写（不改服务端）")
    ap.add_argument("--advance-index", action="store_true",
                    help="像 App 完成学习任务那样推进 lastLearnIndex（默认不动，靠卡片去重自我推进）")
    ap.add_argument("--force", action="store_true", help="当天已记录过也重写")
    a = ap.parse_args()

    with open(os.path.join(a.run_dir, "due.json"), encoding="utf-8") as f:
        batch = json.load(f)
    words = batch.get("words") or []
    names = [w["word"] for w in words]
    result = {"ok": False, "rating": a.rating, "words": names, "rated": [],
              "next_due": {}, "verify_errors": [], "advanced": None,
              "skipped": False, "dry_run": a.dry_run, "fsrs_version": None,
              "params_source": None}

    marker = os.path.join(a.run_dir, "study.json")
    if os.path.isfile(marker) and not a.force:
        with open(marker, encoding="utf-8") as f:
            prev = json.load(f)
        if prev.get("ok"):
            result.update({"ok": True, "skipped": True, "rated": prev.get("rated", []),
                           "next_due": prev.get("next_due", {}),
                           "note": "当天已记录过「学了一遍」，跳过（--force 可重写）"})
            print(json.dumps(result, ensure_ascii=False))
            return 0
    if not names:
        result["ok"] = True
        result["note"] = "没有词需要记录"
        print(json.dumps(result, ensure_ascii=False))
        return 0

    params, src = fsrs_params()
    result["params_source"] = src
    env, val = T.get_store("dict", timeout=90)
    fsrs_data = val.setdefault("fsrsData", {}) or {}
    if val.get("fsrsData") is None:
        val["fsrsData"] = fsrs_data
    lower = {str(k).lower(): k for k in fsrs_data}

    items, key_for = [], {}
    for w in names:
        k = lower.get(w.lower(), w)          # 已经有卡片就沿用原 key（大小写与 App 一致）
        key_for[w] = k
        items.append([k, fsrs_data.get(k)])

    now = datetime.datetime.now(datetime.timezone.utc)
    now_iso = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    out = next_cards(items, params, a.rating, now_iso)
    result["fsrs_version"] = out.get("version")

    for w in names:
        k = key_for[w]
        card = out["results"][k]
        fsrs_data[k] = card
        result["rated"].append(w)
        result["next_due"][w] = card["due"]

    # 可选：像 App 一样推进进度（默认关闭——避免与 App 自己完成学习任务时重复推进）
    if a.advance_index:
        book = ((val.get("word") or {}).get("bookList") or [None])[0]
        bi = (val.get("word") or {}).get("studyIndex") or 0
        books = (val.get("word") or {}).get("bookList") or []
        book = books[bi] if 0 <= bi < len(books) else None
        if book:
            new_cnt = sum(1 for w in words if str(w.get("src", "new")) == "new")
            length = int(book.get("length") or len(book.get("words") or []))
            before = int(book.get("lastLearnIndex") or 0)
            after = min(before + new_cnt, length)
            book["lastLearnIndex"] = after
            if after >= length - 1 and length != 1:
                book["complete"] = True
            result["advanced"] = {"book": book.get("name") or book.get("id"),
                                  "from": before, "to": after, "by": new_cnt, "length": length}

    if not a.dry_run:
        env["val"] = val
        env["updated_at"] = now_iso          # 比 App 本地新 → 浏览器下次同步会拉取这份
        st, body = T.put_store("dict", env, timeout=90)
        if st != 200:
            result["verify_errors"].append(f"写回失败 http={st} body={body[:200]}")
        else:
            _env2, val2 = T.get_store("dict", timeout=90)
            got = {str(k).lower(): v for k, v in (val2.get("fsrsData") or {}).items()}
            for w in names:
                card = got.get(w.lower())
                if not card:
                    result["verify_errors"].append(f"{w}: 服务端无卡片")
                    continue
                if str(card.get("due"))[:19] != str(result["next_due"][w])[:19]:
                    result["verify_errors"].append(
                        f"{w}: due 不一致 服务端={card.get('due')} 期望={result['next_due'][w]}")
                if int(card.get("state", -1)) != int(out["results"][key_for[w]]["state"]):
                    result["verify_errors"].append(f"{w}: state 不一致 {card.get('state')}")
            if result["advanced"]:
                _e3, val3 = T.get_store("dict", timeout=90)
                books3 = ((val3.get("word") or {}).get("bookList") or [])
                b3 = books3[(val3.get("word") or {}).get("studyIndex") or 0] if books3 else {}
                if int(b3.get("lastLearnIndex") or 0) != result["advanced"]["to"]:
                    result["verify_errors"].append(
                        f"lastLearnIndex 未推进: {b3.get('lastLearnIndex')} != {result['advanced']['to']}")

    result["ok"] = not result["verify_errors"]
    if not result["dry_run"]:
        with open(marker, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=1)
    dues = sorted(result["next_due"].values())
    result["next_due"] = {"min": dues[0] if dues else None, "max": dues[-1] if dues else None,
                          "per_word": result["next_due"]}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())