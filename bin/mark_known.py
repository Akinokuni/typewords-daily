#!/usr/bin/env python3
"""把当天讲义收录的词标记为已掌握（known=true），并逐词复核。不改动 wrong 标记。

用法: mark_known.py --run-dir DIR [--dry-run]
输出 JSON: {ok, marked:[], failed:[], verified:[], unverified:[]}
"""
import argparse
import json
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import twlib as T  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    with open(os.path.join(a.run_dir, "due.json"), encoding="utf-8") as f:
        due = json.load(f)
    words = [w["word"] for w in due.get("words", [])]

    result = {"ok": False, "words": words, "marked": [], "failed": [],
              "verified": [], "unverified": [], "dry_run": a.dry_run}

    if a.dry_run:
        result["ok"] = True
        result["marked"] = words
        print(json.dumps(result, ensure_ascii=False))
        return 0

    for w in words:
        try:
            st, body = T.api_post(f"/words/{urllib.parse.quote(w)}/known", {"value": True})
            if st == 200:
                result["marked"].append(w)
            else:
                result["failed"].append({"word": w, "http": st, "body": body[:200]})
        except Exception as e:
            result["failed"].append({"word": w, "error": repr(e)})

    for w in result["marked"]:  # 复核
        try:
            d = T.api_get("/words/" + urllib.parse.quote(w))
            (result["verified"] if (d.get("flags") or {}).get("known")
             else result["unverified"]).append(w)
        except Exception as e:
            result["failed"].append({"word": w, "verify_error": repr(e)})

    result["ok"] = (not result["failed"] and not result["unverified"]
                    and len(result["verified"]) == len(words))
    with open(os.path.join(a.run_dir, "marked.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())