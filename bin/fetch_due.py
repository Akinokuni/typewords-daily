#!/usr/bin/env python3
"""抓取 TypeWords 今日到期词（已 known 的跳过），落 due.json 供 pi 创作使用。

用法: fetch_due.py --run-dir DIR [--max 20] [--include-known]
输出: DIR/due.json
"""
import argparse
import datetime
import json
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import twlib as T  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--max", type=int, default=20)
    ap.add_argument("--include-known", action="store_true")
    a = ap.parse_args()

    due = T.api_get("/words?filter=due&limit=500")
    items = due.get("items", [])
    items.sort(key=lambda i: i.get("due") or "")  # 最逾期的优先

    words, skipped = [], []
    for it in items:
        w = it["word"]
        try:
            d = T.api_get("/words/" + urllib.parse.quote(w))
        except Exception as e:  # 单词详情拿不到就跳过，不阻塞整批
            print(f"WARN detail failed {w}: {e!r}", file=sys.stderr)
            continue
        known = bool((d.get("flags") or {}).get("known"))
        if known and not a.include_known:
            skipped.append(w)
            continue
        words.append(d)
        if len(words) >= a.max:
            break

    entries = []
    for d in words:
        entries.append({
            "word": d.get("word"),
            "part": T.part(d),
            "phonetic": T.phonetic(d),
            "meaning": T.senses(d),
            "fallback_gloss": T.short_gloss(d),
            "due": ((d.get("fsrs") or {}).get("due") or ""),
            "examples": [s.get("c", "") for s in (d.get("sentences") or [])[:3]],
        })

    out = {
        "date": datetime.date.today().isoformat(),
        "due_total": due.get("total"),
        "fetched": len(items),
        "skipped_known": skipped,
        "words": entries,
    }
    os.makedirs(a.run_dir, exist_ok=True)
    with open(os.path.join(a.run_dir, "due.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print(f"due_total={out['due_total']} fetched={out['fetched']} "
          f"skipped_known={len(skipped)} pending={len(entries)}")
    if entries:
        print("words:", " ".join(e["word"] for e in entries))
    return 0


if __name__ == "__main__":
    sys.exit(main())