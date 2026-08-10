#!/usr/bin/env bash
# Hindsight 独立环境安装（vectorize-io/hindsight，MIT）
# 走 hindsight-embed 本地守护进程 + 内嵌 PostgreSQL(pg0)，不需要 Docker。
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HERE/.venv"

echo "=== Hindsight 环境安装 ==="
START=$(date +%s)
"${PYTHON:-python3}" -m venv "$VENV"
# shellcheck disable=SC1091
source "$VENV/bin/activate"
python -m pip install --quiet --upgrade pip
pip install hindsight-embed hindsight-client openai tiktoken
END=$(date +%s)

echo
echo "=== 安装完成 ==="
echo "耗时: $((END-START)) 秒"
echo "依赖体积: $(du -sh "$VENV" | cut -f1)"

cat <<TXT

注意：hindsight-embed 首次运行会下载依赖并加载 ML 模型，官方说明需 1-3 分钟。
      守护进程默认监听 localhost:8888，闲置 5 分钟后自动退出。
      跑测试前先执行一次任意命令把守护进程预热起来，否则第一题的延迟会包含
      模型加载时间，污染 P50/P95 数据。

      预热： hindsight-embed --help  然后随便 store 一条再删掉

=== 下一步 ===
  source $VENV/bin/activate
  cd $(cd "$HERE/../.." && pwd)
  python -m harness.runner --framework hindsight --limit 3
  python -m harness.runner --framework hindsight
TXT
