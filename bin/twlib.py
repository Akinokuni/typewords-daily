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

def api_get(path, timeout=30):
    with urllib.request.urlopen(API + path, timeout=timeout) as r:
        return json.load(r)


def api_post(path, payload, timeout=30):
    req = urllib.request.Request(
        API + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode()


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