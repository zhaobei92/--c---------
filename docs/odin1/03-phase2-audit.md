# Odin1 第二阶段发布前审计与加固

> 对 `odin1_control` + `odin1_tf_adapter` 的架构审计，范围：多线程竞态、Action 生命周期、
> 退出安全、USB 操作互斥、异常恢复、重定位前错误 TF 泄漏。
> **未扩功能**；接口签名保持兼容，只有 `map_odom_fallback` 的默认值变了（见 A5）。
> 状态：未编译、未接硬件；但策略层已真实编译并跑通（见 §4）。

---

## 1. 结论先行

11 个问题，其中 **3 个会导致进程崩溃或挂死**，**3 个会让上层拿到"看起来正常但实际错误"的位姿**。
全部已修。最要命的两个都是上一阶段自己引入的：

- **A1**：我把控制层的 shutdown 钩子插在了 `lidar_system_deinit()` **之后**。
  Ctrl+C 撞上存图时，会先拆掉 SDK，再花 130 秒等一个正在调用已销毁 SDK 的线程。
- **A5**：重定位尚未成功时发布单位阵 `map→odom`。TF 树看起来完好，
  但 `lookupTransform("map","base_link")` 返回的是 odom 系位姿冒充 map 系位姿 ——
  规划器会心安理得地在一张自己并没有定位上的地图里跑。

---

## 2. 审计发现

### 2.1 严重

| ID | 问题 | 现象 | 修复 |
|---|---|---|---|
| **A1** | 退出顺序反了 | 驱动 SIGINT 处理器顺序是 `lidar_stop_stream → odinDevice=nullptr → lidar_system_deinit() → exit(0)`。我的 `shutdown()` 插在 `lidar_system_deinit()` 之后：存图中 Ctrl+C = 先拆 SDK，再等 130 s，且工作线程正在调用已销毁的 SDK | 屏障移到信号处理器**最前面**（第一次 SDK 调用之前），grace 缩到 3 s（信号上下文里不能久等，进程反正要走） |
| **A2** | 双 Goal 的 TOCTOU | goal 回调查标志、worker 才置标志，两个 goal 都能通过检查被接受。SaveMap 与 SwitchMode 各持一个标志，还能互相穿插 | 收敛成**一个 CAS 的准入槽** `exclusive_op_`，在 ACCEPT 前最后一步抢占。互斥从"执行时靠 mutex 补救"提前到"准入时就拒绝" |
| **A3** | 分离线程无异常保护 | `saveMapExecute`/`switchModeExecute` 跑在 detached thread 上。任何异常逃逸 = `std::terminate`，**整个驱动进程死**，点云/里程计发布器一起陪葬。`gh->succeed()` 在 goal 已终止时会抛 `RCLError`，cancel 竞争时就会发生 | 两个 worker 全包 try/catch（含 `catch(...)`）；`succeed/abort/canceled/publish_feedback` 全部走 `safeXxx` 包装 |

### 2.2 高

| ID | 问题 | 现象 | 修复 |
|---|---|---|---|
| **A4** | 设备句柄一把梭 | 句柄取一次用满 120 s。驱动重连路径 `if (odinDevice) { odinDevice = nullptr; ... }`（`:1298-1300`）会回收句柄，之后每一步都在用已废弃的指针 | `DeviceSession` RAII：每次 SDK 调用前重新取句柄并与初始值比对，变了就 `RC_DEVICE_LOST` 失败；再加一处补丁让重连前先 `waitForDeviceIdle(2s)` |
| **A5** | **重定位前错误 TF 泄漏** | `map_odom_fallback` 默认 `identity`，与设备模式无关。map_mode=2 且未定位成功时发单位阵，下游读到的是 odom 位姿伪装成 map 位姿，**静默错误** | 抽出纯函数 `decideMapOdom()`；默认改为 `auto`：只有设备自己定义 map≡odom 的模式（0/1）才发单位阵；模式 2 或模式未知时**什么都不发**，让 map 断开——响亮的失败好过静默的错误 |
| **A6** | 丢定位后跳回单位阵 | 已经定位成功再丢失时，`identity` 回退会把机器人从真实 map 位姿瞬移到 odom 原点，且看起来像一次合法定位 | 不变式：**一旦收到过真实 fix，任何配置下都不再发单位阵**。`identity` 在这种情况下降级为 hold |
| **A7** | switch_mode 后残留旧修正 | `switch_mode` 重启数据流、odom 归零，但适配器仍持有上一个 odom 纪元的 `map→odom`，套到新 odom 上完全错位 | 轮询 `get_device_state`，检测到 map_mode 变化或 `mode_switch_in_progress` 下降沿即 `invalidateMapOdom()`；切换进行中一律不发 |

### 2.3 中 / 低

| ID | 问题 | 修复 |
|---|---|---|
| **A8** | 每次 `GetDeviceState` 都发一次 USB `lidar_get_version`，与正在跑的存图抢同一条控制通道 | 版本按句柄缓存，只查一次；句柄变了才重查 |
| **A9** | 适配器用单线程 `spin`，400 Hz 里程计回调可能饿死状态定时器和 service client——而那正是喂给 A5 安全策略的数据源 | 改 `MultiThreadedExecutor(2)`，定时器 + client 放独立回调组 |
| **A10** | 控制层自造的 rc 没有语义，客户端只能看到裸数字 | 新增 `RC_SHUTTING_DOWN / RC_DEVICE_LOST / RC_INTERNAL_ERROR` + `rcText()`，所有 message 带人话解释 |
| **A11** | feedback ticker 线程调 `publish_feedback`，goal 终止后会抛 | `safeFeedback` 包装 + ticker 自身 try/catch |

### 2.4 未修：已知并接受的风险

| 风险 | 为什么不修 |
|---|---|
| `g_custom_map_mode` 是裸 `int`，控制层写、驱动读 | 字长对齐的单次读写，实践中无撕裂；要修得改驱动变量类型，patch 面积不值 |
| 驱动信号处理器本身不是 async-signal-safe（调 `RCLCPP_INFO`、`fclose`、`exit`） | 厂商既有设计，不在本次范围。我们只把自己的贡献限制在有界的 3 s |
| `exit(0)` 不 join 分离线程：存图超过 3 s grace 时进程仍会退出 | `lidar_save_map()` 无中断点，这是 SDK 的硬限制。已改为大声记 ERROR 而非静默 |
| `SaveMap` 拒绝 cancel | 设计如此：假装取消会留下设备继续生成、无人接收的地图 |

---

## 3. Mock SDK 与测试

### 3.1 链接期 Mock —— 架构自然长出来的

`odin1_control` 是**静态库**，调用 `lidar_*()` 但从不链接 `liblydHostApi`。
这些符号一直未定义，直到有人链接这个 archive：驱动用厂商 `.a` 解析，测试用 mock 解析。

**生产代码里没有任何测试接缝，没有 `#ifdef TESTING`，跑的就是发货的那批 .o。**

```
test_control_server  ──┬── odin1_control.a      (未定义 lidar_*)
                       └── odin_sdk_mock.cpp    (定义 lidar_*)
```

`odin_sdk_mock.cpp` 先 include 真实 `lidar_api.h`，所以**签名由编译器校验**——
厂商改了原型，这里编译不过，而不是静默 mock 了一个错误契约。

### 3.2 覆盖的场景

| 请求的场景 | 测试 |
|---|---|
| 设备断开 | `ServicesReportDeviceNotOpenWhenDisconnected`、`NullHandleIsTreatedAsDisconnected`、`SaveMapAbortsWhenDeviceDisconnectsBeforeExecution` |
| busy | `SaveMapRejectedWhileLegacyTransferRunning`、`LoadMapReturnsBusyWhileSaveMapHoldsTheDevice`、`GetDeviceStateStaysAnswerableDuringSaveMap` |
| 超时 | `SaveMapTimeoutIsReportedNotSwallowed`、`SaveMapReleasesTheSlotAfterFailureSoRetryWorks` |
| 双 Goal | `SecondSaveMapGoalIsRejectedWhileFirstRuns`（并断言 `max_concurrent_save_map == 1`）、`SwitchModeIsRejectedWhileSaveMapRuns` |
| 存图中退出 | `ShutdownDrainsAnInFlightSaveInsteadOfRacingIt`、`RequestsAfterTeardownAreRefusedNotRaced` |
| 重定位失败 | `SwitchModeAbortsWhenMapUploadFails`、`SwitchModeToRelocalizationRejectedWithoutAMap`、`SwitchModeRejectsAMapPathThatDoesNotExist`、`LoadMapRetriesThenReportsFailure` |
| 重连换句柄（A4） | `SwitchModeAbortsWhenTheHandleChangesBetweenSteps`、`SaveMapFailsIfTheHandleIsRecycledMidOperation` |
| 时序契约 | `SwitchModeRunsTheDocumentedSevenStepSequenceInOrder`（按顺序断言 7 步 + `save_map=0` 置位）、`SetInitPoseReportsThatStreamingDefersTheValue` |
| TF 安全策略 | `test_map_odom_policy` 12 个用例，穷举 `have_fix × fresh × map_mode × fallback × switching` |

### 3.3 跑

```bash
colcon test --packages-select odin1_control odin1_tf_adapter
colcon test-result --verbose
```

---

## 4. 本次实际验证到哪一步

没有 ROS 环境，但**策略层和 Mock 层是 ROS-free 的，所以是真跑过的**：

| 验证 | 方法 | 结果 |
|---|---|---|
| **TF 安全策略逻辑** | 用 gtest 兼容 shim 编译**未经修改的** `test_map_odom_policy.cpp` 并执行 | `g++ -Wall -Wextra` 零警告，**12/12 通过** |
| **Mock 签名 vs 厂商头文件** | `g++ -fsyntax-only -I<sdk>` 编译 `odin_sdk_mock.cpp` | 干净通过 → 10 个原型与 `lidar_api.h` 逐一吻合 |
| Mock 完整性 | 交叉比对控制层引用的 SDK 符号 | 10/10 已定义（`lidar_system_deinit` 仅出现在注释里） |
| 补丁往返 | 真实 clone 上 apply → revert → `diff -r` | 逐字节一致；10 处锚点全部唯一 |
| **退出顺序不变式** | 解析打过补丁的驱动源码行号 | `beginTeardown@379 < lidar_stop_stream@388 < lidar_system_deinit@405` ✓<br>`waitForDeviceIdle@1332 < odinDevice=nullptr@1336` ✓ |
| IDL / C++ 结构 / 头实现一致性 | 静态检查器 | 全通过（ControlServer 27 定义，TfAdapterNode 11 定义） |

**仍未验证**：`control_server.cpp` 与 `tf_adapter_node.cpp` 的编译（需要 rclcpp），
以及 `test_control_server.cpp` 的实际执行（需要 rclcpp_action + ROS 图）。

---

## 5. 行为变更（升级注意）

只有一处对使用者可见：

```yaml
# odin1_tf_adapter/config/tf_adapter.yaml
map_odom_fallback: "auto"     # 原默认 "identity"
```

在**重定位模式且尚未定位成功**时，`map` 坐标系现在会**保持断开**，
RViz 的 Fixed Frame 设为 `map` 时会显示不出东西——这是有意的：
以前那个"能显示"的状态是错的。想恢复旧行为（仅限调试）显式写 `identity`，
节点启动时会打一条警告。

`localization_status.state_text` 现在会附带 `| map->odom: <原因>`，
直接回答"为什么没有 map 坐标系"。
