#!/usr/bin/env python3
"""升级检查（供 Hermes cron 的 monitor 门控使用）。

输出必须是确定性的（不含当前时间），否则每次 tick 都会被判定为变化。

  OK                                   —— 一切正常，静默
  DEGRADED <date> <reason>             —— 当天讲义用了兜底例句（可看一眼）
  ATTENTION <date> <stage> <error首行>  —— 当天流程失败，需 Hermes 接手
"""
import json
import os

AGENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(AGENT, "state")


def first_line(s):
    """取最后一行非空内容 —— 报错摘要里这才是真正的异常信息（traceback 尾部）。"""
    for l in reversed((s or "").splitlines()):
        if l.strip():
            return l.strip()[:120]
    return ""


att = os.path.join(STATE, "needs_attention.json")
if os.path.isfile(att):
    try:
        with open(att, encoding="utf-8") as f:
            d = json.load(f)
        print(f"ATTENTION {d.get('date','?')} {d.get('stage','?')} {first_line(d.get('error'))}")
        raise SystemExit(0)
    except SystemExit:
        raise
    except Exception as e:
        print(f"ATTENTION ? ? unreadable flag: {e!r}")
        raise SystemExit(0)

lr = os.path.join(STATE, "last_run.json")
if os.path.isfile(lr):
    try:
        with open(lr, encoding="utf-8") as f:
            d = json.load(f)
        if d.get("degraded"):
            print(f"DEGRADED {d.get('date','?')} {first_line(d.get('degraded_reason'))}")
            raise SystemExit(0)
    except SystemExit:
        raise
    except Exception:
        pass

# 连续多天打印无回执 → 第 3 天和第 7 天提醒一次（本地打印端可能长期离线）
hist = os.path.join(STATE, "history.jsonl")
if os.path.isfile(hist):
    try:
        with open(hist, encoding="utf-8") as f:
            rows = [json.loads(l) for l in f if l.strip()]
        rows = [r for r in rows if not r.get("dry_run") and not r.get("skipped")]
        streak = 0
        for r in reversed(rows):
            if r.get("ack"):
                break
            streak += 1
        if streak in (3, 7):
            print(f"WARN no-print-ack-streak={streak}")
            raise SystemExit(0)
    except SystemExit:
        raise
    except Exception:
        pass

print("OK")