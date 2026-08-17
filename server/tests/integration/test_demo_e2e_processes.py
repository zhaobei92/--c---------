"""三进程真实联调(Phase 3 收口):uvicorn + worker + mock_device 同时运行。

与 test_demo_golden_flow 的区别:那里在测试进程内直接调用服务对象;
这里三个程序都是**独立操作系统进程**,全程只经 HTTP 交互 —— 与真实部署、
与 App 在手机上的行为一致。覆盖审查要求的退出标准:
  * 无真机走完全过程(设备文件 → 下载 → 上传 → 任务 → 转写摘要);
  * API 与 Worker 分别重启后流程可继续;
  * 摘要结论可跳转到原文时间戳(证据 segment_id 可定位)。
"""

from __future__ import annotations

import hashlib
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parent.parent.parent   # server/
REPO = ROOT.parent
API_PORT = 8131
DEVICE_CONTROL_PORT = 9131
DEVICE_FILE_PORT = 9132
API = f"http://127.0.0.1:{API_PORT}"
DEVICE = f"http://127.0.0.1:{DEVICE_CONTROL_PORT}"
FREE_MINUTES = 120


def _env() -> dict:
    from tests.integration.conftest import PG_URL, REDIS_URL, S3_ENDPOINT, S3_KEY, S3_SECRET

    return {
        **os.environ,
        "YS_ENV": "dev",
        "YS_STORAGE_BACKEND": "postgres",
        "YS_OBJECT_BACKEND": "s3",
        "YS_QUEUE_BACKEND": "redis",
        "YS_CODE_STORE_BACKEND": "redis",
        "YS_AI_PROVIDER": "mock",
        "YS_DATABASE_URL": PG_URL,
        "YS_REDIS_URL": REDIS_URL,
        "YS_S3_ENDPOINT": S3_ENDPOINT,
        "YS_S3_ACCESS_KEY": S3_KEY,
        "YS_S3_SECRET_KEY": S3_SECRET,
    }


class Processes:
    """三进程编排;每个进程都能单独 kill / 重启(用于恢复验证)。"""

    def __init__(self) -> None:
        self.api: subprocess.Popen | None = None
        self.worker: subprocess.Popen | None = None
        self.device: subprocess.Popen | None = None

    def start_api(self) -> None:
        self.api = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app",
             "--host", "127.0.0.1", "--port", str(API_PORT)],
            cwd=ROOT, env=_env(),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self._wait(f"{API}/health/ready", self.api, check_ready=True)

    def start_worker(self) -> None:
        self.worker = subprocess.Popen(
            [sys.executable, "-m", "app.workers.runner",
             "--consumer", "e2e", "--interval", "0.3"],
            cwd=ROOT, env=_env(),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        time.sleep(1.5)  # 常驻进程无健康端点,给建连时间
        assert self.worker.poll() is None, self._drain(self.worker)

    def start_device(self) -> None:
        self.device = subprocess.Popen(
            [sys.executable, "-m", "mock_device.server", "--files", "2",
             "--control-port", str(DEVICE_CONTROL_PORT),
             "--file-port", str(DEVICE_FILE_PORT)],
            cwd=REPO, env=os.environ.copy(),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self._wait_device()

    def _wait(self, url: str, proc: subprocess.Popen, *,
              check_ready: bool = False, timeout_s: int = 40) -> None:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if proc.poll() is not None:
                raise AssertionError(f"process exited early:\n{self._drain(proc)}")
            try:
                r = requests.get(url, timeout=2)
                if r.status_code == 200 and (not check_ready or r.json()["ready"]):
                    return
            except requests.RequestException:
                pass
            time.sleep(0.4)
        raise AssertionError(f"timeout waiting for {url}")

    def _wait_device(self, timeout_s: int = 30) -> None:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if self.device.poll() is not None:
                raise AssertionError(f"device exited:\n{self._drain(self.device)}")
            try:
                r = requests.post(f"{DEVICE}/cmd",
                                  json={"cmd": "info.get", "params": {}, "seq": 1},
                                  timeout=2)
                if r.status_code == 200 and r.json().get("ok"):
                    return
            except requests.RequestException:
                pass
            time.sleep(0.4)
        raise AssertionError("timeout waiting for mock device")

    @staticmethod
    def _drain(proc: subprocess.Popen) -> str:
        try:
            return (proc.stdout.read() or b"").decode(errors="replace")[-2000:]
        except Exception:
            return "<no output>"

    def kill(self, name: str) -> None:
        proc = getattr(self, name)
        if proc and proc.poll() is None:
            proc.send_signal(signal.SIGKILL)
            proc.wait(timeout=10)

    def stop_all(self) -> None:
        for name in ("api", "worker", "device"):
            self.kill(name)


@pytest.fixture
def procs(db, redis_db, s3_client):
    p = Processes()
    try:
        p.start_device()
        p.start_api()
        p.start_worker()
        yield p
    finally:
        p.stop_all()


# ---------------- 客户端侧:复刻 App 的黄金链(纯 HTTP,与 Dart 实现同契约)

def _device_cmd(cmd: str, params: dict | None = None) -> dict:
    r = requests.post(f"{DEVICE}/cmd",
                      json={"cmd": cmd, "params": params or {}, "seq": 1},
                      timeout=10)
    body = r.json()
    assert body["ok"], body
    return body["data"]


def _login(email: str = "e2e@test.com") -> dict:
    code = requests.post(f"{API}/v1/auth/email/code",
                         json={"email": email}, timeout=10).json()["dev_code"]
    token = requests.post(f"{API}/v1/auth/email/verify",
                          json={"email": email, "code": code},
                          timeout=10).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _download_with_ranges(file_url: str, file_id: str, size: int) -> bytes:
    """按 App 的续传语义下载:Range 逐段,直到取满(mock 每次可能截断)。"""
    data = b""
    while len(data) < size:
        r = requests.get(f"{file_url}/files/{file_id}",
                         headers={"Range": f"bytes={len(data)}-"}, timeout=30)
        assert r.status_code in (200, 206), r.status_code
        assert r.content, "device returned empty chunk"
        data += r.content
    return data


def _upload(headers: dict, recording_id: str, data: bytes, sha: str) -> None:
    init = requests.post(f"{API}/v1/uploads/init", json={
        "recording_id": recording_id, "size_bytes": len(data),
        "sha256": sha, "part_size": 5 * 1024 * 1024,
    }, headers=headers, timeout=30).json()
    if init.get("deduplicated"):
        return
    for part in init["parts"]:
        n = part["part_no"]
        chunk = data[(n - 1) * init["part_size"]: n * init["part_size"]]
        put = requests.put(part["put_url"], data=chunk, timeout=60)
        assert put.status_code == 200, put.text
        etag = put.headers["ETag"].strip('"')
        requests.post(
            f"{API}/v1/uploads/{init['upload_id']}/parts/{n}/complete",
            json={"etag": etag, "size_bytes": len(chunk)},
            headers=headers, timeout=30)
    done = requests.post(f"{API}/v1/uploads/{init['upload_id']}/complete",
                         headers=headers, timeout=60).json()
    assert done["completed"] is True


def _wait_job(headers: dict, job_id: str, timeout_s: int = 60) -> str:
    """等待常驻 worker 处理(不在测试内驱动任何 worker 逻辑)。"""
    deadline = time.time() + timeout_s
    last = "?"
    while time.time() < deadline:
        last = requests.get(f"{API}/v1/jobs/{job_id}",
                            headers=headers, timeout=10).json()["status"]
        if last in ("completed", "failed"):
            return last
        time.sleep(0.5)
    raise AssertionError(f"job stuck at {last}")


def _sync_one_file(headers: dict, entry: dict, file_url: str) -> tuple[str, str]:
    """App 黄金链:下载 → 校验 → 登记 → 上传 → 建任务。返回 (rec_id, job_id)。"""
    data = _download_with_ranges(file_url, entry["id"], entry["size"])
    sha = hashlib.sha256(data).hexdigest()
    assert sha == entry["sha256"], "downloaded content hash mismatch"

    rec = requests.post(f"{API}/v1/recordings", json={
        "title": entry["name"], "duration_ms": max(60000, entry["size"] // 16),
        "sha256": sha, "size_bytes": len(data),
        "source": "device", "device_file_id": entry["id"],
    }, headers=headers, timeout=30).json()
    _upload(headers, rec["id"], data, sha)
    job = requests.post(f"{API}/v1/jobs", json={"recording_id": rec["id"]},
                        headers=headers, timeout=30).json()
    requests.post(f"{file_url}/files/{entry['id']}/ack", timeout=10)
    return rec["id"], job["id"]


# ---------------- 测试

def test_three_process_golden_flow(procs):
    """无真机全过程:设备 → App(HTTP)→ API → Worker → 转写与摘要可读。"""
    _device_cmd("info.get")
    wifi = _device_cmd("wifi.start")
    file_url = f"http://127.0.0.1:{DEVICE_FILE_PORT}"
    files = _device_cmd("files.list")["files"]
    assert len(files) == 2

    headers = _login()
    rec_id, job_id = _sync_one_file(headers, files[0], file_url)

    assert _wait_job(headers, job_id) == "completed"

    segments = requests.get(f"{API}/v1/recordings/{rec_id}/transcript",
                            headers=headers, timeout=10).json()["segments"]
    assert len(segments) == 6
    assert {s["speaker"] for s in segments} == {"Speaker 1", "Speaker 2"}

    summary = requests.get(f"{API}/v1/recordings/{rec_id}/summary",
                           headers=headers, timeout=10).json()
    seg_ids = {s["segment_id"] for s in segments}
    todos = [i for i in summary["items"] if i["type"] == "todo"]
    assert todos, "摘要必须含待办"
    for item in summary["items"]:
        assert item["evidence"], f"缺时间戳证据: {item['text']}"
        for ev in item["evidence"]:
            assert ev["segment_id"] in seg_ids       # 可跳转到原文分段
            assert ev["end_ms"] > ev["start_ms"]

    balance = requests.get(f"{API}/v1/entitlements/me",
                           headers=headers, timeout=10).json()
    assert balance["total_minutes"] < FREE_MINUTES   # 已扣费
    notes = requests.get(f"{API}/v1/notifications",
                         headers=headers, timeout=10).json()["items"]
    assert any(n["type"] == "job_completed" for n in notes)


def test_flow_continues_after_api_and_worker_restart(procs):
    """API 与 Worker 分别重启后流程可继续(退出标准)。"""
    _device_cmd("wifi.start")
    file_url = f"http://127.0.0.1:{DEVICE_FILE_PORT}"
    files = _device_cmd("files.list")["files"]
    headers = _login("restart@test.com")

    # 第一个文件跑通
    rec1, job1 = _sync_one_file(headers, files[0], file_url)
    assert _wait_job(headers, job1) == "completed"

    # 杀掉两个服务端进程后重启(数据在 PG/Redis/S3,不在进程内存)
    procs.kill("api")
    procs.kill("worker")
    procs.start_api()
    procs.start_worker()

    # 旧数据仍在
    assert requests.get(f"{API}/v1/recordings/{rec1}",
                        headers=headers, timeout=10).status_code == 200
    assert requests.get(f"{API}/v1/jobs/{job1}",
                        headers=headers, timeout=10).json()["status"] == "completed"

    # 重启后的实例继续跑通第二个文件
    rec2, job2 = _sync_one_file(headers, files[1], file_url)
    assert _wait_job(headers, job2) == "completed"
    assert requests.get(f"{API}/v1/recordings/{rec2}/summary",
                        headers=headers, timeout=10).json()["items"]


def test_job_queued_while_worker_down_is_processed_after_restart(procs):
    """Worker 宕机期间创建的任务,在其恢复后被处理(Outbox 不丢事件)。"""
    _device_cmd("wifi.start")
    file_url = f"http://127.0.0.1:{DEVICE_FILE_PORT}"
    files = _device_cmd("files.list")["files"]
    headers = _login("offline@test.com")

    procs.kill("worker")                       # worker 先下线
    rec, job = _sync_one_file(headers, files[0], file_url)
    time.sleep(1.0)
    assert requests.get(f"{API}/v1/jobs/{job}", headers=headers,
                        timeout=10).json()["status"] == "waiting"  # 无人处理

    procs.start_worker()                        # 恢复后自动消费积压
    assert _wait_job(headers, job) == "completed"
    assert requests.get(f"{API}/v1/recordings/{rec}/transcript",
                        headers=headers, timeout=10).json()["segments"]
