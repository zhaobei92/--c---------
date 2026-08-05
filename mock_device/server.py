"""Mock Device HTTP 服务:控制面(模拟 BLE):9100 + 文件面(模拟 Wi-Fi 热点):9101。

启动:python -m mock_device.server [--files N]
"""

from __future__ import annotations

import argparse

from fastapi import FastAPI, Header, Request, Response
from fastapi.responses import JSONResponse

from .device_state import CmdError, DeviceSimulator


def build_apps(device: DeviceSimulator | None = None) -> tuple[FastAPI, FastAPI, DeviceSimulator]:
    dev = device or DeviceSimulator()

    # ---------------- 控制面(模拟 BLE)
    control = FastAPI(title="Mock Device — BLE control plane")

    @control.post("/cmd")
    async def cmd(request: Request):
        body = await request.json()
        seq = body.get("seq", 0)
        try:
            data = dev.execute(body.get("cmd", ""), body.get("params") or {})
            return {"seq": seq, "ok": True, "data": data}
        except CmdError as e:
            return JSONResponse(status_code=200,
                                content={"seq": seq, "ok": False, "error": e.code})

    # ---------------- 文件面(模拟设备 Wi-Fi 热点)
    files = FastAPI(title="Mock Device — WiFi file plane")

    def _wifi_guard() -> JSONResponse | None:
        if not dev.wifi_on:
            return JSONResponse(status_code=503, content={"error": "wifi_off"})
        return None

    @files.get("/files")
    def list_files():
        if (r := _wifi_guard()) is not None:
            return r
        return {"files": [f.index_entry() for f in dev.files.values()]}

    @files.get("/files/{file_id}")
    def download(file_id: str, range: str | None = Header(default=None)):
        if (r := _wifi_guard()) is not None:
            return r
        start, end = 0, None
        status = 200
        if range and range.startswith("bytes="):
            spec = range.removeprefix("bytes=")
            s, _, e = spec.partition("-")
            start = int(s or 0)
            end = int(e) if e else None
            status = 206
        try:
            chunk, actual_start, total = dev.read_file(file_id, start, end)
        except CmdError:
            return JSONResponse(status_code=404, content={"error": "not_found"})
        headers = {"Accept-Ranges": "bytes"}
        if status == 206:
            headers["Content-Range"] = (
                f"bytes {actual_start}-{actual_start + len(chunk) - 1}/{total}"
            )
        return Response(content=chunk, status_code=status,
                        media_type="application/octet-stream", headers=headers)

    @files.get("/files/{file_id}/sha256")
    def file_hash(file_id: str):
        if (r := _wifi_guard()) is not None:
            return r
        f = dev.files.get(file_id)
        if f is None:
            return JSONResponse(status_code=404, content={"error": "not_found"})
        return {"sha256": f.sha256}

    @files.post("/files/{file_id}/ack")
    def ack(file_id: str):
        if (r := _wifi_guard()) is not None:
            return r
        f = dev.files.get(file_id)
        if f is None:
            return JSONResponse(status_code=404, content={"error": "not_found"})
        f.synced = True
        return {"acked": True}

    return control, files, dev


def main() -> None:  # pragma: no cover
    import threading

    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--files", type=int, default=3, help="预置模拟录音文件数")
    parser.add_argument("--control-port", type=int, default=9100)
    parser.add_argument("--file-port", type=int, default=9101)
    args = parser.parse_args()

    control, files, dev = build_apps()
    dev.seed_files(args.files)

    t = threading.Thread(
        target=uvicorn.run, kwargs=dict(app=files, host="127.0.0.1", port=args.file_port),
        daemon=True,
    )
    t.start()
    uvicorn.run(control, host="127.0.0.1", port=args.control_port)


if __name__ == "__main__":  # pragma: no cover
    main()
