# odin1 — ROS 2 控制层与 TF 适配层

面向 Odin1 空间记忆模组的二次开发覆盖包。解决第一阶段调研中定位的两个最大障碍：

| 问题 | 本包的解法 |
|---|---|
| 运行时控制只能 `echo "set k v" > /tmp/odin_command.txt`，只支持 int、无返回值、无状态查询、无法编排 | `odin1_control`：6 个标准 Service / Action |
| TF 树 `odom→map` 方向与 REP-105 相反、没有 `base_link`、frame 名无前缀 → 接不进 Nav2 | `odin1_tf_adapter`：重建合规 TF 树 |

> **状态：已完成编码、发布前架构审计与静态校验；尚未完整编译、未接硬件。**
> 设计决策见 `docs/odin1/02-phase2-design.md`，审计与加固见
> `docs/odin1/03-phase2-audit.md`（11 项修复，含 3 个会导致进程崩溃/挂死的问题）。

---

## 1. 包结构

```
odin1/
├── odin1_interfaces/          纯接口包（2 msg + 4 srv + 2 action）
├── odin1_control/             静态库，链进厂商驱动进程内
│   ├── include/odin1_control/
│   │   ├── device_context.hpp   驱动全局变量 → 控制层的桥
│   │   └── control_server.hpp   服务/动作实现
│   ├── src/control_server.cpp
│   └── patch/apply_driver_patch.py   驱动改造脚本（可校验/可回滚）
├── odin1_tf_adapter/          独立节点，REP-105 TF + 定位状态
└── launch/odin1_full.launch.py  驱动(TF 重映射) + 适配器
```

### 为什么 `odin1_control` 是「库」而不是「节点」

Odin1 的 USB 设备句柄是**进程私有**的：libusb 不允许两个进程同时持有
（厂商 FAQ 5.12，`LIBUSB_ERROR_BUSY`）。因此任何控制接口都**必须**跑在
`host_sdk_sample` 进程里。本包把控制逻辑做成静态库，用一个可校验、可回滚的
脚本注入驱动，改动面控制在 11 处锚点。

---

## 2. 接口一览

### Service / Action

| 名称 | 类型 | 说明 |
|---|---|---|
| `/odin1/save_map` | **action** `SaveMap` | 存图并拉回主机。阻塞可达 120 s，所以是 action |
| `/odin1/switch_mode` | **action** `SwitchMode` | 里程计/建图/重定位切换。内部执行 stop→RAW→SLAM→配置→start→激活流 7 步，逐步反馈 |
| `/odin1/load_map` | service `LoadMap` | 上传重定位地图（仅传输，激活要用 switch_mode） |
| `/odin1/set_init_pose` | service `SetInitPose` | 重定位初值，直接吃 RViz `/initialpose` 的消息类型 |
| `/odin1/reset_algo` | service `ResetAlgo` | 让 SLAM 重新初始化 |
| `/odin1/get_device_state` | service `GetDeviceState` | 连接/流状态、map_mode、固件版本、设备健康快照 |

### Topic

| 名称 | 类型 | 来源 |
|---|---|---|
| `/odin1/device_status` | `DeviceStatus` | odin1_control，1 Hz（温度/CPU/内存/各路实际帧率） |
| `/odin1_tf_adapter/localization_status` | `LocalizationStatus` | odin1_tf_adapter，5 Hz |
| `/tf`, `/tf_static` | — | **仅** odin1_tf_adapter 发布 |
| `/odin1/tf_raw` | `tf2_msgs/TFMessage` | 驱动被重映射后的原始 TF |

---

## 3. 编译

```bash
mkdir -p ~/odin1_ws/src && cd ~/odin1_ws/src

# 厂商驱动
git clone https://github.com/manifoldsdk/odin_ros_driver.git

# 本覆盖包（把 odin1/ 下三个包放进 src/）
cp -r <this-repo>/odin1/odin1_interfaces  .
cp -r <this-repo>/odin1/odin1_control     .
cp -r <this-repo>/odin1/odin1_tf_adapter  .

# 改造驱动：先干跑校验，确认 11 处锚点都在
python3 odin1_control/patch/apply_driver_patch.py --driver ./odin_ros_driver --check
python3 odin1_control/patch/apply_driver_patch.py --driver ./odin_ros_driver

cd ~/odin1_ws
source /opt/ros/humble/setup.bash
colcon build --packages-select odin1_interfaces odin1_control odin1_tf_adapter
colcon build --packages-select odin_ros_driver     # 必须在 odin1_control 之后
source install/setup.bash
```

回滚驱动改动：`python3 .../apply_driver_patch.py --driver ./odin_ros_driver --revert`
（已验证 apply → revert 与原始文件**逐字节一致**）。

**依赖说明**：`odin1_control` 需要厂商头文件 `lidar_api.h`。CMake 会自动在
`../odin_ros_driver/include` 找；找不到会**报错终止**并给出提示，不会退化成
自带一份可能过期的声明——ABI 漂移在硬件上表现为难以定位的 USB 故障，宁可编译期炸掉。
路径特殊时用 `--cmake-args -DODIN1_SDK_INCLUDE_DIR=/abs/path`。

---

## 4. 运行

```bash
export ROS_LOCALHOST_ONLY=1          # 厂商 FAQ 5.8：复杂网络会导致设备掉线
ros2 launch ./odin1/launch/odin1_full.launch.py
```

launch 做的关键一件事：给驱动节点加 `-r /tf:=/odin1/tf_raw`。
**这一步不能省** —— 驱动的 `odom→map` 分支没有开关（`host_sdk_sample.h:1534`
不受 `send_odom_baselink_tf` 控制），如果驱动仍写 `/tf`，`map` 会有两个父节点，
tf2 直接报环。

### 典型流程

```bash
# 建图
ros2 action send_goal /odin1/switch_mode odin1_interfaces/action/SwitchMode \
  "{map_mode: 1, restart_stream: true}" --feedback

# ...走完场景，存图（--feedback 可看进度）
ros2 action send_goal /odin1/save_map odin1_interfaces/action/SaveMap \
  "{dest_dir: '/home/user/maps', file_name: 'office.bin'}" --feedback

# 重定位（上传 + 初值 + 激活，一步完成）
ros2 action send_goal /odin1/switch_mode odin1_interfaces/action/SwitchMode \
  "{map_mode: 2, map_path: '/home/user/maps/office.bin',
    use_init_pose: true,
    init_pose: {position: {x: 0.0, y: 0.0, z: 0.0},
                orientation: {x: 0.0, y: 0.0, z: 0.0, w: 1.0}},
    search_radius_m: 4.0, max_rot_deg: 180.0}" --feedback

# 看定位状态
ros2 topic echo /odin1_tf_adapter/localization_status

# 异常恢复
ros2 service call /odin1/reset_algo odin1_interfaces/srv/ResetAlgo
```

### 把 RViz 的「2D Pose Estimate」接到重定位初值

`SetInitPose.Request.pose` 就是 `geometry_msgs/PoseWithCovarianceStamped`，
所以一行 relay 即可：

```bash
ros2 run topic_tools relay_field /initialpose /odin1/set_init_pose \
  odin1_interfaces/srv/SetInitPose '{pose: m}'   # 或写 3 行 Python 节点
```

---

## 5. 首次上机验证清单

按顺序做，每步都有明确的通过判据。

| # | 步骤 | 通过判据 |
|---|---|---|
| 1 | `lsusb \| grep 2207:0019` | 设备枚举 |
| 2 | 起 launch | 日志出现 `[odin1_control] ready on a dedicated 3-thread executor` |
| 3 | `ros2 service list \| grep odin1` | 4 个新 service + 原有 4 个 AE/AWB |
| 4 | `ros2 action list` | `/odin1/save_map`、`/odin1/switch_mode` |
| 5 | `ros2 service call /odin1/get_device_state ...` | `connected: true`、`state_text: streaming`、版本号非空、`status.valid: true` |
| 6 | `ros2 run tf2_tools view_frames` | 树为 `map → odom → base_link → imu → lidar → camera_0`，**无环、无孤儿** |
| 7 | `ros2 topic hz /tf` | 约等于 `odometry_highfreq` 频率（默认不限速） |
| 8 | RViz Fixed Frame 设 `map`，加 `/odin1/cloud_raw` | 点云正常显示（frame `lidar` 在树内） |
| 9 | `ros2 topic echo /odin1_tf_adapter/localization_status` | 里程计模式下 `state: 1 (STATE_NO_MAP)` |
| 10 | 建图 → `switch_mode {map_mode: 1}` | feedback 7 步依次出现，`success: true` |
| 11 | `save_map` action | `success: true`，文件落盘且大小合理 |
| 12 | `switch_mode {map_mode: 2, map_path: ...}` | 成功后 `localization_status.state` 从 2(SEARCHING) 变 3(LOCALIZED)，`identity_fallback` 变 false |
| 13 | 拔电重插 | 驱动重连后 service/action 仍可用（控制层生命周期跟随进程，不跟随设备） |

### 上机前必须改的一个参数

`odin1_tf_adapter/config/tf_adapter.yaml` 的 `base_to_imu` 默认是单位阵，
意思是 `base_link` 与模组 IMU 重合 —— 这只在实验台上成立。
**装到车/机器人上必须实测填入**，否则所有 `base_link` 位姿都带一个固定偏置。

---

## 6. 测试

```bash
colcon test --packages-select odin1_control odin1_tf_adapter
colcon test-result --verbose
```

`odin1_control` 是静态库且不链接厂商 `.a`，`lidar_*` 符号一直未定义到最终链接为止：
驱动用厂商 `.a` 解析，测试用 `test/mock/odin_sdk_mock.cpp` 解析。
**生产代码里没有任何测试接缝，跑的就是发货的那批 .o。** Mock 先 include 真实
`lidar_api.h`，所以签名由编译器校验。

覆盖：设备断开 / busy / 超时 / 双 Goal / 存图中退出 / 重定位失败 / 重连换句柄 /
7 步时序断言；外加 `test_map_odom_policy` 穷举 TF 安全策略。
详见 `docs/odin1/03-phase2-audit.md`。

---

## 7. 已知限制（诚实清单）

- **`switch_mode` 会重启数据流**。设备不支持在线切模式（厂商 wiki 6.6），
  所以必然有几百 ms 到数秒的数据中断，且 odom 从原点重开。
  下游应把一次成功的 `switch_mode` 当作**位姿硬跳变**处理。
- **`save_map` 不可取消**。`lidar_save_map()` 是单次同步调用，SDK 没有中断点；
  取消请求被明确拒绝，而不是假装接受后留下一个没人收的地图。
- **`load_map` 单独调用不会激活重定位**。设备只在算法启动时读取地图，
  这是设备行为不是本层限制；要原子完成请用 `switch_mode`。
- **`frame_prefix` 非空时必须同时跑 `odin1_frame_retag_node`**，
  否则驱动消息里写死的 `lidar` / `odom` frame 会指向不存在的坐标系。
  默认空前缀，开箱即用、零拷贝。
- **重定位未成功时 `map` 坐标系是断开的**（`map_odom_fallback: auto`，默认）。
  RViz 里把 Fixed Frame 设成 `map` 会看不到东西——这是有意的，
  以前那个"能显示"的状态返回的是 odom 位姿冒充 map 位姿。
  `localization_status.state_text` 会说明原因。
- **`save_map` 超过退出宽限期时，进程以退出码 75 立即终止**。`lidar_save_map()` 无中断点，
  信号处理器只能有界等待 3 秒；超时后走 `_exit(75)` —— **不碰 SDK、不跑析构**，
  因为继续 teardown 会在活跃传输之上拆掉 SDK。模组可能仍在出流，
  下次连接会重新枚举；若报设备忙，上电重启一次即可。
- **存图中拔插设备，本次重连会被跳过**。驱动即将回收句柄而控制层还在 SDK 里时，
  屏障宁可跳过 attach 也不换句柄。日志会提示：等操作结束后重新插拔。
- **未完整编译验证**。ROS-free 的部分（TF 安全策略、Mock 签名）已真实编译并运行通过；
  依赖 rclcpp 的两个 .cpp 与集成测试尚未编译。详见 `docs/odin1/03-phase2-audit.md` §4。
