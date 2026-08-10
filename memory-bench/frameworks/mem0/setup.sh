#!/usr/bin/env bash
# Mem0 独立环境安装（宪法第 3 条）
# 全本地：Ollama 出 LLM 与 embedding，Qdrant 走本地文件模式，不出网。
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HERE/.venv"
LLM_MODEL="${BENCH_LLM_MODEL:-qwen2.5:14b-instruct}"
EMBED_MODEL="${BENCH_EMBED_MODEL:-bge-m3}"

echo "=== Mem0 环境安装 ==="
START=$(date +%s)
"${PYTHON:-python3}" -m venv "$VENV"
# shellcheck disable=SC1091
source "$VENV/bin/activate"
python -m pip install --quiet --upgrade pip
pip install mem0ai qdrant-client openai tiktoken
END=$(date +%s)

echo
echo "=== 安装完成 ==="
echo "耗时: $((END-START)) 秒"
echo "依赖体积: $(du -sh "$VENV" | cut -f1)"
echo "mem0ai 版本: $(pip show mem0ai 2>/dev/null | awk '/^Version/{print $2}')"

echo
echo "=== 检查 Ollama ==="
if curl -sf "${OLLAMA_HOST:-http://localhost:11434}/api/tags" >/dev/null 2>&1; then
  echo "Ollama 在线。请确认已 pull： ollama pull $LLM_MODEL && ollama pull $EMBED_MODEL"
else
  echo "警告：连不上 Ollama（${OLLAMA_HOST:-http://localhost:11434}）。" >&2
fi

cat <<TXT

注意：bge-m3 是 1024 维。换 embedding 模型时必须同步设置
      BENCH_EMBED_DIMS，否则 Qdrant 集合维度对不上会报错。

=== 下一步 ===
  source $VENV/bin/activate
  cd $(cd "$HERE/../.." && pwd)
  python -m harness.runner --framework mem0 --limit 3
  python -m harness.runner --framework mem0
TXT
