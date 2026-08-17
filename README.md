# YS Note / 宇深AI记忆助手 (项目代号)

类 DOWAY 的 AI 录音硬件配套 App:双平台(iOS + Android)设备管理、音频同步、AI 转写、摘要、编辑、导出与会员付费。

**当前定位:`V0.1 Architecture & Prototype Baseline`** — 架构与原型基线,
**不是**可运行 MVP,更不是可交付双平台 App。已实现/未实现边界见
`docs/04-api-spec.md` 顶部的实现状态标注;真机通信、支付平台对接、数据库持久化
接入均未完成。硬件协议验证(阶段0)完成前,所有设备相关代码针对 `mock_device/`
模拟协议开发。

统一验证入口:`make verify`(服务端测试 + 模拟设备测试 + flutter analyze/test,
任何失败即终止,CI 同口径)。

## 仓库结构

| 目录 | 内容 |
|---|---|
| `docs/` | PRD、技术架构、硬件厂协议资料清单、API 规范、验收标准、测试计划、错误码体系、埋点方案、26周交付计划 |
| `server/` | FastAPI 服务端:24 张核心表 schema、分片上传、任务队列、AI 任务状态机、权益服务(usage_ledger 流水)、订单验证框架、管理后台骨架 |
| `mock_device/` | 模拟设备服务:软件模拟 BLE 状态机 + Wi-Fi 文件服务器(Range 断点续传 + SHA-256 校验),供 App/后端在无真机时开发联调 |
| `mobile/` | Flutter 工程脚手架:Riverpod + Clean Architecture、zh/en/ar 多语言 + RTL、SQLite、上传队列、Platform Channel 接口、iOS(Swift)/Android(Kotlin) 原生模块骨架 |
| `scripts/` | `demo_up.sh` 一键启动 Demo 环境(基础设施 + API + Worker + 模拟录音机) |
| `.github/workflows/` | CI:服务端与模拟设备测试、集成测试、compose 从零启动、Flutter analyze/test、Android/iOS 构建 |

## 快速开始

```bash
# 服务端测试
cd server && pip install -e ".[dev]" && pytest

# 模拟设备(BLE 控制面 :9100,Wi-Fi 文件面 :9101)
python -m mock_device.server

# Flutter(需本地安装 Flutter SDK ≥ 3.22)
cd mobile && flutter pub get && flutter gen-l10n && flutter analyze
```

## 关键工程约定

- 所有 AI 分钟扣减必须通过 `usage_ledger` 流水表,禁止直接改用户余额字段(见 `docs/02-architecture.md` §7)。
- AI 任务状态迁移必须走 `server/app/services/job_state_machine.py`,禁止直接 UPDATE 状态列。
- 错误码统一见 `docs/07-error-codes.md`,客户端与服务端共用一套编号。
- 设备通信协议以 `mock_device/protocol.md` 为占位契约,待硬件厂协议(见 `docs/03-hardware-protocol-checklist.md`)到位后替换映射层,业务层接口不变。

## 立项红线(阶段0)

硬件厂未交付完整 BLE/Wi-Fi/OTA 协议、6 台以上工程样机与协议使用授权之前,**不进入阶段2(真机设备连接)开发**。详见 `docs/09-delivery-plan.md`。
