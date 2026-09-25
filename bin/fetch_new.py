#!/usr/bin/env python3
"""抓取 TypeWords「今天要记的新词」—— 与 App 内 getCurrentStudyWord() 同一套算法，落 due.json。

App 算法（服务端渲染的前端逻辑，见 useWordCollectPicker）：
    perDay  = book.perDayStudyNumber      # 每天新词数，当前 20
    start   = book.lastLearnIndex         # 已学进度（今天从第 start 个下标开始）
    ignore  = knownWordsSet ∪ (simpleWords 当 setting.ignoreSimpleWord 时)
    for i in [start, len(words)): if len(new) >= perDay: break
        if words[i].word not in ignore: new.push(words[i])
    isEnd = start >= len(words)-1 (且书长 != 1) → 没有新词可学

用法: fetch_new.py --run-dir DIR [--max 20] [--include-known]
输出: DIR/due.json（字段与旧 due 流程兼容：words[] 里同样是 word/part/phonetic/meaning/fallback_gloss/examples）
"""
import argparse
import datetime
import json
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import twlib as T  # noqa: E402

DICT_ID_WORD_KNOWN = "wordKnown"


def current_book(export):
    """当前选中的词书（App 的 store.sdict）。"""
    val = (export.get("dict") or {}).get("val") or {}
    w = val.get("word") or {}
    books = w.get("bookList") or []
    idx = w.get("studyIndex")
    if not isinstance(idx, int) or not (0 <= idx < len(books)):
        raise RuntimeError(f"无法定位当前词书: studyIndex={idx!r} books={len(books)}")
    return val, books, books[idx]


def known_words(books):
    for b in books:
        if DICT_ID_WORD_KNOWN in (b.get("id"), b.get("enName")):
            return {str(x.get("word", "")).lower() for x in (b.get("words") or [])}
    return set()


def ignore_simple_word():
    """App setting.ignoreSimpleWord —— 取不到就按 App 默认值 false。"""
    try:
        raw = T.api_get("/data/setting", timeout=20) or {}
        val = json.loads(raw.get("value") or "{}").get("val") or {}
        return bool(val.get("ignoreSimpleWord", False))
    except Exception as e:
        print(f"WARN setting unavailable, assume ignoreSimpleWord=false: {e!r}", file=sys.stderr)
        return False


def studied_words(export):
    """已经有 FSRS 卡片的词 = 已经「学了一遍」（纸面或 App 学过）→ 不再当新词重复推。

    卡片由 study_record.py / App 自身写进 dict store 的 fsrsData。
    """
    val = (export.get("dict") or {}).get("val") or {}
    return {str(k).lower() for k in (val.get("fsrsData") or {})}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--max", type=int, default=20)
    ap.add_argument("--out", default="due.json", help="输出文件名（默认 due.json）")
    ap.add_argument("--include-known", action="store_true",
                    help="不跳过已掌握词（调试用；App 的正常算法会跳过）")
    ap.add_argument("--include-studied", action="store_true",
                    help="不跳过已学过的词（有 FSRS 卡片的词）")
    a = ap.parse_args()

    export = T.api_get("/export", timeout=90)
    val, books, book = current_book(export)

    words = book.get("words") or []
    length = int(book.get("length") or len(words))
    start = int(book.get("lastLearnIndex") or 0)
    per_day = int(book.get("perDayStudyNumber") or 20)

    ignore = set() if a.include_known else known_words(books)
    if ignore_simple_word():
        ignore |= {str(x).lower() for x in (val.get("simpleWords") or [])}
    studied = set() if a.include_studied else studied_words(export)

    is_end = start >= len(words) - 1 and len(words) != 1
    picked, skipped, skipped_studied = [], [], []
    if not is_end:
        for i in range(start, len(words)):
            if len(picked) >= min(per_day, a.max):
                break
            it = words[i]
            wl = str(it.get("word", "")).lower()
            if wl in ignore:
                skipped.append(it.get("word"))
                continue
            if wl in studied:            # 已学一遍 → 交给 FSRS 复习队列，不再当新词
                skipped_studied.append(it.get("word"))
                continue
            picked.append((i, it))

    entries = []
    for i, it in picked:
        w = it.get("word")
        try:
            d = T.api_get("/words/" + urllib.parse.quote(w))
        except Exception as e:  # 单个词详情拿不到就跳过，不阻塞整批
            print(f"WARN detail failed {w}: {e!r}", file=sys.stderr)
            continue
        entries.append({
            "word": d.get("word") or w,
            "index": i,                       # 词书下标（1-based 展示用 i+1）
            "src": "new",
            "part": T.part(d),
            "phonetic": T.phonetic(d),
            "meaning": T.senses(d),
            "fallback_gloss": T.short_gloss(d),
            "due": ((d.get("fsrs") or {}).get("due") or ""),
            "examples": [s.get("c", "") for s in (d.get("sentences") or [])[:3]],
        })

    out = {
        "date": datetime.date.today().isoformat(),
        "source": "new",
        "book": {"id": book.get("id"), "name": book.get("name"),
                 "length": length, "last_learn_index": start,
                 "per_day": per_day, "complete": bool(book.get("complete")),
                 "is_end": is_end},
        "batch": {"start_index": start, "size": len(entries)},
        "fetched": len(picked),
        "skipped_ignored": skipped,
        "skipped_studied": skipped_studied,
        "words": entries,
    }
    os.makedirs(a.run_dir, exist_ok=True)
    with open(os.path.join(a.run_dir, a.out), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print(f"source=new book={book.get('name')!r} progress={start}/{length} "
          f"per_day={per_day} skipped_ignored={len(skipped)} "
          f"skipped_studied={len(skipped_studied)} pending={len(entries)}")
    if entries:
        print("words:", " ".join(e["word"] for e in entries))
    return 0


if __name__ == "__main__":
    sys.exit(main())