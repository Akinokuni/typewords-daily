#!/usr/bin/env bash
# GitHub 认证 + 首次推送（无需安装 gh CLI）。
# 走 OAuth 设备流：终端会打印一个 8 位代码，你在 https://github.com/login/device 输入即可。
# token 只会写进 ~/.git-credentials（600），不打印、不入库。
set -uo pipefail
A="$(cd "$(dirname "$0")/.." && pwd)"
cd "$A" || exit 1

REPO="${TW_REPO:-Akinokuni/typewords-daily}"
BRANCH="${TW_BRANCH:-main}"
CLIENT_ID="${GH_CLIENT_ID:-178c6fc778ccc68e1d6a}"   # gh CLI 的公开 client_id

resp=$(curl -s -X POST -H "Accept: application/json" \
  -d "client_id=${CLIENT_ID}&scope=repo,read:org,gist" \
  https://github.com/login/device/code) || { echo "DEVICE_CODE_REQUEST_FAILED"; exit 1; }

device_code=$(printf '%s' "$resp" | sed 's/.*"device_code":"\([^"]*\)".*/\1/')
user_code=$(printf '%s' "$resp" | sed 's/.*"user_code":"\([^"]*\)".*/\1/')
interval=$(printf '%s' "$resp" | sed 's/.*"interval":\([0-9]*\).*/\1/'); interval=${interval:-5}

if [ -z "$device_code" ] || [ "$device_code" = "$resp" ]; then
  echo "NO_DEVICE_CODE: $resp" >&2
  exit 1
fi
echo "USER_CODE=$user_code"
echo "→ 打开 https://github.com/login/device ，输入代码 $user_code"
echo "WAITING_FOR_AUTH..."

while true; do
  sleep "$interval"
  poll=$(curl -s -X POST -H "Accept: application/json" \
    -d "client_id=${CLIENT_ID}&device_code=${device_code}&grant_type=urn:ietf:params:oauth:grant-type:device_code" \
    https://github.com/login/oauth/access_token)
  case "$poll" in
    *access_token*)
      token=$(printf '%s' "$poll" | tr -d '\n' | sed -n 's/.*"access_token":"\([^"]*\)".*/\1/p')
      if [ -z "$token" ]; then echo "TOKEN_PARSE_FAILED" >&2; exit 1; fi
      # 注意：/user 返回的是**美化 JSON**（多行），必须先去换行再抽取，
      # 否则 login 会变成整段 JSON，凭据文件被写坏 → push 报 "could not read Username"。
      login=$(curl -s -H "Authorization: token $token" https://api.github.com/user \
              | tr -d '\n' | sed -n 's/.*"login": *"\([^"]*\)".*/\1/p')
      login=${login:-x-access-token}
      umask 077
      printf 'https://%s:%s@github.com\n' "$login" "$token" > "$HOME/.git-credentials"
      chmod 600 "$HOME/.git-credentials"
      git config --global credential.helper store
      # 自检：凭据必须是单行标准格式，否则 push 会报 "could not read Username"
      if [ "$(wc -l < "$HOME/.git-credentials")" != "1" ] || ! grep -qE '^https://[^:]+:[^@]+@github\.com$' "$HOME/.git-credentials"; then
        echo "CREDENTIALS_FORMAT_INVALID" >&2; exit 1
      fi
      echo "AUTH_OK login=$login"

      if git remote | grep -qx origin; then
        git remote set-url origin "https://github.com/${REPO}.git"
      else
        git remote add origin "https://github.com/${REPO}.git"
      fi
      echo "REMOTE=$(git remote get-url origin)"
      echo "PUSHING..."
      if git push -u origin "$BRANCH"; then
        echo "PUSH_OK"
        exit 0
      fi
      echo "PUSH_FAILED rc=$?"
      exit 1 ;;
    *authorization_pending*) ;;                      # 继续轮询
    *slow_down*) interval=$((interval + 5)) ;;        # 按 GitHub 要求退避
    *expired_token*) echo "CODE_EXPIRED — 请重新运行本脚本"; exit 1 ;;
    *access_denied*) echo "USER_DENIED"; exit 1 ;;
    *) echo "UNEXPECTED: $poll" >&2; exit 1 ;;
  esac
done