#!/usr/bin/env bash
# pi 自检入口：用当前 run 目录里的 article.json 组版并编译 PDF。
# 用法: ./bin/selfcheck.sh state/runs/<DATE>
set -uo pipefail
A="$(cd "$(dirname "$0")/.." && pwd)"
exec "$A/.venv/bin/python" "$A/bin/write_article.py" --run-dir "$1"