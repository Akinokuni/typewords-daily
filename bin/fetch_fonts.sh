#!/usr/bin/env bash
# 把讲义所需字体就位到 assets/fonts/（字体体积大且为第三方文件，不入库）。
# 优先从本机已装的 margin-ruby-reader skill 复制；失败则提示手工准备。
set -uo pipefail
A="$(cd "$(dirname "$0")/.." && pwd)"
DST="$A/assets/fonts"
SRC="${TW_FONTS_SRC:-/root/.hermes/skills/margin-ruby-reader/assets/fonts}"
NEED=(LibertinusSerif-Regular.otf LibertinusSerif-Bold.otf \
      LibertinusSerif-Italic.otf LibertinusSerif-BoldItalic.otf \
      SourceHanSansSC-Regular.otf)

mkdir -p "$DST"
missing=0
for f in "${NEED[@]}"; do
  if [ -s "$DST/$f" ]; then
    echo "已有 $f"
    continue
  fi
  if [ -s "$SRC/$f" ]; then
    cp "$SRC/$f" "$DST/$f" && echo "复制 $f"
  else
    echo "缺少 $f（来源 $SRC 也没有）" >&2
    missing=$((missing + 1))
  fi
done

if [ "$missing" -gt 0 ]; then
  echo "有 $missing 个字体缺失：请把 Libertinus Serif 与 Source Han Sans SC(Regular) 的 .otf 放进 assets/fonts/" >&2
  exit 1
fi
echo "字体就位：$(ls "$DST" | wc -l) 个文件，$(du -sh "$DST" | cut -f1)"