cmake_minimum_required(VERSION 3.8)
project(odin1_tf_adapter)

if(NOT CMAKE_CXX_STANDARD)
  set(CMAKE_CXX_STANDARD 17)
endif()
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra)
endif()

find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(nav_msgs REQUIRED)
find_package(sensor_msgs REQUIRED)
find_package(tf2 REQUIRED)
find_package(tf2_ros REQUIRED)
find_package(tf2_msgs REQUIRED)
find_package(odin1_interfaces REQUIRED)

set(ADAPTER_DEPS
  rclcpp geometry_msgs nav_msgs tf2 tf2_ros tf2_msgs odin1_interfaces)

add_executable(odin1_tf_adapter_node src/tf_adapter_node.cpp)
target_include_directories(odin1_tf_adapter_node PRIVATE
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>)
ament_target_dependencies(odin1_tf_adapter_node ${ADAPTER_DEPS})

add_executable(odin1_frame_retag_node src/frame_retag_node.cpp)
ament_target_dependencies(odin1_frame_retag_node rclcpp nav_msgs sensor_msgs)

install(TARGETS odin1_tf_adapter_node odin1_frame_retag_node
  DESTINATION lib/${PROJECT_NAME})
install(DIRECTORY include/ DESTINATION include)
install(DIRECTORY launch config DESTINATION share/${PROJECT_NAME})

# ---------------------------------------------------------------------------
# Tests
#
# map_odom_policy.hpp is header-only and ROS-free by design, so the safety rule
# that decides whether a map frame may be published at all is tested as a pure
# function - no node, no graph, no timing.
# ---------------------------------------------------------------------------
if(BUILD_TESTING)
  find_package(ament_cmake_gtest REQUIRED)

  ament_add_gtest(test_map_odom_policy test/test_map_odom_policy.cpp)
  if(TARGET test_map_odom_policy)
    target_include_directories(test_map_odom_policy PRIVATE
      ${CMAKE_CURRENT_SOURCE_DIR}/include)
  endif()
endif()

ament_package()
