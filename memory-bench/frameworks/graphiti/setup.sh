#!/usr/bin/env bash
# Graphiti 独立环境安装（宪法第 3 条：每框架独立 venv，依赖互不污染）
#
# 本地优先：图库用嵌入式 falkordblite（无需 Docker），LLM 与 embedding 走本机 Ollama。
# 运行前请确认 Ollama 已起，且已 pull 好统一模型。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HERE/.venv"
LLM_MODEL="${BENCH_LLM_MODEL:-qwen2.5:14b-instruct}"
EMBED_MODEL="${BENCH_EMBED_MODEL:-bge-m3}"

echo "=== Graphiti 环境安装 ==="
START=$(date +%s)

# falkordblite 要求 Python >= 3.12
PYBIN="${PYTHON:-python3}"
PYVER=$("$PYBIN" -c 'import sys;print("%d.%d"%sys.version_info[:2])')
if [ "$(printf '%s\n3.12\n' "$PYVER" | sort -V | head -1)" != "3.12" ]; then
  echo "错误：falkordblite 需要 Python >= 3.12，当前是 $PYVER。" >&2
  echo "      指定其它解释器：PYTHON=/path/to/python3.12 bash setup.sh" >&2
  exit 1
fi

"$PYBIN" -m venv "$VENV"
# shellcheck disable=SC1091
source "$VENV/bin/activate"
python -m pip install --quiet --upgrade pip

# falkordblite：FalkorDB 的嵌入式打包版，不需要 Docker。
# 刻意不用 kuzu extra——graphiti 的 pyproject.toml 已注明上游 Kuzu 停维护、该 extra 将移除。
pip install "graphiti-core[falkordblite]" openai tiktoken

END=$(date +%s)
echo
echo "=== 安装完成 ==="
echo "耗时: $((END-START)) 秒"
echo "依赖体积: $(du -sh "$VENV" | cut -f1)"
echo "graphiti-core 版本: $(pip show graphiti-core 2>/dev/null | awk '/^Version/{print $2}')"

echo
echo "=== 检查 Ollama ==="
if curl -sf "${OLLAMA_BASE_URL:-http://localhost:11434}/api/tags" >/dev/null 2>&1; then
  echo "Ollama 在线。请确认已 pull 统一模型："
  echo "  ollama pull $LLM_MODEL"
  echo "  ollama pull $EMBED_MODEL"
else
  echo "警告：连不上 Ollama（${OLLAMA_BASE_URL:-http://localhost:11434}）。" >&2
  echo "      先启动 ollama serve，再 pull 上面两个模型。" >&2
fi

cat <<EOF

=== 下一步 ===
  source $VENV/bin/activate
  cd $(cd "$HERE/../.." && pwd)
  python -m harness.runner --framework graphiti --limit 3   # 先冒烟
  python -m harness.runner --framework graphiti             # 跑满 60 题
EOF
