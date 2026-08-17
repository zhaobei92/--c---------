cmake_minimum_required(VERSION 3.8)
project(odin1_interfaces)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(builtin_interfaces REQUIRED)
find_package(std_msgs REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(action_msgs REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/DeviceStatus.msg"
  "msg/LocalizationStatus.msg"
  "srv/GetDeviceState.srv"
  "srv/LoadMap.srv"
  "srv/ResetAlgo.srv"
  "srv/SetInitPose.srv"
  "action/SaveMap.action"
  "action/SwitchMode.action"
  DEPENDENCIES builtin_interfaces std_msgs geometry_msgs action_msgs
)

ament_export_dependencies(rosidl_default_runtime)
ament_package()
