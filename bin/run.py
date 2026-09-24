#!/usr/bin/env python3
"""TypeWords 日报 Agent 编排器。

成功 → 静默（stdout 为空）；失败 → 输出 ≤10 行摘要 + 置 state/needs_attention.json 升级标志。
用法: run.py [--dry-run] [--force] [--date YYYY-MM-DD]
  --dry-run  不打印、不标记掌握（只生成 PDF 并校验）
  --force    忽略幂等（当天已成功也重跑）
"""
import datetime
import json
import os
import subprocess
import sys
import time

AGENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(AGENT, "bin"))
import twlib as T  # noqa: E402

PY = sys.executable
PI = os.environ.get("TW_PI_BIN", "/usr/local/bin/pi")  # 可用 TW_PI_BIN 覆盖（演练/换机用）

DRY = "--dry-run" in sys.argv
FORCE = "--force" in sys.argv
CUSTOM_DATE = None
if "--date" in sys.argv:
    CUSTOM_DATE = sys.argv[sys.argv.index("--date") + 1]

TODAY = CUSTOM_DATE or datetime.date.today().isoformat()
RUN_DIR = os.path.join(AGENT, "state", "runs", TODAY)
REL_RUN = os.path.join("state", "runs", TODAY)
LOG_PATH = os.path.join(AGENT, "logs", f"run-{TODAY}.log")
STATE_DIR = os.path.join(AGENT, "state")
ATTENTION = os.path.join(STATE_DIR, "needs_attention.json")

os.makedirs(RUN_DIR, exist_ok=True)
os.makedirs(os.path.join(AGENT, "out"), exist_ok=True)
_LOG = open(LOG_PATH, "a", encoding="utf-8")


def log(msg):
    _LOG.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    _LOG.flush()


def run(cmd, timeout=None, stage=""):
    log(f"$ {' '.join(str(c) for c in cmd)}")
    try:
        r = subprocess.run(cmd, cwd=AGENT, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        log(f"TIMEOUT after {timeout}s ({stage})")
        return None
    log(f"[rc={r.returncode}] out: {(r.stdout or '')[-3000:]}")
    if r.stderr:
        log(f"err: {r.stderr[-3000:]}")
    return r


def write_status(payload):
    payload.setdefault("date", TODAY)
    payload["finished_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    with open(os.path.join(RUN_DIR, "status.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    with open(os.path.join(STATE_DIR, "last_run.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)


def clear_attention():
    if os.path.exists(ATTENTION):
        os.remove(ATTENTION)
        log("cleared needs_attention flag")


def escalate(stage, detail):
    detail = (detail or "").strip()
    nonempty = [l.strip() for l in detail.splitlines() if l.strip()] or ["(no detail)"]
    tail, head = nonempty[-1], nonempty[0]
    tail_lines = nonempty[-4:]
    # 真实异常行：从尾部往前找第一行不是 caret/File 噪声的（traceback 尾行才是病根）
    clean = next((l for l in reversed(tail_lines)
                  if not l.startswith("^") and not l.startswith(("File \"", "  File"))), tail)
    # 报错摘要优先给「最后一行异常信息」，traceback 头部噪声只在末尾信息太短时补上
    brief = clean if len(clean) > 15 else head
    lines = [brief]
    if head != brief and not head.startswith(("Traceback", "File \"", "  File")):
        lines.append(head)
    flag = {"date": TODAY, "stage": stage, "error": clean[:300],
            "traceback_tail": tail_lines[-4:], "log": LOG_PATH, "run_dir": RUN_DIR,
            "ts": datetime.datetime.now().isoformat(timespec="seconds")}
    with open(ATTENTION, "w", encoding="utf-8") as f:
        json.dump(flag, f, ensure_ascii=False, indent=1)
    write_status({"ok": False, "stage": stage, "error": detail[-500:]})
    log(f"ESCALATE stage={stage} error={detail[:300]}")
    print(f"❌ TypeWords 日报失败 — {TODAY}")
    print(f"阶段：{stage}")
    for l in lines:
        print(f"原因：{l[:160]}")
    print(f"日志：{LOG_PATH}")
    print(f"目录：{RUN_DIR}")
    print("（07:25 的自动修复任务会接手；也可直接回复本消息让我处理）")
    return 1


def render_brief(nwords, words):
    with open(os.path.join(AGENT, "prompts", "daily.md"), encoding="utf-8") as f:
        tpl = f.read()
    return (tpl.replace("{{DATE}}", TODAY)
               .replace("{{RUN_DIR}}", REL_RUN)
               .replace("{{N}}", str(nwords))
               .replace("{{WORDS}}", ", ".join(words)))


# ---------------- 幂等 ----------------
lr = os.path.join(STATE_DIR, "last_run.json")
if not FORCE and os.path.exists(lr):
    try:
        with open(lr, encoding="utf-8") as f:
            prev = json.load(f)
        if prev.get("date") == TODAY and prev.get("ok"):
            log(f"idempotent skip (already ok at {prev.get('finished_at')})")
            sys.exit(0)
    except Exception:
        pass

wf = T.load_workflow()
log(f"=== run start date={TODAY} dry_run={DRY} force={FORCE} ===")

# ---------------- 1. 取数 ----------------
fetch_cmd = [PY, os.path.join(AGENT, "bin", "fetch_due.py"), "--run-dir", RUN_DIR,
             "--max", str(wf.get("max_words", 20))]
if os.environ.get("TW_INCLUDE_KNOWN") or not wf.get("exclude_known", True):
    fetch_cmd.append("--include-known")
r = run(fetch_cmd, timeout=300, stage="fetch")
if r is None or r.returncode != 0:
    sys.exit(escalate("fetch", (r.stdout + r.stderr) if r else "timeout"))

with open(os.path.join(RUN_DIR, "due.json"), encoding="utf-8") as f:
    due = json.load(f)
words = [w["word"] for w in due.get("words", [])]
if not words:
    log(f"no pending words (due_total={due.get('due_total')}, "
        f"skipped_known={len(due.get('skipped_known', []))}) — 静默跳过")
    write_status({"ok": True, "skipped": "no due words",
                  "due_total": due.get("due_total"),
                  "skipped_known": len(due.get("skipped_known", []))})
    clear_attention()
    sys.exit(0)

# ---------------- 2. pi 创作 + 3. 组版编译 ----------------
pi_cfg = wf.get("pi", {})
attempts = int(pi_cfg.get("attempts", 2))
timeout = int(pi_cfg.get("timeout_seconds", 900))
fallback_used = False
build = None
feedback = ""

for attempt in range(1, attempts + 1):
    if os.path.exists(os.path.join(RUN_DIR, "article.json")) and attempt > 1:
        os.replace(os.path.join(RUN_DIR, "article.json"),
                   os.path.join(RUN_DIR, f"article.attempt{attempt-1}.json"))
    brief = render_brief(len(words), words) + (f"\n\n反馈：{feedback}\n" if feedback else "")
    cmd = [PI, "-p", "-a",
           "--provider", pi_cfg.get("provider", "deepseek"),
           "--model", pi_cfg.get("model", "deepseek-v4-pro"),
           "--thinking", str(pi_cfg.get("thinking", "low")),
           "--tools", "read,write,edit,bash,grep,find,ls",
           "--session-dir", os.path.join(RUN_DIR, "sessions"),
           "--name", f"typewords-{TODAY}",
           "--append-system-prompt", os.path.join(AGENT, "prompts", "CONTRACT.md"),
           brief]
    pr = run(cmd, timeout=timeout, stage=f"pi-attempt{attempt}")
    log(f"pi attempt {attempt} rc={getattr(pr, 'returncode', None)}")

    build = run([PY, os.path.join(AGENT, "bin", "write_article.py"),
                 "--run-dir", RUN_DIR], timeout=600, stage="build")
    if build is not None and build.returncode == 0:
        break
    if attempt < attempts:
        feedback = ((build.stdout if build else "") or "")[-600:]
        feedback = "上一次 article.json 未通过校验：" + feedback
        log(f"build failed, retrying pi with feedback")
    else:
        # 兜底：用 API 例句成文，保证当天仍有讲义
        log("pi failed all attempts — falling back to example-sentence article")
        build = run([PY, os.path.join(AGENT, "bin", "write_article.py"),
                     "--run-dir", RUN_DIR, "--fallback"], timeout=600, stage="build-fallback")
        fallback_used = True

if build is None or build.returncode != 0:
    sys.exit(escalate("build", (build.stdout + build.stderr) if build else "timeout"))

build_info = json.loads(build.stdout.strip().splitlines()[-1])
log(f"build ok: {build_info}")

# ---------------- 4. 打印 ----------------
print_info = {"success": False, "skipped": True, "message": "dry-run"}
if not DRY and wf.get("print", True):
    pr = run([PY, os.path.join(AGENT, "bin", "print_pdf.py"), build_info["pdf"],
              "--ack-timeout", str(wf.get("print_ack_timeout", 45)),
              "--retries", str(wf.get("print_retries", 2))],
             timeout=300, stage="print")
    if pr is None or not pr.stdout.strip():
        sys.exit(escalate("print", (pr.stderr if pr else "") or "no output"))
    try:
        print_info = json.loads(pr.stdout.strip().splitlines()[-1])
    except Exception as e:
        sys.exit(escalate("print", f"无法解析打印结果: {e!r} | {pr.stdout[-400:]}"))
    with open(os.path.join(RUN_DIR, "print.json"), "w", encoding="utf-8") as f:
        json.dump(print_info, f, ensure_ascii=False, indent=1)
    if not print_info.get("success"):
        sys.exit(escalate("print", print_info.get("message", "")))

# ---------------- 5. 标记已掌握 ----------------
mark_info = {"ok": False, "skipped": True, "dry_run": True}
if not DRY and wf.get("mark_known", True):
    mr = run([PY, os.path.join(AGENT, "bin", "mark_known.py"), "--run-dir", RUN_DIR],
             timeout=600, stage="mark")
    if mr is None or not mr.stdout.strip():
        sys.exit(escalate("mark", (mr.stderr if mr else "") or "no output"))
    try:
        mark_info = json.loads(mr.stdout.strip().splitlines()[-1])
    except Exception as e:
        sys.exit(escalate("mark", f"无法解析标记结果: {e!r}"))
    if not mark_info.get("ok"):
        sys.exit(escalate("mark", json.dumps(mark_info, ensure_ascii=False)[:400]))

# ---------------- 6. 独立校验 ----------------
vcmd = [PY, os.path.join(AGENT, "bin", "verify.py"), "--run-dir", RUN_DIR,
        "--pdf", build_info["pdf"]]
if not DRY and wf.get("print", True):
    vcmd.append("--expect-print")
if not DRY and wf.get("mark_known", True):
    vcmd.append("--expect-mark")
vr = run(vcmd, timeout=300, stage="verify")
if vr is None:
    sys.exit(escalate("verify", "timeout"))
try:
    verify_info = json.loads(vr.stdout.strip().splitlines()[-1])
except Exception as e:
    sys.exit(escalate("verify", f"无法解析校验结果: {e!r} | {vr.stdout[-400:]}"))
with open(os.path.join(RUN_DIR, "verify.json"), "w", encoding="utf-8") as f:
    json.dump(verify_info, f, ensure_ascii=False, indent=1)
if not verify_info.get("ok"):
    sys.exit(escalate("verify", "失败项: " + ", ".join(verify_info.get("failures", []))))

# ---------------- 成功（静默） ----------------
status = {
    "ok": True, "degraded": fallback_used,
    "degraded_reason": "pi 未产出有效文章，已用 API 例句兜底" if fallback_used else "",
    "due_total": due.get("due_total"),
    "words": words, "word_count": len(words),
    "pdf": build_info["pdf"], "pages": build_info.get("pages"),
    "job_id": print_info.get("job_id"), "ack": print_info.get("ack"),
    "print_clients_connected": print_info.get("clients_connected"),
    "known_verified": len(mark_info.get("verified", [])),
    "warnings": verify_info.get("warnings", []),
    "dry_run": DRY,
}
write_status(status)
clear_attention()
with open(os.path.join(STATE_DIR, "history.jsonl"), "a", encoding="utf-8") as f:
    f.write(json.dumps({"date": TODAY, "words": words, "pdf": build_info["pdf"],
                        "job_id": print_info.get("job_id"),
                        "ack": bool(print_info.get("ack")), "dry_run": DRY,
                        "degraded": fallback_used},
                       ensure_ascii=False) + "\n")
log(f"=== success: {json.dumps(status, ensure_ascii=False)[:600]} ===")
sys.exit(0)