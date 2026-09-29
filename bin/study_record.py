#!/usr/bin/env python3
"""打印 = 学了一遍：把当天讲义收录的词写进 TypeWords 的 FSRS 卡片（不标 known）。

与 mark_known.py 的本质区别（用户口径的关键）：
  known   = 「掌握」→ 会被 App 的新词算法永久排除，且删掉 FSRS 卡（等于把这批词从复习体系里拿走）
  studied = 「学了一遍」→ 只写 FSRS 卡片（ts-fsrs 打分），词仍在复习池里，
            之后 App / 服务端按 FSRS 到期时间自动把它推到「到期复习」里 → 天天滚动复习

写回方式（2026-09-29 服务端 ops 引擎上线后）：
  POST /api/ops   kind=word.fsrs.set   payload={"word": <k>, "card": <完整 ts-fsrs 卡>}
  每词一个 op（唯一 opId + 提交时看到的最新 baseRevision），服务端按「操作」记账、自动做
  乐观并发。**不再用 PUT /api/data/dict**：那是整文档覆盖（危险操作），会与用户正在练习的
  浏览器互相覆盖，并广播一次「文档替换」让所有在线客户端重载。

算法与 App 完全一致（App 源码：store.fsrsData[word] = new FSRS(store.fsrsParameters)
.next(card ?? createEmptyCard(), now, grade).card），参数直接读服务端保存的 setting，
打分默认 good（= 今天认真过了一遍，无错误；见 config/workflow.yaml 的 study_rating）。

打分区沿用现有卡片（2026-09-30 修正，此前一律传 None）：写卡前先读服务端现有 fsrsData，把**已有卡片**
交给 ts-fsrs 续排（App 亦如此：`store.fsrsData[word] ?? createEmptyCard()`）。传 None 会把复习词
当新卡处理（state 退回 Learning、stability/reps 清零），等于抹掉历史。

推进游标按「本批新词最大 index + 1」（2026-09-30 修正，此前按词数累加）：App 里 lastLearnIndex 的
语义是「下一个新词的下标」（usePracticeWordSession.ts:262 `lastLearnIndex += 练习的新词数`），
所以必须按 index 对齐；按词数累加会与 App 的新词窗口错位（表现为浏览器把已打印过的词当新词再练一遍）。

用法: study_record.py --run-dir DIR [--rating good] [--dry-run] [--advance-index] [--force]
输出 JSON（单行）: {ok, mode, rating, words[], rated[], revision, ops_applied, conflicts[],
                   cards_reused, next_due{min,max,per_word}, verify_errors[], advanced, skipped, dry_run}
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
import urllib.parse

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


def existing_cards(names):
    """取**服务端现有卡片（整卡）** + 对齐 key 大小写（App 也按 fsrsData 的实际 key 写，否则会多出一张卡）。

    一次 /api/export 拿全量（实测 1.3MB / 1.4s/天，可接受），换来两件事：
      1) 卡片续排：把已有卡交给 ts-fsrs，复习词的下次到期从历史推算（传 None 会清零历史）；
      2) key 大小写与线上一致（实测全是小写，但沿用实际 key 更稳）。
    export 不可用时退回 /words?filter=due（只对齐 key，卡片按空卡算，并在日志里告警）。
    返回 {word: (server_key, card_or_None)}
    """
    cards, keys = {}, {}
    try:
        d = T.api_get("/export", timeout=180) or {}
        root = d.get("dict") if isinstance(d.get("dict"), dict) else d
        fsrs = ((root or {}).get("val") or {}).get("fsrsData") or {}
        for k, c in fsrs.items():
            if isinstance(c, dict) and c.get("due"):
                cards[str(k).lower()] = c
                keys[str(k).lower()] = str(k)
    except Exception as e:
        print(f"WARN export unavailable: {e!r}", file=sys.stderr)
    if cards:
        return {w: (keys.get(w.lower(), w.lower()), cards.get(w.lower())) for w in names}

    m = {}
    try:
        d = T.api_get("/words?filter=due&limit=1000", timeout=60) or {}
        for it in d.get("items") or []:
            w = str(it.get("word") or "")
            if w:
                m[w.lower()] = w
    except Exception as e:
        print(f"WARN due list unavailable: {e!r}", file=sys.stderr)
    print("WARN 现有卡片不可得（export 失败）：按空卡打分，复习历史可能被清零", file=sys.stderr)
    return {w: (m.get(w.lower(), w.lower()), None) for w in names}


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


def plan_advance(words, result):
    """算好 lastLearnIndex 目标（像 App 完成学习任务那样推进；默认不启用）。

    目标是「本批新词的最大 index + 1」= App 语义里的「下一个新词的下标」，
    这样 App 的取新词窗口正好从下一批要打印的词开始（闭环）。
    缺少 index 时才退回「当前 + 新词数」（旧行为，可能与 App 窗口错位）。
    """
    ov = T.api_get("/overview", timeout=30) or {}
    cd = ov.get("currentDict") or {}
    if not cd.get("id"):
        result["verify_errors"].append("advance-index: /api/overview 没给 currentDict，跳过推进")
        return None
    length = int(cd.get("length") or 0)
    before = int(cd.get("lastLearnIndex") or 0)
    new_idx = [int(w["index"]) for w in words
               if str(w.get("src", "new")) == "new" and isinstance(w.get("index"), int) and w["index"] >= 0]
    by = sum(1 for w in words if str(w.get("src", "new")) == "new")
    if new_idx:
        mode = "index"
        raw = max(new_idx) + 1
    else:
        mode = "count"
        raw = before + by
    after = min(raw, length) if length else raw
    return {"dictKey": cd["id"], "book": cd.get("name") or cd.get("id"),
            "from": before, "to": after, "by": by, "length": length, "mode": mode,
            "new_index_max": max(new_idx) if new_idx else None,
            "complete": bool(length and after >= length - 1 and length != 1)}


def submit(ops, result):
    """提交 + 冲突重投一次（同实体被人改过 → 重取 revision 再投，仍冲突就上报）。"""
    st, resp = T.ops_submit(ops)
    result["http_status"] = st
    result["revision"] = resp.get("revision")
    applied = list(resp.get("applied") or [])
    conflicts = list(resp.get("conflicts") or [])
    if resp.get("raw") is not None:
        result["verify_errors"].append(f"ops 响应无法解析 http={st} body={resp['raw'][:200]}")

    if conflicts:
        rev = T.current_revision()
        by_id = {o["opId"]: o for o in ops}
        retry = []
        for c in conflicts:
            o = by_id.get(c.get("opId"))
            if not o:
                continue
            n = dict(o)
            n["opId"] = T.new_op_id()          # 重投换个新 opId：它并没被应用，新 id 避免被去重误判
            n["baseRevision"] = rev
            retry.append(n)
        result["retried"] = len(retry)
        if retry:
            st2, resp2 = T.ops_submit(retry)
            result["http_status"] = st2
            result["revision"] = resp2.get("revision") or result["revision"]
            applied += list(resp2.get("applied") or [])
            conflicts = list(resp2.get("conflicts") or [])

    result["ops_applied"] = len(applied)
    result["conflicts"] = [{"opId": c.get("opId"), "kind": c.get("kind"), "reason": c.get("reason")}
                           for c in conflicts]
    for c in conflicts:
        result["verify_errors"].append(
            f"op 未应用（{c.get('reason')}）kind={c.get('kind')} opId={c.get('opId')} —— 有别人改过同一实体，未覆盖")
    return applied


def verify_via_log(rev0, cards, key_for, result):
    """读 op 日志，比对服务端记录下来的卡片（一次请求）。"""
    d = T.api_get(f"/ops?since={rev0}", timeout=60) or {}
    if d.get("mode") != "ops":
        result["log_verify"] = f"skipped(mode={d.get('mode')})"
        return
    seen = {}
    for o in d.get("ops") or []:
        if o.get("kind") == "word.fsrs.set":
            p = o.get("payload") or {}
            w = str(p.get("word") or "").lower()
            if w:
                seen[w] = p.get("card") or {}
    result["log_verify"] = f"{len(seen)}/{len(cards)}"
    for w, card in cards.items():
        c = seen.get(key_for[w].lower())
        if not c:
            result["verify_errors"].append(f"{w}: op 日志里没有本次写入")
            continue
        if str(c.get("due")) != str(card.get("due")) or int(c.get("state", -1)) != int(card.get("state")):
            result["verify_errors"].append(
                f"{w}: op 日志卡片不一致 due={c.get('due')} state={c.get('state')}")


def verify_via_words(cards, result):
    """逐词回读 /api/words/{word}（实测 ~60ms/词），确认服务端当前卡片。"""
    for w, card in cards.items():
        try:
            d = T.api_get("/words/" + urllib.parse.quote(w), timeout=30) or {}
        except Exception as e:
            result["verify_errors"].append(f"{w}: 回读失败 {e!r}")
            continue
        f = d.get("fsrs") or {}
        if not f:
            result["verify_errors"].append(f"{w}: 服务端没有卡片（写入可能没生效）")
            continue
        if str(f.get("due"))[:19] != str(card.get("due"))[:19]:
            result["verify_errors"].append(f"{w}: due 不一致 服务端={f.get('due')} 期望={card.get('due')}")
        if int(f.get("state", -1)) != int(card.get("state")):
            result["verify_errors"].append(f"{w}: state 不一致 服务端={f.get('state')} 期望={card.get('state')}")


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
    result = {"ok": False, "mode": "ops", "rating": a.rating, "words": names, "rated": [],
              "revision": None, "ops_applied": 0, "conflicts": [], "retried": 0,
              "cards_reused": [], "cards_new": [],
              "next_due": {}, "verify_errors": [], "advanced": None,
              "skipped": False, "dry_run": a.dry_run, "fsrs_version": None,
              "params_source": None, "log_verify": None}

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
    ec = existing_cards(names)
    key_for = {w: v[0] for w, v in ec.items()}
    card_in = {w: v[1] for w, v in ec.items()}
    result["card_keys"] = key_for
    result["cards_reused"] = sorted(w for w, c in card_in.items() if c)
    result["cards_new"] = sorted(w for w, c in card_in.items() if not c)

    now = datetime.datetime.now(datetime.timezone.utc)
    now_iso = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    # 已有卡 → 交给 ts-fsrs 续排；没有才当新卡（与 App 的 fsrsData[word] ?? createEmptyCard() 一致）
    out = next_cards([[key_for[w], card_in[w]] for w in names], params, a.rating, now_iso)
    result["fsrs_version"] = out.get("version")

    cards = {}
    for w in names:
        card = out["results"][key_for[w]]
        if not card:
            result["verify_errors"].append(f"{w}: 打分器没给出卡片")
            continue
        cards[w] = card
        result["rated"].append(w)
        result["next_due"][w] = card["due"]

    # 只要有一张卡算不出来，就整批不写（宁可整天不记，也不写半截）
    if result["verify_errors"]:
        result["ok"] = False
        result["note"] = "打分阶段就出错了，未提交任何写入"
        print(json.dumps(result, ensure_ascii=False))
        return 1

    advance = plan_advance(words, result) if a.advance_index else None

    if a.dry_run:
        result["ok"] = not result["verify_errors"]
        result["note"] = f"dry-run：本会提交 {len(cards)} 个 word.fsrs.set" + \
                         (" + 1 个 dict.progress.set" if advance and advance["to"] > advance["from"] else "")
        result["advanced"] = advance
        dues = sorted(result["next_due"].values())
        result["next_due"] = {"min": dues[0] if dues else None, "max": dues[-1] if dues else None,
                              "per_word": result["next_due"]}
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["ok"] else 1

    # 写前取最新 revision 作基准；只有「同实体在本批开始前被别人改过」才会被判冲突
    # （服务端 commitOps.ts:223 的判定：baseRevision 落后 + 实体域相交）
    rev0 = T.current_revision()
    result["base_revision"] = rev0
    ops = [T.make_op("word.fsrs.set", {"word": key_for[w], "card": cards[w]}, rev0, client_ts=now_iso)
           for w in cards]
    do_advance = bool(advance and advance["to"] > advance["from"])
    if do_advance:
        ops.append(T.make_op("dict.progress.set",
                             {"dictKey": advance["dictKey"], "lastLearnIndex": advance["to"],
                              "complete": advance["complete"]},
                             rev0, client_ts=now_iso))
    elif advance:
        advance["skipped_reason"] = "目标未超过当前进度（服务端进度只增不回退），不提交进度 op"
    submit(ops, result)
    result["advanced"] = advance

    verify_via_log(rev0, cards, key_for, result)
    verify_via_words(cards, result)
    if do_advance:
        try:
            p = T.api_get("/dicts/" + urllib.parse.quote(advance["dictKey"]) + "/progress", timeout=30) or {}
            got = int(p.get("lastLearnIndex") or 0)
            if got < advance["to"]:
                result["verify_errors"].append(
                    f"lastLearnIndex 未推进: {got} < {advance['to']}")
        except Exception as e:
            result["verify_errors"].append(f"advance-index 回读失败 {e!r}")

    result["ok"] = not result["verify_errors"]
    with open(marker, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    dues = sorted(result["next_due"].values())
    result["next_due"] = {"min": dues[0] if dues else None, "max": dues[-1] if dues else None,
                          "per_word": result["next_due"]}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())