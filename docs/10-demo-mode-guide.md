# Demo Mode 演示与商店审核指南

用途:
1. **内部演示 / 录屏**:无真机跑通完整产品流程;
2. **App Store / Google Play 审核**:审核人员没有录音硬件,必须能独立走完全流程(见 `01-prd.md` §11);
3. **回归验证**:三进程真实联调由 `server/tests/integration/test_demo_e2e_processes.py` 自动执行,CI 每次运行。

## 一、一键启动

```bash
make demo-up          # 基础设施 + API + Worker + 模拟录音机
make demo-down        # 停止并清理
```

启动后:

| 服务 | 地址 | 说明 |
|---|---|---|
| API | http://127.0.0.1:8000/docs | 接口文档;`/health/ready` 查依赖状态 |
| 模拟录音机 | http://127.0.0.1:9100 | 控制面(BLE 语义);文件面 9101 |
| Mailpit | http://127.0.0.1:8025 | 收登录验证码邮件 |
| MinIO 控制台 | http://127.0.0.1:9001 | 音频对象存储(ysnote / ysnote-dev-secret) |

日志在 `.demo-run/{api,worker,device}.log`。

## 二、启动 App

```bash
cd mobile
flutter run \
  --dart-define=YS_API_URL=http://127.0.0.1:8000 \
  --dart-define=YS_DEVICE_URL=http://127.0.0.1:9100
```

- **Android 模拟器**:把 `127.0.0.1` 换成 `10.0.2.2`(模拟器访问宿主机的地址);
- **iOS 模拟器**:`127.0.0.1` 可直接用;
- **真机调试**:换成开发机在局域网的 IP。

## 三、演示脚本(审核人员操作路径)

1. **登录**:邮箱默认已填,点"发送验证码"——dev 环境自动回填验证码(prod 环境到 Mailpit / 真实邮箱取码),点登录。
2. **连接设备**:进入设备页,点"添加设备"。App 通过 DeviceTransport 连接模拟录音机,显示型号、SN、电量、固件版本,并列出 3 个预置录音文件。
3. **同步**:点任一文件的"同步录音"。依次展示进度:下载(Range 断点续传 + SHA-256 校验)→ 登记 → 上传(S3 分片)→ 创建 AI 任务。
4. **查看结果**:自动跳转到转写页,任务状态轮询至完成后显示:
   - **原文页签**:6 个分段,双说话人,每段带时间戳,含中英混语;
   - **摘要页签**:结论与待办,每条下方是时间戳 chip。
5. **时间戳溯源**:点摘要里任一时间戳 chip → 跳回原文页签并高亮对应分段。这是产品的核心可信度机制(摘要不凭空生成,每条都能定位到原话)。

## 四、Demo Mode 与真机的关系

App 通过 `DeviceTransport` 抽象访问设备,两种实现:

| 实现 | 用途 | 状态 |
|---|---|---|
| `MockHttpDeviceTransport` | Demo Mode / 审核 / 自动化测试 | 已完成 |
| `NativeDeviceTransport` | 真机(BLE + Wi-Fi) | 待硬件协议 PoC(阶段0)通过后启用 |

**业务层、页面、上传与 AI 链路完全一致**,切换真机只替换 `deviceTransportProvider` 的实现,其余代码不动。

AI 侧同理:`YS_AI_PROVIDER=mock` 走固定演示脚本;接入真实 ASR/LLM 后改为对应 provider,写库结构与 API 不变。

## 五、自动化保障

`test_demo_e2e_processes.py` 以三个**独立操作系统进程**(uvicorn + worker + mock_device)运行,全程只经 HTTP,验证:

- 无真机走完全过程,摘要证据可定位到原文分段;
- **API 与 Worker 分别重启后流程可继续**(数据在 PostgreSQL/Redis/S3,不在进程内存);
- **Worker 宕机期间创建的任务,恢复后被处理**(Outbox 不丢事件)。

## 六、审核提交前检查表

- [ ] 审核账号可登录(prod 环境需预置账号,验证码走真实邮箱)
- [ ] Demo Mode 在 App 内可达且无需硬件
- [ ] 内置演示录音可完成转写(或 Demo 设备可用)
- [ ] 手机麦克风录音可用(无硬件用户路径)
- [ ] App 内账户删除入口可达
- [ ] 权限按需申请,用途说明与实际一致
- [ ] 隐私标签 / Data Safety 与实际 SDK 一致
