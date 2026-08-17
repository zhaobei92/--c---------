# Snapshot of lidar_device_status_t (LIDAR_DT_DEV_STATUS stream).
#
# Source of truth: odin_ros_driver/include/lidar_api_type.h  lidar_device_status_t
# The driver receives this at ~1 Hz on the data callback; odin1_control caches
# the latest sample and serves it here.

builtin_interfaces/Time stamp    # host time at which the sample was cached
bool valid                       # false = no LIDAR_DT_DEV_STATUS received yet

float64 uptime_seconds

# --- SoC thermals (deg C) ---
int32 package_temp
int32 cpu_temp
int32 center_temp
int32 gpu_temp
int32 npu_temp

# --- load ---
int32[] cpu_use_rate             # 8 cores, percent
int32 ram_use_rate               # percent

# --- per-sensor rates: configured vs actually transmitted ---
int32 rgb_configured_odr
int32 rgb_tx_odr
int32 dtof_configured_odr
int32 dtof_tx_odr
int32 dtof_subframe_odr          # NOTE: this is a RATE, not a microsecond interval
                                 # (the SDK header comment is wrong; see official FAQ Q4.6)
int16 dtof_tx_temp
int16 dtof_rx_temp
int32 imu_configured_odr
int32 imu_tx_odr

# --- algorithm output rates ---
int32 slam_cloud_tx_odr
int32 slam_odom_tx_odr
int32 slam_odom_highfreq_tx_odr
