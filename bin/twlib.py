#!/usr/bin/env python3
"""TypeWords 日报 Agent 共享工具：API 访问、释义清洗、屈折匹配、typst 编译辅助。"""
import json
import os
import re
import subprocess
import urllib.parse
import urllib.request

API = os.environ.get("TW_API", "https://typewords.akinokuni.cn/api")
AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------- API ----------------

def api_token():
    """TypeWords 的 API_TOKEN：优先环境变量 TW_API_TOKEN，其次 config/api_token 文件。

    服务端没启用 API_TOKEN 时为空字符串（当前线上就是这样）；启用后除 /api/health 与
    /api/data/*、/api/ops* 之外的接口都需要 `Authorization: Bearer <token>`，缺失会返回 401。
    """
    tok = (os.environ.get("TW_API_TOKEN") or "").strip()
    if tok:
        return tok
    try:
        with open(os.path.join(AGENT_DIR, "config", "api_token"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def headers(extra=None):
    h = {"Content-Type": "application/json"}
    tok = api_token()
    if tok:
        h["Authorization"] = "Bearer " + tok
    h.update(extra or {})
    return h


def api_get(path, timeout=30):
    req = urllib.request.Request(API + path, headers=headers(), method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def api_post(path, payload, timeout=30):
    req = urllib.request.Request(
        API + path,
        data=json.dumps(payload).encode(),
        headers=headers(),
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode()


def api_put(path, payload, timeout=30):
    req = urllib.request.Request(
        API + path,
        data=json.dumps(payload).encode(),
        headers=headers(),
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode()


def get_store(key, timeout=60):
    """读回 TypeWords 服务端存储的某个 store（'dict'/'setting'/'practice_word'）。

    返回 (envelope, value)：envelope = {"val":..., "version":..., "updated_at":...}

    注意：dict 文档实测 ~1.4MB / ~2s，日常只读 setting 就够了；需要全量词表时优先想清楚
    是不是真的要拉这 1.4MB（新词算法确实需要，见 fetch_new.py）。
    """
    raw = api_get(f"/data/{key}", timeout=timeout) or {}
    envelope = json.loads(raw.get("value") or "{}")
    return envelope, envelope.get("val")


def put_store(key, envelope, timeout=60):
    """⚠️ 危险：整文档覆盖写入（value 字段是 JSON 字符串，与 App 的 dataSync 一致）。

    2026-09 起服务端有了 ops 引擎，日常修改走 ops_submit() / make_op()，
    这个函数只保留给「显式导入/恢复」用：它会替换整份数据、广播一次「文档替换」，
    让所有在线客户端（含用户正在练习的浏览器）重载，且与用户改动互相覆盖。
    """
    return api_put(f"/data/{key}", {"value": json.dumps(envelope, ensure_ascii=False)}, timeout=timeout)


def current_revision(timeout=30):
    """只取当前全局 revision（since 超过当前值不会返回日志，最省流量）。"""
    d = api_get("/ops?since=999999999", timeout=timeout) or {}
    return int(d.get("revision") or 0)


def ops_submit(ops, scope="dict", timeout=120):
    """提交一批 op → (http_status, 响应 dict)。返回 {"revision","applied","conflicts"}。"""
    st, body = api_post("/ops", {"scope": scope, "ops": ops}, timeout=timeout)
    try:
        parsed = json.loads(body or "{}")
    except Exception:
        parsed = {"raw": (body or "")[:500]}
    return st, parsed


def new_op_id(tag="agent"):
    """全局唯一 opId（服务端按它幂等去重：同 opId 重投不会产生第二次变更）。"""
    import secrets
    import time
    return f"{tag}-{int(time.time() * 1000)}-{secrets.token_hex(4)}"


def make_op(kind, payload, base_revision, op_id=None, origin="agent", client_ts=None):
    """构造一个提交单元。

    base_revision 必须是「提交方看到的最后修订号」：落后且同一实体已被别人改过 → ENTITY_MODIFIED
    （实测连 no-op 都会被拦），所以写前先 current_revision()。
    """
    op = {"opId": op_id or new_op_id(), "kind": kind, "payload": payload,
          "baseRevision": int(base_revision), "origin": origin}
    if client_ts:
        op["clientTs"] = client_ts
    return op


def load_workflow(path=None):
    import yaml
    p = path or os.path.join(AGENT_DIR, "config", "workflow.yaml")
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------- 文本清洗 / typst 转义 ----------------

ESC = {
    "\\": "\\\\", "#": "\\#", "$": "\\$", "[": "\\[", "]": "\\]",
    "*": "\\*", "_": "\\_", "@": "\\@", "<": "\\<", ">": "\\>",
    "~": "\\~", "`": "\\`",
}


def esc_content(s):
    """typst content 模式转义（用于 [...] 参数）。"""
    return "".join(ESC.get(c, c) for c in (s or ""))


def esc_str(s):
    """typst 字符串模式转义（用于 #ruby(\"...\", \"...\") 里）。输入须已是 content 转义文本。"""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def strip_paren(s):
    s = re.sub(r"[（(][^）)]*[）)]", "", s or "")
    return s.strip(" 　。，；")


def is_name_entry(t):
    cn = t.get("cn", "")
    return "人名" in cn or cn.strip().startswith("【名】")


def clean_cn(t):
    cn = (t.get("cn") or "").strip()
    cn = re.sub(r"^[-–—\s]+", "", cn)
    cn = re.sub(r"<[^>]*>", "", cn)
    return cn.strip()


POS_RE = re.compile(
    r"^(?:n|v|vt|vi|v\s*&\s*vi|adj|adv|prep|conj|pron|num|int|aux|art)\.\s*", re.I
)


def norm_entries(d):
    """[(pos, cn)] —— 去掉人名义项与「时态」噪声，词性前缀归一。"""
    out = []
    for t in d.get("trans", []):
        if is_name_entry(t):
            continue
        cn = clean_cn(t)
        if not cn or "时态" in cn:
            continue
        pos = (t.get("pos") or "").strip()
        m = POS_RE.match(cn)
        if m:
            if not pos:
                pos = m.group(0).strip()
            cn = cn[m.end():].strip()
        if cn:
            out.append((pos, cn))
    return out


def senses(d, n_chunks=1, max_len=22):
    out = []
    for pos, cn in norm_entries(d)[:2]:
        chunks = [c.strip() for c in re.split(r"[；;]", cn) if c.strip()]
        if not chunks:
            continue
        txt = "；".join(chunks[:n_chunks])
        if len(txt) > max_len:
            txt = txt[:max_len].rstrip("；，、") + "…"
        out.append(f"{pos} {txt}".strip() if out else txt)
    return " ／ ".join(out)


def phonetic(d):
    p = (d.get("phonetic0") or d.get("phonetic1") or "").strip()
    return f"/{p}/" if p else ""


def part(d):
    for p, _cn in norm_entries(d):
        if p:
            return p
    return ""


def short_gloss(d, max_len=6):
    """从释义里取一个 2–6 字的中文短义，用作 ruby 兜底标注。"""
    for _pos, cn in norm_entries(d):
        c = re.split(r"[；;，,（(／/]", cn)[0]
        c = re.sub(r"^\[[^\]]*\]\s*", "", c).strip()
        c = c.strip(" 　。，、")
        if c:
            return c[:max_len]
    return ""


# ---------------- 屈折形式匹配 ----------------

IRREG = {
    "slide": ["slid", "sliding"],
    "spit": ["spitting", "spat"],
    "split": ["splitting"],
    "slip": ["slipped", "slipping"],
    "spill": ["spilled", "spilt", "spilling"],
    "burst": ["bursting"],
    "breed": ["bred", "breeding"],
    "transmit": ["transmitted", "transmitting"],
    "dispose": ["disposed", "disposing"],
    "consume": ["consumed", "consuming"],
}


def inflect_forms(w):
    w = w.lower()
    forms = {w}
    if w.endswith("y") and len(w) > 2 and w[-2] not in "aeiou":
        forms |= {w[:-1] + "ies", w[:-1] + "ied"}
    if re.search(r"[^aeiou][aeiou][^aeiouwxy]$", w):
        forms |= {w + w[-1] + "ed", w + w[-1] + "ing"}
    forms |= {w + "s", w + "es", w + "ed", w + "d", w + "ing"}
    if w.endswith("e"):
        forms |= {w[:-1] + "ing"}
    forms |= set(IRREG.get(w, []))
    return sorted(forms, key=len, reverse=True)


def inflect_regex(w):
    alts = "|".join(re.escape(f) for f in inflect_forms(w))
    return re.compile(r"\b(?:" + alts + r")\b", re.I)


def mark_paragraph(text, targets, glosses, used=None):
    """先把文本做 typst 转义，再把每个目标词的首个出现包成 #ruby(词, 中文)。

    返回 (带标注的文本, 已用词集合)。未在 targets 里的 ruby 不会产生。
    """
    body = esc_content(text)
    used = used if used is not None else set()
    out, pos = "", 0
    while True:
        best = None
        for w in targets:
            if w in used:
                continue
            m = inflect_regex(w).search(body[pos:])
            if m and (best is None or m.start() < best[0]):
                best = (m.start(), m, w)
        if best is None:
            break
        off, m, w = best
        start, end = pos + off, pos + off + (m.end() - m.start())
        surface = body[start:end]
        out += body[pos:start] + '#ruby("%s", "%s")' % (
            esc_str(surface), esc_str(glosses.get(w, ""))
        )
        pos = end
        used.add(w)
    out += body[pos:]
    return out, used


# ---------------- typst 编译 ----------------

def pdf_pages(pdf_path):
    try:
        r = subprocess.run(["pdfinfo", pdf_path], capture_output=True, text=True, timeout=30)
        m = re.search(r"^Pages:\s*(\d+)", r.stdout, re.M)
        return int(m.group(1)) if m else None
    except Exception:
        return None


def pdf_fonts(pdf_path):
    try:
        r = subprocess.run(["pdffonts", pdf_path], capture_output=True, text=True, timeout=30)
        names = []
        for line in r.stdout.splitlines()[2:]:
            parts = line.split()
            if len(parts) >= 4 and parts[0]:
                names.append(parts[0])
        return names
    except Exception:
        return []