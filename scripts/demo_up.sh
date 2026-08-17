#!/usr/bin/env bash
# 一键启动 Demo 环境(Phase 3 收口):基础设施 + API + Worker + 模拟录音机。
#
#   scripts/demo_up.sh          启动全部并等待就绪
#   scripts/demo_up.sh --down   停止并清理
#
# 启动后:
#   API        http://127.0.0.1:8000        (/docs 可看接口)
#   模拟设备    http://127.0.0.1:9100        (控制面;文件面 9101)
#   Mailpit    http://127.0.0.1:8025        (收验证码邮件)
#   MinIO 控制台 http://127.0.0.1:9001       (ysnote / ysnote-dev-secret)
#
# App 侧:cd mobile && flutter run \
#   --dart-define=YS_API_URL=http://127.0.0.1:8000 \
#   --dart-define=YS_DEVICE_URL=http://127.0.0.1:9100
# (Android 模拟器把 127.0.0.1 换成 10.0.2.2)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$REPO/.demo-run"
cd "$REPO"

export YS_ENV=dev
export YS_STORAGE_BACKEND=postgres
export YS_OBJECT_BACKEND=s3
export YS_QUEUE_BACKEND=redis
export YS_CODE_STORE_BACKEND=redis
export YS_AI_PROVIDER=mock
export YS_DATABASE_URL="${YS_DATABASE_URL:-postgresql+psycopg://ys:ys@127.0.0.1:5432/ysnote}"
export YS_REDIS_URL="${YS_REDIS_URL:-redis://127.0.0.1:6379/0}"
export YS_S3_ENDPOINT="${YS_S3_ENDPOINT:-http://127.0.0.1:9000}"
export YS_S3_ACCESS_KEY="${YS_S3_ACCESS_KEY:-ysnote}"
export YS_S3_SECRET_KEY="${YS_S3_SECRET_KEY:-ysnote-dev-secret}"
export YS_SMTP_HOST="${YS_SMTP_HOST:-127.0.0.1}"
export YS_SMTP_PORT="${YS_SMTP_PORT:-1025}"
export YS_SMTP_FROM="${YS_SMTP_FROM:-noreply@ysnote.dev}"
export YS_SMTP_STARTTLS=false

stop_all() {
  echo "==> 停止 Demo 进程"
  for name in api worker device; do
    pid_file="$RUN_DIR/$name.pid"
    [ -f "$pid_file" ] && kill "$(cat "$pid_file")" 2>/dev/null || true
    rm -f "$pid_file"
  done
  docker compose down -v 2>/dev/null || true
  echo "已停止。"
}

if [ "${1:-}" = "--down" ]; then
  stop_all
  exit 0
fi

mkdir -p "$RUN_DIR"

echo "==> 1/4 启动基础设施(PostgreSQL / Redis / MinIO / Mailpit)"
docker compose up -d --wait

echo "==> 2/4 执行数据库迁移"
(cd server && python3 -m alembic upgrade head)

echo "==> 3/4 启动 API 与 Worker"
(cd server && nohup python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 \
   > "$RUN_DIR/api.log" 2>&1 & echo $! > "$RUN_DIR/api.pid")
(cd server && nohup python3 -m app.workers.runner --consumer demo \
   > "$RUN_DIR/worker.log" 2>&1 & echo $! > "$RUN_DIR/worker.pid")

echo "==> 4/4 启动模拟录音机(预置 3 个录音文件)"
nohup python3 -m mock_device.server --files 3 \
  > "$RUN_DIR/device.log" 2>&1 & echo $! > "$RUN_DIR/device.pid"

echo -n "==> 等待 API 就绪"
for _ in $(seq 1 60); do
  if curl -sf http://127.0.0.1:8000/health/ready >/dev/null 2>&1; then
    echo " ok"; break
  fi
  echo -n "."; sleep 1
done

curl -sf http://127.0.0.1:8000/health/ready >/dev/null || {
  echo; echo "API 未就绪,日志:"; tail -30 "$RUN_DIR/api.log"; exit 1; }

cat <<EOF

Demo 环境已就绪:
  API          http://127.0.0.1:8000/docs
  模拟录音机     http://127.0.0.1:9100  (文件面 9101)
  Mailpit      http://127.0.0.1:8025
  MinIO 控制台   http://127.0.0.1:9001

启动 App:
  cd mobile && flutter run \\
    --dart-define=YS_API_URL=http://127.0.0.1:8000 \\
    --dart-define=YS_DEVICE_URL=http://127.0.0.1:9100

日志:$RUN_DIR/{api,worker,device}.log
停止:scripts/demo_up.sh --down
EOF
