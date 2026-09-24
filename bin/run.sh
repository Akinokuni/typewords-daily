#!/usr/bin/env bash
# TypeWords 日报 Agent 唯一入口。
#   成功 / 无到期词 → stdout 为空（定时任务不会发任何消息）
#   失败            → 输出 ≤10 行摘要（定时任务会把摘要直接发到对话）
# 用法: run.sh [--dry-run] [--force] [--date YYYY-MM-DD]
set -uo pipefail
A="$(cd "$(dirname "$0")/.." && pwd)"
cd "$A" || exit 1
# 环境：config/env.sh 提供 PATH（node v22 必须优先于 /usr/bin/node v20，
# 否则 pi 会因 fs.globSync 缺失直接崩）与可选的 API key 回退。
# 大模型凭据实际由 pi 自己读 ~/.pi/agent/auth.json（provider 定义见 ~/.pi/agent/models.json）。
if [ -f "$A/config/env.sh" ]; then
  . "$A/config/env.sh"
else
  export PATH="/root/.local/bin:/usr/local/bin:/usr/bin:/bin"
fi
mkdir -p "$A/state" "$A/logs" "$A/out"

exec 9>"$A/state/lock"
if ! flock -n 9; then
  # 上一次运行还没结束：静默退出，让本次定时触发空转过去
  exit 0
fi

"$A/.venv/bin/python" "$A/bin/run.py" "$@"
exit $?