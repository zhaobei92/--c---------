"""模拟设备核心状态机(与 protocol.md 同步)。

纯 Python,无 IO —— server.py 把它暴露为 HTTP,测试可直接驱动。
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass, field


class CmdError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass
class DeviceFile:
    id: str
    name: str
    data: bytes
    mode: str
    created_at: str
    synced: bool = False

    @property
    def size(self) -> int:
        return len(self.data)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()

    def index_entry(self) -> dict:
        return {"id": self.id, "name": self.name, "size": self.size,
                "sha256": self.sha256, "created_at": self.created_at,
                "mode": self.mode, "synced": self.synced}


def _fake_audio(seconds: int, seed: int) -> bytes:
    """确定性伪音频数据(≈16KB/s,模拟压缩音频码率)。"""
    chunk = hashlib.sha256(f"audio-{seed}".encode()).digest()
    return (chunk * (seconds * 512))[: seconds * 16384]


@dataclass
class DeviceSimulator:
    sn: str = "MOCK-SN-0001"
    model: str = "MOCK-1"
    firmware: str = "0.9.0"
    protocol_version: str = "0.1"
    battery: int = 88
    total_mb: int = 64 * 1024
    record_state: str = "idle"  # idle / recording / paused / ota
    mode: str = "meeting"
    files: dict[str, DeviceFile] = field(default_factory=dict)
    wifi_on: bool = False
    wifi_ssid: str = ""
    wifi_password: str = ""
    faults: dict[str, object] = field(default_factory=dict)

    # ------------------------------------------------ 工具

    def seed_files(self, count: int) -> None:
        for i in range(count):
            f = DeviceFile(
                id=f"F{i + 1:04d}", name=f"REC_{i + 1:04d}.opus",
                data=_fake_audio(seconds=60 * (i + 1), seed=i),
                mode="meeting", created_at=f"2026-08-0{(i % 7) + 1}T09:00:00Z",
            )
            self.files[f.id] = f

    @property
    def free_mb(self) -> int:
        used = sum(f.size for f in self.files.values()) // (1024 * 1024)
        return max(0, self.total_mb - used)

    # ------------------------------------------------ 命令分发

    def execute(self, cmd: str, params: dict) -> dict:
        if self.record_state == "ota" and cmd not in ("info.get", "reboot"):
            raise CmdError("E_BUSY")
        handler = {
            "info.get": self._info,
            "battery.get": lambda p: {"percent": self.battery},
            "storage.get": lambda p: {"total_mb": self.total_mb, "free_mb": self.free_mb},
            "record.status": lambda p: {"state": self.record_state, "mode": self.mode},
            "record.start": self._record_start,
            "record.pause": self._record_pause,
            "record.stop": self._record_stop,
            "record.set_params": self._set_params,
            "files.list": lambda p: {"files": [f.index_entry() for f in self.files.values()]},
            "files.delete": self._delete_file,
            "wifi.start": self._wifi_start,
            "wifi.stop": self._wifi_stop,
            "ota.enter": self._ota_enter,
            "factory.reset": self._factory_reset,
            "reboot": self._reboot,
            "fault.inject": self._fault_inject,
        }.get(cmd)
        if handler is None:
            raise CmdError("E_BAD_CMD")
        return handler(params)

    # ------------------------------------------------ 各命令实现

    def _info(self, p: dict) -> dict:
        return {"sn": self.sn, "model": self.model, "firmware": self.firmware,
                "protocol_version": self.protocol_version}

    def _record_start(self, p: dict) -> dict:
        if self.record_state not in ("idle", "paused"):
            raise CmdError("E_BAD_STATE")
        if self.free_mb <= 0:
            raise CmdError("E_STORAGE_FULL")
        self.mode = p.get("mode", self.mode)
        self.record_state = "recording"
        return {"state": self.record_state}

    def _record_pause(self, p: dict) -> dict:
        if self.record_state != "recording":
            raise CmdError("E_BAD_STATE")
        self.record_state = "paused"
        return {"state": self.record_state}

    def _record_stop(self, p: dict) -> dict:
        if self.record_state not in ("recording", "paused"):
            raise CmdError("E_BAD_STATE")
        self.record_state = "idle"
        f = DeviceFile(
            id=f"F{uuid.uuid4().hex[:8].upper()}",
            name=f"REC_{len(self.files) + 1:04d}.opus",
            data=_fake_audio(seconds=60, seed=len(self.files) + 100),
            mode=self.mode, created_at="2026-08-03T12:00:00Z",
        )
        self.files[f.id] = f
        return {"file": f.index_entry()}

    def _set_params(self, p: dict) -> dict:
        self.mode = p.get("mode", self.mode)
        return {"applied": True}

    def _delete_file(self, p: dict) -> dict:
        if p.get("file_id") not in self.files:
            raise CmdError("E_NOT_FOUND")
        del self.files[p["file_id"]]
        return {"deleted": True}

    def _wifi_start(self, p: dict) -> dict:
        self.wifi_on = True
        self.wifi_ssid = f"YS-{self.sn[-4:]}"
        self.wifi_password = secrets.token_hex(4)
        return {"ssid": self.wifi_ssid, "password": self.wifi_password,
                "url": "http://127.0.0.1:9101", "ttl_s": int(self.faults.get("wifi_ttl", 300))}

    def _wifi_stop(self, p: dict) -> dict:
        self.wifi_on = False
        return {"stopped": True}

    def _ota_enter(self, p: dict) -> dict:
        min_battery = int(p.get("min_battery", 30))
        if self.battery < min_battery:
            raise CmdError("E_LOW_BATTERY")
        self.record_state = "ota"
        return {"accepted": True, "target": p.get("version")}

    def _factory_reset(self, p: dict) -> dict:
        self.files.clear()
        self.record_state = "idle"
        self.wifi_on = False
        self.faults.clear()
        return {"ok": True}

    def _reboot(self, p: dict) -> dict:
        self.record_state = "idle"
        self.wifi_on = False
        return {"ok": True}

    def _fault_inject(self, p: dict) -> dict:
        kind = p.get("kind")
        if kind == "clear":
            self.faults.clear()
        elif kind == "low_battery":
            self.battery = int(p["value"])
        elif kind in ("wifi_drop_after", "wifi_ttl", "corrupt_file"):
            self.faults[kind] = p["value"]
        else:
            raise CmdError("E_BAD_CMD")
        return {"ok": True}

    # ------------------------------------------------ 文件面辅助

    def read_file(self, file_id: str, start: int, end: int | None) -> tuple[bytes, int, int]:
        """返回 (数据, 实际起点, 文件总长);应用故障注入。"""
        f = self.files.get(file_id)
        if f is None:
            raise CmdError("E_NOT_FOUND")
        data = f.data
        if self.faults.get("corrupt_file") == file_id:
            data = b"\x00" * len(data)  # 内容损坏,sha256 端点仍返回原值
        total = len(data)
        stop = total if end is None else min(end + 1, total)
        chunk = data[start:stop]
        drop_after = self.faults.pop("wifi_drop_after", None)
        if drop_after is not None:
            chunk = chunk[: int(drop_after)]  # 截断一次,模拟传输中断
        return chunk, start, total
