# Odin1 第二阶段设计：odin1_control 与 odin1_tf_adapter

> 承接《Odin1 开发指南》（`docs/odin1/README.md`）方向一、方向二。
> 本文是**设计决策记录**：讲清为什么这么做、改哪里、以及上机时哪些地方最可能出问题。
> 代码在 `odin1/`。状态：编码 + 静态校验完成，未编译、未接硬件。

---

## 1. 三个决定性的源码事实

设计不是从需求推导的，是从这三条读源码读出来的约束推导的。

### 事实一：驱动的 ROS2 主循环是单线程 10 Hz

```cpp
// host_sdk_sample.cpp:2485-2515
rclcpp::Rate rate(10);
while (rclcpp::ok()) {
    rclcpp::spin_some(node);          // ← 单线程
    if (g_sendcloudrender) g_ros_object->try_process_pair();
    if (deviceConnected) process_command_file();
    rate.sleep();                      // ← 100 ms
}
```

**推论**：任何放进 node 默认回调组的 service，(a) 最多 10 Hz 被响应，
(b) 执行期间**阻塞全部 publisher**。而 `lidar_save_map()` 单次可阻塞 120 秒。

**对策**：`ControlServer` 自建执行器：

```cpp
cb_group_ = node->create_callback_group(
    rclcpp::CallbackGroupType::Reentrant,
    /*automatically_add_to_executor_with_node=*/false);   // ← 关键
executor_ = std::make_shared<rclcpp::executors::MultiThreadedExecutor>(opts, 3);
executor_->add_callback_group(cb_group_, node->get_node_base_interface());
executor_thread_ = std::thread([this]{ executor_->spin(); });
```

第二个参数 `false` 让驱动的 `spin_some(node)` **不会**捡起这个回调组，
数据路径与控制路径彻底不争用。长任务再从回调里 detach 到工作线程，
执行器线程也不被占满。

### 事实二：USB 设备句柄进程私有

libusb 不允许两进程同持（厂商 FAQ 5.12，`LIBUSB_ERROR_BUSY`）。
`odinDevice` 是 `host_sdk_sample.cpp:69` 的 `static` 变量。

**推论**：控制层**必须**在驱动进程内。做不成独立节点，只能是链进去的库 + 一处补丁。

**对策**：`odin1_control` 编译成**静态库**（SDK 符号留到最终 link 由
`host_sdk_sample` 解析），通过 `DeviceContext` 结构体接收驱动全局量的访问器 lambda。
补丁只需一个 `#include` 加一处构造。

为什么不直接 `extern` 那些全局量：它们是 `static`（文件内链接），
要改成外部链接就得动更多行；而且名字一旦被上游改掉，`extern` 会在链接期
才报错。lambda 桥接把耦合收敛到补丁脚本这**一个**文件里。

### 事实三：`odom→map` 无法关闭

```cpp
// host_sdk_sample.h:1531-1549 (ROS2)
case OdometryType::TRANSFORM: {
    transformStamped.header.frame_id = "odom";   // ← 父
    transformStamped.child_frame_id  = "map";    // ← 子，与 REP-105 相反
    tf_broadcaster->sendTransform(transformStamped);
}   // ← 注意：这个 case 没有被 sendOdomBaseLinkTF() 包住
```

对比同函数里的 `STANDARD` 分支，`odom→imu` 是被 `if (getRosNodeControl()->sendOdomBaseLinkTF())`
门控的，而 `TRANSFORM` 分支不是。

**推论**：`config/control_command.yaml` 里**没有任何开关**能让驱动不发 `odom→map`。
若适配器同时发 `map→odom`，`map` 会有两个父节点，tf2 报环。

**对策**：launch 层给驱动节点加 `-r /tf:=/odin1/tf_raw -r /tf_static:=/odin1/tf_static_raw`，
适配器独占真正的 `/tf`。这是唯一可行路径，不是风格选择。

---

## 2. odin1_control 设计

### 2.1 为什么 save_map / switch_mode 是 Action 而不是 Service

| | 耗时 | 可分步 | 可取消 | 结论 |
|---|---|---|---|---|
| `save_map` | ≤120 s | 否（SDK 单次同步调用） | **否** | Action（要进度反馈），取消**明确拒绝** |
| `switch_mode` | 数百 ms ~ 数秒 | 是（7 步） | 是（步间） | Action，逐步 feedback |
| `load_map` | 秒级 | 否 | 否 | Service |
| `set_init_pose` / `reset_algo` / `get_device_state` | 毫秒级 | — | — | Service |

`save_map` 取消被**拒绝**而不是假装接受：`lidar_save_map()` 内部把
「触发 → 轮询 → 传输」整条状态机跑完，没有中断点。假装取消会让设备继续生成
一份没人来取的地图，下一次 `save_map` 反而会撞上 `-2 device busy`。诚实拒绝更好。

### 2.2 switch_mode 的 7 步序列

厂商 wiki 6.6 明说：「目前不支持动态切换设备运行模式和算法运行模式，
如需切换请先停止算法，完成相关配置后重新启动算法。」

序列取自 wiki 6.4.3 / 6.4.5 / 6.4.7，并与驱动自己的连接路径
（`host_sdk_sample.cpp:1687-1915`）逐条对齐：

| 步 | 调用 | 依据 |
|---|---|---|
| 1 | `lidar_stop_stream(dev, SLAM)` | 失败不致命：首次切换时流本就没起来 |
| 2 | `lidar_set_mode(dev, RAW)` | wiki 6.4.5「切到传感器模式，关闭内部算法」 |
| 3 | `lidar_set_mode(dev, SLAM)` | wiki 6.4.3「使能内部算法」 |
| 4 | `set_custom_parameter("map_mode", n)` | 0 里程计 / 1 建图 / 2 重定位 |
| 5 | 建图：`save_map=0`；重定位：`set_relocalization_map` + `init_pos` | 驱动连接时也这么做（:1714-1774），漏了第一次存图会是 no-op |
| 6 | `lidar_start_stream(dev, SLAM, odr)` | |
| 7 | 按 yaml 开关逐个 `activate/deactivate_stream_type` | 复刻 :1891-1915 |

失败时 `failed_stage` 精确指出断在哪一步 —— 上机调试时这比一个笼统的
`success: false` 有用得多。步 6 失败会把设备留在「流已停」状态，
result message 直接写明「re-run switch_mode 可恢复」。

### 2.3 参数名的来源

`custom_map_mode` → 设备参数 `map_mode`。这条规则**任何文档都没写**，
是从 `yaml_parser.cpp:69-70` 读出来的：

```cpp
if (key.substr(0, 7) == "custom_") {
    std::string param_name = key.substr(7);   // 剥掉 custom_
```

代码里把这 6 个名字集中成常量并注明出处，避免以后再去猜：

```cpp
kParamMapMode = "map_mode";            kParamSaveMap = "save_map";
kParamAlgoReset = "algo_reset";        kParamInitPos = "init_pos";
kParamInitSearchRadius = "init_pose_search_radius";
kParamInitMaxRotDeg = "init_pose_max_rot_deg";
```

### 2.4 与遗留 `/tmp` 通道共存

驱动的 `process_command_file()` 仍然在跑，`./set_param.sh save_map 1` 依然有效。
两条路都会去动 `g_map_transfer_in_progress`，所以：

- `DeviceContext` 暴露该标志的读写器，Action 在 goal 阶段就检查它，
  发现遗留通道正在传输就**拒绝** goal；
- `SaveMap` 的 RAII guard 带 `armed` 位，只有自己确实置位过才在退出时清零，
  绝不会误清别人置的标志。

### 2.5 SetInitPose 的时序诚实性

设备只在算法启动时消费 `init_pos`（重定位指南「调用时机」一节）。
所以流已经在跑时调用本 service，值只是**暂存**。响应里用
`applied_immediately` 明确区分，message 直接告诉调用方要用 `switch_mode` 才生效。
判据是 `lidar_get_device_state()` 是否为 `LIDAR_DEVICE_STREAMING`。

---

## 3. odin1_tf_adapter 设计

### 3.1 数据源选择

| 输出 | 数据源 | 为什么不用 TF |
|---|---|---|
| `odom → base_link` | `/odin1/odometry_highfreq`（~400 Hz） | 驱动 TF 只有 ~10 Hz；厂商 FAQ Q4.4 明确指出高速场景要用高频 TF |
| `imu → lidar`、`lidar → camera` | `/odin1/wiwc` | wiwc 直接携带 4×4 外参原始矩阵，比从 TF 反推更准也更简单 |
| `map → odom` | `/odin1/tf_raw` | **只有 TF 有**：`SLAM_ODOMETRY_TF` 类型不发任何 topic |

`wiwc` 的解码依据（`host_sdk_sample.h` `publishWiwc`）：
`pose.covariance[0..15]` = T_CL（camera←lidar），`twist.covariance[0..15]` = T_IL（imu←lidar），
行主序，底行被强制为 `[0 0 0 1]`。因此 `lidar→camera = T_CL⁻¹`，
这与驱动自己算 `T_wc = T_wi · T_base_lidar · T_CL⁻¹` 完全一致。

设备下发的旋转矩阵未必严格正交，代码统一转四元数再归一化后才进 TF。

### 3.2 输出树

```
map ──(反转自驱动的 odom→map)── odom ──(odometry_highfreq)── base_link
                                                                 │ (static, base_to_imu 参数)
                                                                imu ──(wiwc T_IL)── lidar ──(wiwc T_CL⁻¹)── camera_0
```

`base_to_imu` 默认单位阵。**上机第一件事就是实测填它**，否则整条链带固定偏置。

### 3.3 未定位时怎么办

`map_odom_fallback` 三选一：

| 值 | 行为 | 适用 |
|---|---|---|
| `identity`（默认） | 发单位阵 `map→odom`，树保持连通，`LocalizationStatus.identity_fallback = true` | 调试、可视化 |
| `hold` | 持续补发最后一次修正，时间戳刷新 | 短暂丢失容忍 |
| `none` | 不发，`map` 断开 | **自主导航推荐**：规划器不可能把单位阵误当成真定位 |

### 3.4 为什么 map→odom 要按 50 Hz 补发

ROS2 Jazzy 的 tf2 严格时间戳查找、不外推 —— 这正是厂商用
`tf_extra_publish_rate` 绕过的同一个问题。若 `map→odom` 只在重定位更新时才发，
任何 map 系查找都会因为找不到覆盖区间而失败。所以它挂在 odometry 回调上按
`map_odom_rate_hz` 节流补发，且**用 odometry 的时间戳**，保证与 `odom→base_link`
天然同源、可插值。

### 3.5 时间基准的坑

驱动默认 `use_host_ros_time: 0`，消息时间戳是**设备开机时间**，
与主机时钟不可比，且模组断电重启后会归零。所以：

- **盖时间戳**：一律用消息自带的 stamp（保持与数据同源）；
- **判新鲜度 / 限速**：一律用 `std::chrono::steady_clock`（主机侧单调时钟）。

两者严格分开。混用会导致 `use_host_ros_time` 一改配置行为就崩。

### 3.6 LocalizationStatus 的推断逻辑

驱动从不显式报告重定位成功/失败：成功就开始发 `odom→map`，失败则**静默**
退化为 fallback SLAM（此时存图还被禁用）。适配器把这个隐式信号显式化：

```
device_map_mode ∈ {0,1}                     → STATE_NO_MAP   （map 与 odom 同义，本来就没得定位）
map→odom 新鲜（age ≤ timeout）              → STATE_LOCALIZED
曾收到过但已过期                             → STATE_STALE    （疑似丢失）
device_map_mode == 2 且从未收到              → STATE_SEARCHING（正在重定位）
其余                                        → STATE_UNKNOWN
```

`device_map_mode` 通过异步调 `/odin1/get_device_state` 拿（1 Hz，单请求在途），
拿不到就降级，不影响 TF 主功能。

---

## 4. 驱动改动点（9 处锚点）

由 `odin1/odin1_control/patch/apply_driver_patch.py` 施加。已验证：
**apply → revert 与原文件逐字节一致**；重复 apply 幂等；任一锚点不唯一即拒绝写入。

| # | 文件 | 位置 | 内容 |
|---|---|---|---|
| 1 | `src/host_sdk_sample.cpp` | :53 include 块内 | `#include "odin1_control/control_server.hpp"` |
| 2 | 同上 | :101 全局区 | `g_control_server` + 设备状态缓存 4 个变量 |
| 3 | 同上 | :1043 `LIDAR_DT_DEV_STATUS` 分支 | 缓存设备状态快照（可选，缺了只是 `status.valid=false`） |
| 4 | 同上 | :2009 `main()` | 填 `DeviceContext` 并构造 `ControlServer`（约 40 行） |
| 5 | 同上 | :410 信号处理 | `g_control_server->shutdown()` |
| 6 | 同上 | :2470 无设备早退 | `g_control_server.reset()` |
| 7 | 同上 | :2516 正常退出 | `g_control_server.reset()` |
| 8 | `CMakeLists.txt` | :270 | `find_package(odin1_interfaces / odin1_control)` |
| 9 | 同上 | :302 | 追加一次 `ament_target_dependencies(host_sdk_sample ...)` |

ROS1 分支不受影响：hunk 1/4/5/6/7 都落在 `#ifdef ROS2` 内，
hunk 2 的缓存变量与 hunk 3 只用标准库类型，8/9 在 CMake 的 ROS2 分支里。

**上游更新后**：先 `--revert`，`git pull`，再 `--check`。锚点若失效脚本会
指名道姓告诉你哪一处，不会静默错位。

---

## 5. 已做的静态校验（未编译，但不是没验）

| 校验 | 方法 | 结果 |
|---|---|---|
| Python 语法 | `ast.parse` 全部 3 个 .py | 通过 |
| ROS2 IDL 合法性 | 自写检查器：段数、类型存在性、字段 snake_case、常量 UPPER | 8 个接口文件全通过 |
| C++ 括号配平 | 去注释/去字符串后统计 `{}()[]` | 6 个文件全平衡 |
| **SDK 调用正确性** | 把 `control_server.cpp` 里每个 `lidar_*` 调用与真实 `lidar_api.h` 声明对照**函数名 + 参数个数** | 10 个函数全对上；12 个枚举/类型全部存在 |
| 头/实现一致性 | 声明的成员函数是否都有定义 | ControlServer 21/21，TfAdapterNode 全覆盖 |
| 补丁往返 | 在真实 clone 上 apply → revert → `diff -r` | 逐字节一致 |

**没做的**：编译、链接、运行。首次编译预计要处理的：rclcpp 在 Humble/Jazzy 之间
`create_service` 的 QoS 重载差异（已选用两版都有的 `rmw_qos_profile_services_default`，
Jazzy 会有 deprecation 警告，故意没开 `-Werror`）；`tf2/LinearMath/*.h`
在 Jazzy 上是 `.hpp` 的兼容 shim。

---

## 6. 下一步

1. **拿到硬件**：按 `odin1/README.md` §5 的 13 步清单逐项验证。
2. **补 Nav2 集成包**（方向二后半）：点云 → costmap、用设备重定位替代 AMCL、
   `/initialpose` → `set_init_pose` 桥接。TF 层已经就位，这一步没有阻塞项。
3. **补单元测试**：`ControlServer` 的 `DeviceContext` 是纯 `std::function`，
   可以注入假设备做无硬件测试 —— 这是当初选 lambda 桥接而非 `extern` 的额外收益。
