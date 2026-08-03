# Mock Device 协议(占位契约 V0.1)

用途:在硬件厂真协议(`docs/03-hardware-protocol-checklist.md`)到位前,为 App 与后端提供
可开发、可自动化测试的设备行为契约。真协议到位后**只替换传输映射层**,命令语义保持不变。
Demo Mode(App 审核用)同样复用本协议。

模拟方式:
- **BLE 控制面** → HTTP JSON(`:9100`)。真机上这些命令走 BLE 特征写/通知;
  命令名、参数、错误码与状态机与本文档一致。
- **Wi-Fi 文件面** → HTTP(`:9101`)。仅在 `wifi.start` 后可用,模拟设备临时热点;
  支持 `Range` 断点续传与 SHA-256 校验,并可注入传输故障。

## 1. 控制面(模拟 BLE)

统一入口:`POST /cmd`,请求 `{"cmd": "<name>", "params": {...}, "seq": <int>}`,
响应 `{"seq": <int>, "ok": true, "data": {...}}` 或 `{"seq": <int>, "ok": false, "error": "<code>"}`。

| cmd | params | data | 对应真机命令 |
|---|---|---|---|
| info.get | — | sn, model, firmware, protocol_version | 获取 SN/型号/固件版本 |
| battery.get | — | percent | 获取电量 |
| storage.get | — | total_mb, free_mb | 获取剩余空间 |
| record.status | — | state(idle/recording/paused), mode | 获取录音状态/模式 |
| record.start | mode? | state | 开始录音 |
| record.pause | — | state | 暂停 |
| record.stop | — | file(新文件条目) | 停止并保存 |
| record.set_params | mode, sample_rate?, denoise? | applied | 修改录音参数 |
| files.list | — | files[]: {id, name, size, sha256, created_at, mode} | 获取文件列表 |
| files.delete | file_id | deleted | 删除文件 |
| wifi.start | — | ssid, password, url(文件面地址), ttl_s | 开启 Wi-Fi 传输 |
| wifi.stop | — | stopped | 关闭热点 |
| ota.enter | version, sha256, min_battery | accepted | 进入 OTA 模式 |
| factory.reset | — | ok | 恢复出厂 |
| reboot | — | ok | 重启 |
| fault.inject | kind, value | ok | **仅模拟器**:注入故障(见 §3) |

设备状态机:`idle ⇄ recording ⇄ paused`;`ota` 模式中拒绝其余命令(`E_BUSY`);
Wi-Fi 开启中允许控制命令;`record.stop` 仅在 recording/paused 合法。

错误码:`E_BAD_CMD`(未知命令)、`E_BAD_STATE`(状态机拒绝)、`E_NOT_FOUND`(文件不存在)、
`E_LOW_BATTERY`(OTA 电量不足)、`E_BUSY`(OTA 中)、`E_STORAGE_FULL`。
App 侧映射:`E_BAD_STATE→DEV_1401 类`、`E_LOW_BATTERY→DEV_1302` 等(docs/07)。

## 2. 文件面(模拟设备 Wi-Fi 热点)

Wi-Fi 未开启时所有端点返回 `503 {"error": "wifi_off"}`(模拟未加入热点)。

| 端点 | 说明 |
|---|---|
| GET /files | 文件索引(与控制面 files.list 相同数据) |
| GET /files/{id} | 下载文件体;支持 `Range: bytes=start-end`,响应 206 + `Content-Range` |
| GET /files/{id}/sha256 | 服务端计算的完整文件 SHA-256(App 下载后比对) |
| POST /files/{id}/ack | 下载完成确认(设备标记 synced;App 可据用户设置再发 files.delete) |

## 3. 故障注入(测试同步异常用例 SYNC-01…14)

`fault.inject` 支持:

| kind | value | 效果 |
|---|---|---|
| wifi_drop_after | 字节数 N | 下一次下载在发送 N 字节后截断(模拟传输中断→验证 Range 续传) |
| wifi_ttl | 秒 | 热点在 N 秒后自动关闭(模拟热点超时) |
| low_battery | percent | 设置电量(测 OTA 阈值 DEV_1302) |
| corrupt_file | file_id | 下载内容被破坏但 sha256 端点仍返回原值(测 Hash 校验失败重试 DEV_1204) |
| clear | — | 清除全部故障 |

## 4. 启动

```bash
python -m mock_device.server            # 控制面 :9100,文件面 :9101
python -m mock_device.server --files 5  # 预置 5 个模拟录音文件
```
