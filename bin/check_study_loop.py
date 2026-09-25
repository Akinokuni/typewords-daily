#!/usr/bin/env python3
"""验证「学了一遍 → 到期复习」闭环：等 FSRS 卡的 due 时间过去后，
用服务端 filter=due 看这些词是否真的回到复习队列（App 的复习任务取的就是这份数据）。"""
import json, sys, time
from datetime import datetime, timezone

sys.path.insert(0, "/root/typewords-agent/bin")
import twlib as T  # noqa: E402

RUN = "/root/typewords-agent/state/runs/2026-09-25"
study = json.load(open(f"{RUN}/study.json"))
words = [w.lower() for w in study["rated"]]
per_word = study["next_due"] if "per_word" not in study["next_due"] else study["next_due"]["per_word"]
latest = max(per_word.values())
t_due = datetime.fromisoformat(latest.replace("Z", "+00:00"))

# 等到最晚的 due 过后 30 秒（最多等 20 分钟）
deadline = time.time() + 1200
while datetime.now(timezone.utc) < t_due and time.time() < deadline:
    time.sleep(20)

d = T.api_get("/words?filter=due&limit=500")
due_words = {str(w.get("word", "")).lower() for w in d["items"]}
hit = [w for w in words if w in due_words]
known_now = []
for w in hit[:3]:
    try:
        known_now.append((w, bool((T.api_get("/words/" + w).get("flags") or {}).get("known"))))
    except Exception as e:
        known_now.append((w, f"err {e!r}"))

print(json.dumps({
    "checked_at": datetime.now(timezone.utc).isoformat(),
    "due_time_max": latest,
    "server_due_total": d["total"],
    "our_words_in_due_list": len(hit),
    "our_words_total": len(words),
    "sample": hit[:6],
    "known_check": known_now,
    "loop_ok": len(hit) == len(words),
}, ensure_ascii=False, indent=1))