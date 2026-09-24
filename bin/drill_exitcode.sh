#!/usr/bin/env bash
# 退出码契约演练：失败必须 rc=1 且只吐 ≤10 行摘要（否则 cron 会把失败当成功静默掉）
cd /root/typewords-agent || exit 99
out=$(TW_API="http://127.0.0.1:9/api" ./bin/run.sh --force --date drill-exit 2>&1); rc=$?
echo "退出码 = $rc （契约应为 1）"
echo "摘要行数 = $(printf '%s\n' "$out" | grep -c .) （契约应 <=10）"
echo "---- 摘要原文 ----"
printf '%s\n' "$out"
echo "---- 升级标志 ----"
.venv/bin/python -c "import json,os;p='state/needs_attention.json';print(json.load(open(p)) if os.path.exists(p) else '(无)')"