"""真实进程启动验收(审查 Phase 2.1 第一优先级):

不再手工调用 state.configure_db —— 设置环境变量 → 启动 uvicorn 子进程
→ /health/ready 全绿 → 经 HTTP 完成黄金主链 → 杀进程重启 → 数据仍在。
"""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import hashlib
import pytest
import requests

pytestmark = pytest.mark.integration

SERVER_DIR = Path(__file__).resolve().parent.parent.parent
PORT = 8123
BASE = f"http://127.0.0.1:{PORT}"
FREE = 120


def _spawn(extra_env: dict | None = None) -> subprocess.Popen:
    from tests.integration.conftest import PG_URL, REDIS_URL, S3_ENDPOINT, S3_KEY, S3_SECRET

    env = {
        **os.environ,
        "YS_ENV": "dev",
        "YS_STORAGE_BACKEND": "postgres",
        "YS_OBJECT_BACKEND": "s3",
        "YS_QUEUE_BACKEND": "redis",
        "YS_CODE_STORE_BACKEND": "redis",
        "YS_DATABASE_URL": PG_URL,
        "YS_REDIS_URL": REDIS_URL,
        "YS_S3_ENDPOINT": S3_ENDPOINT,
        "YS_S3_ACCESS_KEY": S3_KEY,
        "YS_S3_SECRET_KEY": S3_SECRET,
        **(extra_env or {}),
    }
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PORT),
         "--host", "127.0.0.1"],
        cwd=SERVER_DIR, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )


def _wait_ready(proc: subprocess.Popen, timeout_s: int = 30) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if proc.poll() is not None:
            out = proc.stdout.read().decode(errors="replace")[-2000:]
            raise AssertionError(f"uvicorn exited early:\n{out}")
        try:
            r = requests.get(f"{BASE}/health/ready", timeout=2)
            if r.status_code == 200 and r.json()["ready"]:
                return
        except requests.ConnectionError:
            pass
        time.sleep(0.5)
    raise AssertionError("readiness timeout")


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=10)


def test_uvicorn_boots_wired_and_survives_restart(db, redis_db):
    proc = _spawn()
    try:
        _wait_ready(proc)
        ready = requests.get(f"{BASE}/health/ready", timeout=5).json()
        assert ready["checks"]["postgres"] == "ok"
        assert ready["checks"]["s3"] == "ok"
        assert ready["checks"]["redis"] == "ok"
        assert requests.get(f"{BASE}/health/live", timeout=5).json()["status"] == "alive"

        # 黄金主链(纯 HTTP,无任何测试侧接线)
        email = "boot@test.com"
        code = requests.post(f"{BASE}/v1/auth/email/code",
                             json={"email": email}, timeout=5).json()["dev_code"]
        tokens = requests.post(f"{BASE}/v1/auth/email/verify",
                               json={"email": email, "code": code}, timeout=5).json()
        h = {"Authorization": f"Bearer {tokens['access_token']}"}

        data = b"boot-audio" * 100
        rec = requests.post(f"{BASE}/v1/recordings", json={
            "title": "boot", "duration_ms": 3 * 60000,
            "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data),
        }, headers=h, timeout=5).json()
        assert rec["deduplicated"] is False

        job = requests.post(f"{BASE}/v1/jobs", json={"recording_id": rec["id"]},
                            headers=h, timeout=5).json()
        assert job["status"] == "waiting" and job["minutes_charged"] == 3
        balance = requests.get(f"{BASE}/v1/entitlements/me",
                               headers=h, timeout=5).json()
        assert balance["total_minutes"] == FREE - 3

        # 杀进程 → 重启 → 数据仍在(验证码在 Redis,任务与录音在 PG)
        _stop(proc)
        proc = _spawn()
        _wait_ready(proc)
        again = requests.get(f"{BASE}/v1/recordings/{rec['id']}",
                             headers=h, timeout=5)
        assert again.status_code == 200
        assert requests.get(f"{BASE}/v1/jobs/{job['id']}", headers=h,
                            timeout=5).json()["status"] == "waiting"
        assert requests.get(f"{BASE}/v1/entitlements/me", headers=h,
                            timeout=5).json()["total_minutes"] == FREE - 3
    finally:
        _stop(proc)


def test_uvicorn_refuses_to_start_when_postgres_down(db):
    """非 memory 后端连接失败 → 进程拒绝启动(而不是静默退回内存模式)。"""
    proc = _spawn({"YS_DATABASE_URL":
                   "postgresql+psycopg://ys:ys@127.0.0.1:59999/nope"})
    try:
        deadline = time.time() + 20
        while time.time() < deadline and proc.poll() is None:
            time.sleep(0.5)
        assert proc.poll() is not None, "PostgreSQL 不可达时必须拒绝启动"
        out = proc.stdout.read().decode(errors="replace")
        assert "PostgreSQL unreachable" in out
    finally:
        _stop(proc)
