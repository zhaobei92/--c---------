# Odin1 开发指南

> 面向二次开发的源码级调研笔记。基于官方仓库源码 + 官方 wiki 实测阅读整理，
> 不是官网文案的转述。所有结论均给出源码位置（`文件:行号`），可复核。
>
> 调研时间：2026-08 · 驱动版本 `v0.14.1`（CHANGELOG 20260809）· 要求固件 ≥ `0.13.0`
> 状态：**纯资料/源码调研，未接硬件、未配环境、未编译。**

---

## 0. 一页速览

| 问题 | 结论 |
|---|---|
| Odin1 是什么 | Manifold Tech（MindPalace）「空间记忆模组」：dTOF LiDAR + RGB + IMU + **板载 SLAM**（MindSLAM™）一体机 |
| SDK 开源吗 | **半开源**。C API 头文件 + ROS 驱动源码开源（Apache-2.0）；**SDK 实现是预编译静态库**，SLAM 算法跑在设备内部，均不开源 |
| 唯一通信方式 | **USB 3.0**（VID `2207` / PID `0019`），libusb + 厂商私有协议。无网口/无 ROS 原生接口 |
| 主机侧接口 | 一套 C API（`lidar_api.h`，43 个函数）+ 一个 ROS1/ROS2 参考节点 |
| 二次开发落点 | ① 直接用 ROS 话题（最快）② 直接链 `liblydHostApi_*.a` 写自己的节点（最灵活）③ 改设备内算法（**不可能**） |
| 最大的坑 | TF 树 `odom→map` **方向与 REP-105 相反**；控制面走 `/tmp` 文件而非 ROS Service；点云/位姿是**定点整数**需手动换算 |

---

## 1. 官方资料清单（已全部拉取核对）

### 1.1 代码仓库

| 仓库 | 内容 | 语言/许可 | 价值 |
|---|---|---|---|
| [`manifoldsdk/odin_ros_driver`](https://github.com/manifoldsdk/odin_ros_driver) | **主 SDK + ROS1/ROS2 驱动**。含 `lidar_api.h`、`lidar_api_type.h`、预编译 `liblydHostApi_{amd,arm}.a`、参考节点 `host_sdk_sample` | C++ / Apache-2.0 | ★★★★★ 唯一必读 |
| [`ManifoldTechLtd/wiki`](https://github.com/ManifoldTechLtd/wiki) | 官方文档站源码（[在线版](https://manifoldtechltd.github.io/wiki/Odin1/Cover.html)）：数据输出、坐标系/内外参、建图重定位调用流程、时间同步、FAQ(464 行)、技术参数 | Markdown | ★★★★★ |
| [`ManifoldTechLtd/Odin-Nav-Stack`](https://github.com/ManifoldTechLtd/Odin-Nav-Stack) | 基于 Odin1 的**开源导航栈**（Unitree Go2）：NeuPAN 局部规划、YOLO 语义导航、VLM 场景理解、pcd2pgm、fake360 | C++/Python, ROS1 Noetic | ★★★★ 最佳集成范例 |
| [`ManifoldTechLtd/SRU-Odin`](https://github.com/ManifoldTechLtd/SRU-Odin) | 无图导航（ETH RSL SRU 论文 RL 策略落地），用 Odin1 一颗替掉 ZED-X + DLIO | Python / MIT | ★★★ 思路参考 |
| [`ManifoldTechLtd/NeuPAN`](https://github.com/ManifoldTechLtd/NeuPAN) | NeuPAN fork（分支 `odin-stack-fixes`），加了 stuck-escape 与调参 | Python | ★★ |
| [`ManifoldTechLtd/MT-Real2Sim-Tutorial`](https://github.com/ManifoldTechLtd/MT-Real2Sim-Tutorial) | 真实场景 → Isaac Sim 仿真 | Python | ★★ |

### 1.2 wiki 里藏着的二进制资产（容易漏，很有用）

路径 `docs/odin_series/odin1/assets/`：

| 文件 | 说明 |
|---|---|
| `code/map_to_ply_amd64` / `_arm64` | **官方地图解析工具**：把重定位地图 `loop_map.bin`（`MAPV0001` 格式）转成 binary-LE PLY 点云。x86_64 / aarch64 双版本，glibc 2.31 |
| `code/pcd_publisher.py` | PCD 发布示例 |
| `code/rosbag2_qos.yaml` | 高频话题录制 QoS 覆写（见 §8.3） |
| `software/OdinViewer0.1.0.7z` | Windows GUI 上位机（26 MB） |
| `pdf/*.pdf` | 快速启动、重定位地图获取手册、OdinViewer 手册、固件升级工具说明（中英各一份） |

### 1.3 关键判断：什么开源、什么不开源

```
✅ 开源  lidar_api.h / lidar_api_type.h        —— 完整 API 契约 + doc 注释
✅ 开源  host_sdk_sample.{h,cpp} 等 ROS 驱动    —— 12k 行参考实现
✅ 开源  Odin-Nav-Stack 全部上层应用
❌ 闭源  liblydHostApi_amd.a / _arm.a          —— USB 协议栈 + 协议解析（预编译）
❌ 闭源  设备端 lydapp / MindSLAM              —— SLAM、回环、重定位、地图格式（跑在设备 SoC 内）
```

**结论：算法层动不了，但主机侧的一切（数据处理、控制编排、上层应用）完全可控。**

---

## 2. 软件架构

```
┌──────────────────────── Odin1 设备（Rockchip SoC，IP66，约 280 g）────────────────────────┐
│  dTOF 发射/接收 ──┐                                                                       │
│  RGB 全局快门相机 ─┼──→ lydapp ──→ MindSLAM（里程计 / 建图+回环 / 重定位）                 │
│  IMU (400 Hz)  ──┘         │            └─ 地图文件 .bin（MAPV0001）落在设备内部           │
│                            └─ ae_control（ISP，UDP loopback）                              │
└────────────────────────────────────┬──────────────────────────────────────────────────────┘
                                     │  USB 3.0 · VID 2207 / PID 0019 · 私有协议 + 心跳
┌────────────────────────────────────┴──────────────────────────────────────────────────────┐
│ 主机  liblydHostApi_{amd,arm}.a   （libusb 异步传输 / 命令通道 / 文件通道 / NTP-like 对时） │
│                                    ↕  lidar_api.h  (extern "C")                            │
│       host_sdk_sample 节点  ── 解定点 / NV12→BGR / 去畸变 / 渲染 / 打 TF / 写 csv & 录制    │
│                                    ↕  ROS1 或 ROS2（同一份源码 #ifdef ROS2 双编）           │
│       pcd2depth · cloud_reprojection · image_overlay（三个纯主机侧后处理 demo 节点）        │
└───────────────────────────────────────────────────────────────────────────────────────────┘
```

### 2.1 三条通信面

| 面 | 载体 | 说明 |
|---|---|---|
| **数据面** | SDK 内部线程 → 回调 `lidar_data_callback_t` | 所有传感器/算法数据走同一个回调，用 `lidar_data_t.type` 分流 |
| **控制面** | 同步 USB control 命令（内部互斥锁串行化） | 模式、自定义参数、AE/AWB、版本、标定 |
| **文件面** | 大文件分块传输 | 标定文件、重定位地图上传、建图结果下载、图像 mask、加密日志 |

### 2.2 回调契约（写代码前必看，`include/lidar_api.h:80-96`）

- 回调由 **SDK 内部线程**发起，不同数据类型可能是**不同线程** → 用户代码必须线程安全。
- **`pAddr` 只在回调期间有效**，要留就必须拷贝，回调返回后解引用 = UB。
- 回调里不能阻塞，否则丢帧/抖动。参考实现的做法：IMU 进队列交给专用线程（`src/host_sdk_sample.cpp` IMU 分支 → `start_imu_thread()`）。

---

## 3. 核心 C API（`include/lidar_api.h`）

### 3.1 标准生命周期

```c
lidar_system_init(device_cb)                   // 启动发现，设备插拔回调
  └─ device_cb(info, attach=true)
       lidar_create_device(&info, &handle)
       lidar_get_version(handle, &ver)         // 版本太低会直接失败，见 FAQ 5.4
       lidar_get_calib_file(handle, path)      // 拉每台机器独有的 calib.yaml
       lidar_enable_encrypted_device_log(...)  // 可选，必须在 open 之前
       lidar_open_device(handle)
       lidar_set_depth_parameter(handle, &odr) // 必须在开流前
       lidar_set_mode(handle, LIDAR_MODE_SLAM) // RAW=仅原始数据 / SLAM=启用板载算法
       lidar_set_custom_parameter(handle, "map_mode", &v, 4)   // 0 里程计 / 1 建图 / 2 重定位
       lidar_set_relocalization_map(handle, "/abs/map.bin")    // 仅 map_mode=2
       lidar_register_stream_callback(handle, cb_info)
       lidar_start_stream(handle, type, &dtof_subframe_odr)
       lidar_activate_stream_type(handle, LIDAR_DT_xxx)        // 逐类型开关
  ...
lidar_stop_stream / close / destory / lidar_system_deinit
```

> `activate_stream_type` ≠ `start_stream`：前者配置设备使能哪些流，后者才真正开始传输
> （`lidar_api.h:62-78`）。参考实现是先 `start_stream` 再逐类型 `activate/deactivate`
> （`src/host_sdk_sample.cpp:1865-1915`）。

### 3.2 API 分组速查

| 组 | 函数 |
|---|---|
| 系统/设备 | `lidar_system_init` `lidar_system_deinit` `lidar_create_device` `lidar_destory_device` `lidar_open_device` `lidar_close_device` `lidar_reset_usb` `lidar_get_device_state` |
| 数据流 | `lidar_register_stream_callback` `lidar_unregister_stream_callback` `lidar_start_stream` `lidar_stop_stream` `lidar_activate_stream_type` `lidar_deactivate_stream_type` `lidar_set_mode` `lidar_set_depth_parameter` |
| 参数 | `lidar_set_custom_parameter` `lidar_get_custom_parameter` `lidar_get_version` `lidar_get_calibration` `lidar_set_calibration` `lidar_get_calib_file` `lidar_log_set_level` |
| **地图** | `lidar_set_relocalization_map` `lidar_get_mapping_result` **`lidar_save_map`**（一站式同步：触发→轮询→拉取） |
| 相机 ISP | `lidar_get_ae_info` `lidar_set_ae_param` `lidar_get_awb_info` `lidar_set_awb_param` |
| IMU 整形 | `lidar_enable_imu_smooth_sending` `lidar_set_imu_smooth_frequency`（专用高优先级线程按 400 Hz 等间隔发，降抖动） |
| 其它 | `lidar_set_image_mask`（上传 1600×1296 mask 屏蔽自遮挡）`lidar_send_user_data`（**旁路通道**，≤8 MiB 透传进设备 SLAM 共享内存）`lidar_enable_encrypted_device_log` |

**错误码**：`0` 成功；`-1` 通用失败；`-2` 参数非法/传输占用；`-3` 设备未找到；`-4` 超时；`-5` 资源分配失败。
AE/AWB 额外用**正数**表示设备侧错误：`400/401/402/403(越界，最常见)/404/405/0xFF`。

### 3.3 `custom_parameter` 名字表（设备侧算法开关，官方文档 §6）

| 名字 | 类型 | 含义 |
|---|---|---|
| `map_mode` | int | `0` 里程计 / `1` 建图(含回环，可存图) / `2` 重定位 |
| `save_map` | int | 置 1 触发设备内存图；设备完成后**自己置回 0**，主机轮询该值判完成 |
| `init_pos` | float[7] | 重定位初值 `[x,y,z,qx,qy,qz,qw]`，米 + 归一化四元数，**必须在 `start_stream` 前设** |
| `init_pose_search_radius` | float | 初值搜索半径（米，建议 ≤10） |
| `init_pose_max_rot_deg` | float | 初值最大旋转搜索（度，≤180） |
| `algo_reset` | int | 置 1 让 SLAM 重新初始化（碰撞/位姿跳变后用） |

> **命名规则**：YAML 里写 `custom_xxx`，驱动剥掉 `custom_` 前缀后作为参数名下发
> （`src/yaml_parser.cpp:69-70`）。所以 `custom_map_mode` → 设备参数 `map_mode`。
> 这条规则文档里没直说，是从源码读出来的——**自己加新参数时按这个约定走即可**。

---

## 4. 数据类型与线格式（最容易踩的一段）

统一容器：`lidar_data_t { type; capture_Image_List_t stream; }`，
`stream.imageList[0..3]` 每个是 `{length, sequence, timestamp, interval, pAddr, width, height}`。

| `type` | 通道 | 线格式 | **换算/陷阱** |
|---|---|---|---|
| `LIDAR_DT_RAW_IMU` | 1 | `imu_convert_data_t` | accel m/s²、gyro rad/s、`stamp` ns、400 Hz。float，无需换算 |
| `LIDAR_DT_RAW_DTOF` | 4 | `[0]` depth `float`(米)<br>`[1]` xyz `float[3]` 交织<br>`[2]` confidence `uint8`<br>`[3]` intensity `uint16` | 头文件标 **256×192**；技术参数表标深度分辨率 **240×180** —— 以 `imageList[i].width/height` 运行时值为准 |
| `LIDAR_DT_RAW_RGB` | 1 | NV12（Y 平面 + UV 平面） | 头文件标 1536×1280，而 `calib.yaml` 标定是 **1600×1296** —— 以设备下发的 calib 为准 |
| `LIDAR_DT_SLAM_CLOUD` | 1 | `slam_cloud_point_t`（7×`int32`，28 B/点） | ⚠ **xyz 是 0.1 mm 定点**：`米 = xyz * SLAM_CLOUD_XYZ_TO_M (1e-4)`；rgba 各只有低字节有效 |
| `LIDAR_DT_SLAM_ODOMETRY` / `_HIGHFREQ` / `_TF` | 1 | `ros_odom_convert_complete_t` | ⚠ **`pos` 是 int64 μm（÷1e6）**，**`orient` 是 int64 ×1e6 的四元数（÷1e6）**；另带 `pose_cov[36]`/`twist_cov[36]` |
| `LIDAR_DT_SLAM_WIWC` | 1 | 同上结构，但**语义被复用** | ⚠ 不是位姿！`pose_cov[0..15]` = **T_CL**（camera←lidar），`twist_cov[0..15]` = **T_IL**（imu←lidar），行主序 4×4。驱动靠它拿实时外参（`include/host_sdk_sample.h:1185+`） |
| `LIDAR_DT_DEV_STATUS` | 1 | `lidar_device_status_t` | SoC/CPU/GPU/NPU 温度、8 核占用、内存、各路 configured/tx 帧率。做健康监控就靠它 |
| `LIDAR_DT_NTP` | 1 | `ptp_sync_data_t{delay, offset}` | NTP-like 对时结果，30 帧滑窗平滑 |

### 4.1 时间戳语义（官方 FAQ §四，很关键）

- **点云 `header.stamp` = 整帧起始时刻**。dTOF 分 32 组逐组曝光（类卷帘），第一组即帧头，所以第一个点 `offset_time = 0` 是**真时刻**而非未填充。
- **图像 `header.stamp` = 曝光中点**，相机是**全局快门**。
- `dtof_subframe_odr` 是**速率**不是微秒间隔 —— **头文件里 `lidar_start_stream` 的注释写错了**，官方 FAQ Q4.6 已确认，以示例代码为准。
- 设备默认用**开机时间**做时基。要和主机对齐用 `use_host_ros_time: 2`（NTP-like 软同步，公式见 wiki §12）。

### 4.2 坐标系与外参

- 三个系：**I**(IMU)、**L**(点云)、**C**(相机，OpenCV 约定 X右/Y下/Z前)。
- `T_imu_lidar` 是**固定值**：单位旋转 + 平移 `(-0.02663, 0.03447, 0.02174)` m。
- `T_camera_lidar`（`Tcl_0`）**每台机器不同**，驱动连上后写进 `calib.yaml`（运行时目录：`$ODIN_CALIB_DIR` → `$ROS_HOME/odin_ros_driver` → `~/.ros/odin_ros_driver` → `/tmp`）。
- 相机模型是**等距投影 + 全阶多项式鱼眼**（`k2..k7` + 仿射 `A11/A12/A22/u0/v0`），
  **与 OpenCV `cv2.fisheye`(Kannala-Brandt) 不等价，不能直接套用**。实现见 `include/polynomial_camera.hpp`。

---

## 5. ROS 层完整清单

### 5.1 Node（`CMakeLists.txt`，ROS1/ROS2 各一套可执行名）

| ROS2 可执行 | ROS1 可执行 | 作用 |
|---|---|---|
| `host_sdk_sample` | `host_sdk_sample` | **主驱动**：SDK 生命周期 + 全部话题 + TF + 4 个 AE/AWB service |
| `pcd2depth_ros2_node` | `pcd2depth_node` | 稀疏点云 → 稠密深度图补全 demo（**很吃算力**，默认关） |
| `cloud_reprojection_ros2_node` | `cloud_reprojection_node` | `cloud_slam` + odom + wiwc → 重投影到相机像面 |
| `image_overlay_node` | `image_overlay_node` | 重投影图与相机图 alpha 混合叠加 |

launch：`ros2 launch odin_ros_driver odin1_ros2.launch.py` / `roslaunch odin_ros_driver odin1_ros1.launch`
（两者都会一起拉起上面 4 个 node + RViz）。

### 5.2 Topic

| Topic | 类型 | frame_id | 频率 | 开关(yaml) |
|---|---|---|---|---|
| `odin1/imu` | `sensor_msgs/Imu` | `imu` | 400 Hz | `sendimu` |
| `odin1/image` | `sensor_msgs/Image` (bgr8) | — | 10/14.5 Hz | `sendrgb` |
| `odin1/image/compressed` | `CompressedImage` (jpeg 原始) | — | 同上 | `sendrgbcompressed` |
| `odin1/image/undistorted` | `Image` | — | 同上 | `sendrgbundistort` |
| `odin1/image/intensity_gray` | `Image` (mono8) | `map`⚠ | 同上 | `pubintensitygray` |
| `odin1/cloud_raw` | `PointCloud2` | **`lidar`** | 10/14.5 Hz | `senddtof` |
| `odin1/cloud_render` | `PointCloud2` (XYZRGB) | **`lidar`** | — | `sendcloudrender` |
| `odin1/cloud_slam` | `PointCloud2` (XYZRGB) | **`odom`** | — | `sendcloudslam` |
| `odin1/odometry` | `nav_msgs/Odometry` | `odom`→`imu` | ~10 Hz | `sendodom` |
| `odin1/odometry_highfreq` | `nav_msgs/Odometry` | `odom` | ~400 Hz | `sendodom` |
| `odin1/wiwc` | `nav_msgs/Odometry`（承载外参⚠） | `odom` | — | 常开 |
| `odin1/path` | `MarkerArray` | — | — | `showpath` |
| `odin1/camera_pose_visual` | `MarkerArray` | — | — | `showcamerapose` |
| `odin1/depth_img_competetion` | `Image` | — | — | `senddepth` |
| `odin1/depth_img_competetion_cloud` | `PointCloud2` | — | — | `senddepth` |
| `odin1/reprojected_image` | `Image` | — | — | `sendreprojection` |
| `odin1/overlay_image` | `Image` | — | — | `sendoverlay` |

> **README 与源码不一致**：README §4.3 写 `odin1/image_undistort`，实际代码是
> `odin1/image/undistorted`（`include/host_sdk_sample.h:1924`）。**以源码为准。**

`cloud_raw` 自定义字段：`x,y,z(float32,米) + intensity(uint8) + confidence(uint16) + offset_time(float32,秒)`。
`confidence` 典型 0~1300，建议阈值 30~35（yaml `cloud_raw_confidence_threshold`，默认 35）。

### 5.3 Service（只有 AE/AWB 4 个）

| Service | 类型 | 说明 |
|---|---|---|
| `/odin1/get_ae` | `odin_ros_driver/srv/GetAe` | 曝光/增益/ISO/亮度/收敛/环境光/fps |
| `/odin1/get_awb` | `GetAwb` | rgain/bgain/色温/收敛 |
| `/odin1/set_ae` | `SetAe` | `mode`(0自动/1手动) + `exposure_time`(1e-4~0.033 s) + `gain`(1~64) |
| `/odin1/set_awb` | `SetAwb` | `mode` + `rgain`/`bgain`(0.1~4.0) |

阻塞最长 ~10 s，正常几十 ms；手动模式**不跨重启保留**；驱动没打开设备时返回 `rc = -100`。

### 5.4 TF 树 ⚠ 重点

```
       odom  ←── 根节点
        ├── imu        （来自 SLAM_ODOMETRY，~10 Hz，受 send_odom_baselink_tf 控制）
        │    └── lidar （来自 WIWC 的 T_IL，实时外参）
        ├── camera_0   （T_wc = T_wi · T_base_lidar · T_CL⁻¹）
        └── map        ⚠⚠ 来自 SLAM_ODOMETRY_TF，重定位成功后才有
```

**两个必须知道的偏差：**

1. **`odom → map` 方向与 ROS REP-105 相反**（REP-105 是 `map → odom → base_link`）。
   源码：`include/host_sdk_sample.h:1538-1539`（ROS2）/ `1688-1689`（ROS1），
   `header.frame_id="odom"`, `child_frame_id="map"`。
   README 也确认这个设计：「话题都在 odom 系，要 map 系请自行套 odom→map 的 TF」。
   → **接 Nav2 / move_base 前必须做一层 TF 反转适配**。
2. **frame 名无前缀**（`imu` / `lidar` / `odom` / `map` / `camera_0`），多传感器系统里极易撞名。
   另外 `Odin-Nav-Stack` 的 README 还在讲 `odin1_base_link`，那是**旧版驱动**的名字，已改成 `imu` —— 跨仓库看文档时注意版本漂移。
3. `odin1/image/intensity_gray` 的 `frame_id` 被写成 `"map"`（`host_sdk_sample.h:780`），**明显是笔误**，用之前自己改。

**ROS2 Jazzy / Ubuntu 24.04 专属坑**：tf2 严格时间戳查找不外推，而点云时间戳比 odom 超前约 100 ms → RViz 里 `cloud_raw` 显示失败。解法：`tf_extra_publish_rate: 100`（起定时器按推进时间戳补发 `odom→imu` 与 `imu→lidar`）。

### 5.5 参数（`config/control_command.yaml`，全在 `register_keys:` 下）

分四类：

- **数据开关**：`sendrgb` `sendrgbcompressed` `sendrgbundistort` `sendimu` `senddtof` `sendcloudslam` `sendcloudrender` `senddepth` `sendreprojection` `sendoverlay` `pubintensitygray` `showpath` `showcamerapose`
- **模式/算法（`custom_` 前缀会下发到设备）**：`custom_map_mode` `custom_init_pos` `custom_init_pose_search_radius` `custom_init_pose_max_rot_deg` `relocalization_map_abs_path` `mapping_result_dest_dir` `mapping_result_file_name` `resetalgo`
- **传感与时间**：`dtof_fps`(100/145/290) `cloud_raw_confidence_threshold` `enable_imu_smooth` `imu_smooth_frequency` `use_host_ros_time`(0设备时间/1主机接收时间/2 NTP对齐) `strict_usb3.0_check`
- **TF / 日志 / 录制**：`send_odom_baselink_tf` `tf_extra_publish_rate` `devstatuslog` `save_log` `recorddata`（olx 格式供 MindCloud 后处理，**10 min ≈ 9.5 GB**）`sendimagemask` `image_mask_abs_path`

### 5.6 ⚠ 运行时控制面：不是 ROS Service，是 `/tmp` 文件

```bash
./set_param.sh save_map 1     # 实际做的事：echo "set save_map 1" > /tmp/odin_command.txt
```

主循环 10 Hz 轮询该文件 → 读一行 → **删文件** → 解析 `set <name> <value>` → 调
`lidar_set_custom_parameter`（`src/host_sdk_sample.cpp:439-605`，调用点 `:2508`/`:2542`）。

`save_map` 走特例：直接调一站式 `lidar_save_map()` 并**在后台线程**执行（`:537-562`），
用 `g_map_transfer_in_progress` 原子标志防重入，两次存图**至少间隔 5 秒**。

> 这是**当前架构最大的可改造点**：只支持 `int` 值、无返回、无状态查询、无法在 launch /
> 行为树里编排。见 §9 方向一。

---

## 6. 能力矩阵：能做什么、怎么拿

| 能力 | 获取方式 | 成熟度 | 备注 |
|---|---|---|---|
| **原始点云** | `odin1/cloud_raw` (`lidar` 系) | ✅ 直接可用 | **低延迟，用于近场避障**；0.4 m 内有畸变；FOV 边缘 >110° 噪点多 |
| **SLAM 点云** | `odin1/cloud_slam` (`odom` 系) | ✅ | **位姿矫正后，用于建图/定位**，无分层 |
| **彩色点云** | `odin1/cloud_render` | ✅ | 主机侧用 calib 把 RGB 贴到 raw 点云上 |
| **IMU** | `odin1/imu` 400 Hz | ✅ | 可开 SDK smooth 等间隔发送降抖动 |
| **Pose（低频）** | `odin1/odometry` ~10 Hz | ✅ | 带 pose/twist 协方差 |
| **Pose（高频）** | `odin1/odometry_highfreq` ~400 Hz | ✅ | 高速场景/TF 跟车必须用这个 |
| **里程计模式** | `custom_map_mode: 0` | ✅ | map 与 odom 同位姿；漂了用 `./set_param.sh algo_reset 1` |
| **建图（含回环）** | `custom_map_mode: 1` | ✅ | 驱动启动即自动建图并缓存 |
| **地图保存** | `./set_param.sh save_map 1` → `lidar_save_map()` | ✅ | 每次生成新文件；默认落 `{ws}/src/odin_ros_driver/map/{启动时间}/map_{时间}.bin` |
| **地图加载/重定位** | `custom_map_mode: 2` + `relocalization_map_abs_path` | ⚠ 环境敏感 | 建议起始点距原轨迹 **1 m / ±10°** 内；失败则退化为 fallback SLAM（**此时禁止存图**）并后台持续重试 |
| **指定初值重定位** | `custom_init_pos` 或 API `init_pos` | ✅ | 7 float，**必须开流前设**；可从 RViz `/initialpose` 灌入（官方给了示例代码） |
| **地图格式转换** | wiki `assets/code/map_to_ply_*` | ✅ | `.bin(MAPV0001)` → `.ply`；再往 pgm 走可用 Nav-Stack 的 `pcd2pgm` |
| **深度补全** | `senddepth: 1` → `odin1/depth_img_competetion` | ⚠ demo | 算力开销大，与 `image/undistorted` 一一对应 |
| **相机 AE/AWB 在线调** | 4 个 ROS Service | ✅ | 唯一一组正经的 ROS 服务接口 |
| **设备健康监控** | `LIDAR_DT_DEV_STATUS` / `devstatuslog` → `dev_status.csv` | ✅ | 温度/CPU/内存/各路实际帧率 |
| **外部数据注入** | `lidar_send_user_data()` ≤8 MiB | ❓ 未文档化用途 | fire-and-forget 透传进设备 SLAM 共享内存 —— **潜在的外部里程计/GNSS 融合入口，值得试探** |
| **改 SLAM 算法/地图格式** | — | ❌ 不可能 | 闭源，跑在设备内 |
| **动态切模式** | — | ❌ 不支持 | 官方明确：切模式必须先停算法、重配、重启（wiki §6.6） |

---

## 7. 关键代码索引（拿来就能跳）

| 想干什么 | 去看 |
|---|---|
| 看 API 全貌 + QuickStart 流程图 | `include/lidar_api.h:15-107` |
| 数据类型/线格式表 | `include/lidar_api_type.h:42-123` |
| 定点换算常量 | `lidar_api_type.h:179-185`（`SLAM_CLOUD_XYZ_TO_M`） |
| **主入口 `main()`** | `src/host_sdk_sample.cpp:2242-2609` |
| **设备连接回调（整条初始化链）** | `src/host_sdk_sample.cpp:1600-1960` |
| **数据分发 `lidar_data_callback`** | `src/host_sdk_sample.cpp`（按 `case LIDAR_DT_*` 找） |
| 命令文件解析 / save_map 特例 | `src/host_sdk_sample.cpp:439-605` |
| 所有 publisher 定义（ROS2/ROS1） | `include/host_sdk_sample.h:1902-2018` |
| **TF 发布（含 odom→map 反向）** | `include/host_sdk_sample.h:1531-1699` |
| WIWC 解外参 | `include/host_sdk_sample.h:1185+` |
| 高频补发 TF 定时器 | `include/host_sdk_sample.h:1933-1998` |
| YAML → 设备参数（`custom_` 剥前缀） | `src/yaml_parser.cpp:60-124, 217-233` |
| AE/AWB service 注册 | `src/host_sdk_sample.cpp:2044-2232` |
| 鱼眼相机模型 | `include/polynomial_camera.hpp` |
| 点云上色 | `src/rawCloudRender.cpp` |
| 重定位完整用法 + 编程接口示例 | `RELOCALIZATION_GUIDE.md`（642 行，中英双语，含 RViz `/initialpose` 注入代码） |

**可直接跑的 Demo**：`odin1_ros2.launch.py` / `odin1_ros1.launch`（驱动+RViz）、
`Odin-Nav-Stack` 的 `scripts/map_recording.sh`（建图落 pcd/pgm）、`map_planner/whole.launch` +
`NeuPAN/neupan/ros/neupan_ros.py`（导航）、`run_yolo_detector.sh`（语义导航）。

---

## 8. 已知坑清单

### 8.1 硬件/连接
- **必须 USB 3.0**。检测到 2.0 直接 `FATAL` 退出（`host_sdk_sample.cpp:2434-2444`）；可用 `strict_usb3.0_check: 0` 放行但 SLAM 地图传输会不可靠。
- udev 规则必配：`SUBSYSTEM=="usb", ATTR{idVendor}=="2207", ATTR{idProduct}=="0019", MODE="0666", GROUP="plugdev"`。
- 慢主机（Jetson 等）跑一段时间报 `LIBUSB_ERROR_NO_MEM` → 调大 `usbcore.usbfs_memory_mb`（默认 16 MB，建议 128）。

### 8.2 ROS2
- 复杂网络下 ROS2 广播会阻塞 publish 导致设备被判掉线 → `export ROS_LOCALHOST_ONLY=1`。
- 系统里存在多个 OpenCV 版本 → 驱动开 `sendrgb` 时直接 die。只保留一个版本。
- Ubuntu 24.04 / Jazzy 的 TF 问题 → `tf_extra_publish_rate: 100`。

### 8.3 录制
- `ros2 bag record` 默认订阅 `depth=10`，400 Hz 下只能缓 25 ms → **IMU/highfreq 静默丢帧**（SDK 侧和 `topic hz` 都看不出来）。
  必须用 `--qos-profile-overrides-path script/rosbag2_qos.yaml`（把两个高频话题 depth 提到 4000），
  外加 `-s mcap --max-cache-size` 与 `sysctl net.core.rmem_max=33554432`。ROS1 无此问题。

### 8.4 文档与源码的已知不一致（本次核对发现）
| 项 | 文档说 | 源码是 |
|---|---|---|
| 去畸变话题名 | `odin1/image_undistort` | `odin1/image/undistorted` |
| `dtof_subframe_odr` | 头文件写「微秒间隔」 | 实为**速率**（官方 FAQ Q4.6 已认错） |
| 深度分辨率 | 技术参数 240×180 | 头文件注释 256×192 |
| RGB 分辨率 | 头文件 1536×1280 | 标定文件 1600×1296 |
| TF child | Nav-Stack README 用 `odin1_base_link` | 现驱动是 `imu` |

---

## 9. 后续最值得开发的 5 个方向

> 排序依据：**刚需程度 × 可行性（不依赖闭源部分）× 复用价值**。

### 方向一：把控制面做成正规 ROS2 Service / Action（**最高优先级**）
**问题**：所有运行时控制只能 `echo "set k v" > /tmp/odin_command.txt`，只支持 int，
无返回值、无状态查询、无法在 launch/behavior tree/Nav2 里编排；`save_map` 还是后台线程异步完成。
**做什么**：
- `SaveMap.action`（带进度与最终路径）、`SetMapMode.srv`、`SetInitPose.srv`（直接吃
  `geometry_msgs/PoseWithCovarianceStamped`，对齐 RViz「2D Pose Estimate」）、
  `LoadMap.srv`（封装 `lidar_set_relocalization_map`）、`ResetAlgo.srv`、`GetDeviceState.srv`。
- 直接调已有 C API 即可，`RELOCALIZATION_GUIDE.md` 已给出 `/initialpose` → `init_pos` 的完整范例。
**难度**：低。**收益**：极高，是后面所有方向的基础设施。

### 方向二：坐标系合规适配层 + Nav2 集成包
**问题**：`odom→map` 方向反了、frame 名无前缀、`cloud_slam` 在 `odom` 系、
`intensity_gray` 的 frame 是笔误 —— 现状**接不进 Nav2**；官方 Nav-Stack 只有 ROS1 Noetic + move_base/NeuPAN。
**做什么**：
- 一个 `odin1_tf_adapter` 节点：反转并重发 `map→odom`，加可配置 `frame_prefix`，
  按 REP-105 补 `base_link` 与静态外参；
- `odin1_nav2_bringup`：点云 → `voxel_layer`/`obstacle_layer` costmap（**近场用 `cloud_raw`，
  建图定位用 `cloud_slam`**，这是官方 FAQ 明确的分工），用设备重定位替代 AMCL，
  把 `/initialpose` 桥到 `init_pos`。
**难度**：中。**收益**：极高，直接打通 ROS2 主流导航生态。

### 方向三：地图生命周期管理与格式流水线
**问题**：存图只能 shell 触发、每次生成新文件无版本管理；换地图必须**重启驱动**；
`.bin` 是私有格式，只有一个官方 CLI 能转 PLY；MindCloud Studio 目前**不支持导出编辑后的地图**。
**做什么**：
- `map_manager` 节点/服务：地图库（元数据：设备 SN、算法版本、建图时间、覆盖范围、预览图）、
  切图编排（停算法 → 配置 → 重启流，把「不支持动态切模式」这个限制封装成一次原子操作）；
- 转换流水线 `.bin → .ply →(pcl)→ .pcd → .pgm/yaml`（复用 `map_to_ply_*` 与 Nav-Stack 的 `pcd2pgm`），
  产出可直接喂 Nav2 的栅格图；
- 地图质检：覆盖率、回环数、可重定位性打分。
**难度**：中。**收益**：高，且是「产品化部署」的必经之路。

### 方向四：重定位可观测性与自动恢复
**问题**：重定位是产品核心卖点，但**环境敏感**（1 m/±10° 建议范围、静态不动难定位、
竖装难定位、隧道/白墙退化）。而驱动对外**几乎不暴露状态** —— 失败只是静静退化成 fallback SLAM
（且此时禁止存图），上层根本不知道自己在哪个状态。
**做什么**：
- `/odin1/localization_status`（`RELOCALIZED / RELOCALIZING / FALLBACK_SLAM / LOST`），
  用「`odom→map` TF 是否出现 + 时新度 + odom 与 map 位姿一致性」推断；
- 自动恢复策略：失败时按 `init_pose_search_radius` / `init_pose_max_rot_deg` 阶梯放大重试，
  触发「轻微晃动/小幅移动」提示动作（官方明确这能提高成功率），必要时调 `algo_reset`；
- 退化检测（隧道/白墙）：结合 `cloud_raw` 几何退化度 + 设备状态，提前告警。
**难度**：中高（需实机调）。**收益**：高，直接决定产品在真实场景能不能用。

### 方向五：数据录制 / 回放 / 评测基线
**问题**：现有录制两条路都不顺 —— `recorddata`(olx) 体积恐怖（10 min ≈ 9.5 GB）且绑定 MindCloud；
`ros2 bag` 默认会**静默丢高频帧**。没有基线数据集，任何算法改动都无法量化。
**做什么**：
- 一键录制脚本（内建 QoS override + mcap + 内核 buffer 调优 + 磁盘预检），
  标准 topic 集：`cloud_raw` / `imu` / `image/compressed` / `odometry_highfreq` / `wiwc` / `tf`
  （这正是官方 FAE 排障要求的组合）；
- 回放侧的「假驱动」节点：从 bag 复现全部话题与 TF，**让上层算法开发彻底脱离硬件**
  （对本次「暂不接硬件」的场景尤其有用）；
- 评测集与指标：轨迹精度（对标 ±5 cm）、重定位成功率/耗时、时间戳一致性
  （`use_host_ros_time` 三种模式对比）。
**难度**：中。**收益**：中高，是持续迭代的地基。

> 附：`lidar_send_user_data()`（≤8 MiB 透传进设备 SLAM 共享内存）是官方头文件里存在
> 但文档从未展开的旁路通道。如果它真能把外部观测（轮速/GNSS/外部里程计）喂进板载 SLAM，
> 那将是唯一能「间接影响闭源算法」的口子 —— **值得优先做一次探索性验证**。

---

## 10. 从零到跑起来（备忘，待接硬件时执行）

```bash
# 1. udev
sudo tee /etc/udev/rules.d/99-odin-usb.rules <<'EOF'
SUBSYSTEM=="usb", ATTR{idVendor}=="2207", ATTR{idProduct}=="0019", MODE="0666", GROUP="plugdev"
EOF
sudo udevadm control --reload && sudo udevadm trigger

# 2. 依赖：OpenCV>=4.2(只留一个版本) / yaml-cpp / libusb-1.0 / OpenSSL / Eigen3
# 3. 必须 clone 进 <ws>/src/ 否则编译失败
git clone https://github.com/manifoldsdk/odin_ros_driver.git <ws>/src/odin_ros_driver
cd <ws>/src/odin_ros_driver && ./script/build_ros2.sh    # ROS1: ./script/build_ros.sh

# 4. 跑（ROS2 建议先 export ROS_LOCALHOST_ONLY=1）
source <ws>/install/setup.bash
ros2 launch odin_ros_driver odin1_ros2.launch.py

# 5. 建图 → 存图
#    control_command.yaml: custom_map_mode: 1
./set_param.sh save_map 1        # 落到 map/{启动时间}/map_{时间}.bin，两次间隔 ≥5s

# 6. 重定位
#    custom_map_mode: 2 + relocalization_map_abs_path: /abs/path/map.bin
#    起始位置距原轨迹 1m/±10° 内；起来后轻晃设备提高成功率
```

系统要求：Ubuntu 20.04(Noetic/Foxy) / 22.04(Humble 推荐)；24.04(Jazzy) 非官方支持但可用（需 `tf_extra_publish_rate`）；**不支持 18.04**。

---

## 参考

- 官方文档站：<https://manifoldtechltd.github.io/wiki/Odin1/Cover.html>
- 驱动仓库：<https://github.com/manifoldsdk/odin_ros_driver>
- 导航栈：<https://github.com/ManifoldTechLtd/Odin-Nav-Stack> · [网页](https://ManifoldTechLtd.github.io/Odin-Nav-Stack-Webpage)
- 无图导航：<https://github.com/ManifoldTechLtd/SRU-Odin>
- 技术支持：`support@manifoldtech.cn`
