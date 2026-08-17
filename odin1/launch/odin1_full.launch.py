# Copyright 2026 Odin1 integration contributors
# Licensed under the Apache License, Version 2.0
"""Driver (with its /tf remapped away) + REP-105 TF adapter.

This intentionally does NOT include the vendor's odin1_ros2.launch.py: the
remapping has to be applied to the host_sdk_sample node itself, and the vendor
launch file also starts the depth-completion, reprojection and overlay demo
nodes, which are off by default in control_command.yaml anyway.

Copy this file into a package's share/launch, or run it by path:
    ros2 launch ./odin1_full.launch.py

The control surface (save_map / switch_mode / load_map / set_init_pose /
reset_algo / get_device_state) is hosted INSIDE host_sdk_sample by odin1_control
and needs no node of its own - it appears as soon as the driver starts, provided
the driver was built with odin1_control/patch/apply_driver_patch.py applied.
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

# The driver's TransformBroadcaster writes to the absolute topics /tf and
# /tf_static. Node-level remapping moves the whole non-compliant tree onto a
# side channel that only odin1_tf_adapter reads.
TF_REMAPPINGS = [('/tf', '/odin1/tf_raw'), ('/tf_static', '/odin1/tf_static_raw')]


def generate_launch_description():
    driver_share = get_package_share_directory('odin_ros_driver')
    adapter_share = get_package_share_directory('odin1_tf_adapter')

    config_arg = DeclareLaunchArgument(
        'config_file',
        default_value=os.path.join(driver_share, 'config', 'control_command.yaml'),
        description='Vendor driver configuration')
    params_arg = DeclareLaunchArgument(
        'params_file',
        default_value=os.path.join(adapter_share, 'config', 'tf_adapter.yaml'),
        description='odin1_tf_adapter parameters')
    rviz_arg = DeclareLaunchArgument(
        'rviz', default_value='false', description='Start RViz2')
    rviz_config_arg = DeclareLaunchArgument(
        'rviz_config',
        default_value=os.path.join(driver_share, 'config', 'odin_ros2.rviz'),
        description='RViz2 configuration')

    driver = Node(
        package='odin_ros_driver',
        executable='host_sdk_sample',
        name='host_sdk_sample',
        output='screen',
        parameters=[{'config_file': LaunchConfiguration('config_file')}],
        remappings=TF_REMAPPINGS,
    )

    adapter = Node(
        package='odin1_tf_adapter',
        executable='odin1_tf_adapter_node',
        name='odin1_tf_adapter',
        output='screen',
        parameters=[LaunchConfiguration('params_file')],
    )

    rviz = Node(
        package='rviz2',
        executable='rviz2',
        name='rviz2',
        output='screen',
        condition=IfCondition(LaunchConfiguration('rviz')),
        arguments=['-d', LaunchConfiguration('rviz_config')],
    )

    return LaunchDescription(
        [config_arg, params_arg, rviz_arg, rviz_config_arg, driver, adapter, rviz])
