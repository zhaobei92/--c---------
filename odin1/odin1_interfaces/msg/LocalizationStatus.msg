# Localization state inferred by odin1_tf_adapter.
#
# The driver never reports relocalization success explicitly: on success the device
# starts emitting LIDAR_DT_SLAM_ODOMETRY_TF (which the driver turns into an
# odom->map TF), and on failure it silently degrades to a fallback SLAM mode.
# This message turns that implicit signal into something an upper layer can act on.

std_msgs/Header header           # stamp = time of evaluation, frame_id = map frame

uint8 STATE_UNKNOWN=0            # adapter just started, nothing observed yet
uint8 STATE_NO_MAP=1             # map_mode 0/1: map and odom coincide by design
uint8 STATE_SEARCHING=2          # map_mode 2, no map->odom seen yet (relocalizing)
uint8 STATE_LOCALIZED=3          # fresh map->odom being received
uint8 STATE_STALE=4              # map->odom was received but has gone stale (lost)
uint8 state

string state_text

bool map_odom_valid              # a map->odom transform has ever been received
float32 map_odom_age_sec         # seconds since the last one (inf if never)
bool identity_fallback           # true = adapter is publishing an identity map->odom
                                 # to keep the TF tree connected; poses are NOT in a
                                 # real map frame while this is true

geometry_msgs/Transform map_to_odom   # last published map->odom
