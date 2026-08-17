cmake_minimum_required(VERSION 3.8)
project(odin1_control)

if(NOT CMAKE_CXX_STANDARD)
  set(CMAKE_CXX_STANDARD 17)
endif()
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  # No -Werror on purpose: rclcpp's create_service(rmw_qos_profile_t) overload is
  # deprecated on Jazzy but is the only one available on Humble, and we support both.
  add_compile_options(-Wall -Wextra)
endif()

find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(rclcpp_action REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(builtin_interfaces REQUIRED)
find_package(odin1_interfaces REQUIRED)

# ---------------------------------------------------------------------------
# Vendor SDK header
#
# This library calls the Odin1 host SDK directly, so it needs lidar_api.h /
# lidar_api_type.h from odin_ros_driver. We deliberately do NOT vendor a copy of
# the prototypes: a silent ABI drift between our declarations and the shipped
# liblydHostApi_*.a would corrupt the USB control path in ways that only show up
# on hardware. Compiling against the real header makes any drift a build error.
#
# The .a itself is NOT linked here. odin1_control is a STATIC library, so the SDK
# symbols stay undefined until host_sdk_sample links it, and host_sdk_sample
# already links liblydHostApi.
# ---------------------------------------------------------------------------
set(ODIN1_SDK_INCLUDE_DIR "" CACHE PATH
    "Directory containing lidar_api.h (odin_ros_driver/include)")

if(NOT ODIN1_SDK_INCLUDE_DIR)
  find_path(ODIN1_SDK_INCLUDE_DIR
    NAMES lidar_api.h
    HINTS
      ${CMAKE_CURRENT_SOURCE_DIR}/../../odin_ros_driver/include
      ${CMAKE_CURRENT_SOURCE_DIR}/../odin_ros_driver/include
      ${CMAKE_INSTALL_PREFIX}/../odin_ros_driver/include
    NO_DEFAULT_PATH)
endif()

if(NOT ODIN1_SDK_INCLUDE_DIR)
  message(FATAL_ERROR
    "odin1_control: could not locate lidar_api.h.\n"
    "Clone the vendor driver into this workspace:\n"
    "  git clone https://github.com/manifoldsdk/odin_ros_driver.git <ws>/src/odin_ros_driver\n"
    "or point CMake at it explicitly:\n"
    "  colcon build --cmake-args -DODIN1_SDK_INCLUDE_DIR=/abs/path/to/odin_ros_driver/include")
endif()
message(STATUS "odin1_control: using Odin1 SDK headers from ${ODIN1_SDK_INCLUDE_DIR}")

add_library(odin1_control STATIC src/control_server.cpp)
target_compile_definitions(odin1_control PUBLIC ROS2)
target_include_directories(odin1_control PUBLIC
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include>
  ${ODIN1_SDK_INCLUDE_DIR})
set_target_properties(odin1_control PROPERTIES POSITION_INDEPENDENT_CODE ON)

ament_target_dependencies(odin1_control
  rclcpp
  rclcpp_action
  geometry_msgs
  builtin_interfaces
  odin1_interfaces)

install(DIRECTORY include/ DESTINATION include)
install(TARGETS odin1_control
  EXPORT export_odin1_control
  ARCHIVE DESTINATION lib
  LIBRARY DESTINATION lib
  RUNTIME DESTINATION bin
  INCLUDES DESTINATION include)

# The consuming target (host_sdk_sample) must also see lidar_api.h; it does,
# because odin_ros_driver puts its own include/ on the path first.
ament_export_include_directories(include)
ament_export_targets(export_odin1_control HAS_LIBRARY_TARGET)
ament_export_dependencies(rclcpp rclcpp_action geometry_msgs builtin_interfaces odin1_interfaces)

install(PROGRAMS patch/apply_driver_patch.py DESTINATION lib/${PROJECT_NAME})

ament_package()
