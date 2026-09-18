#!/bin/sh
# CodeBee 启动脚本（macOS/Linux 版，对应 start.bat）
cd "$(dirname "$0")" || exit 1
PY="$(command -v python3 || command -v python)"
if [ -z "$PY" ]; then
  echo "[错误] 未找到 python3，请先安装 Python 3.8+（brew install python3）"
  exit 1
fi
exec "$PY" app/main.py "$@"
