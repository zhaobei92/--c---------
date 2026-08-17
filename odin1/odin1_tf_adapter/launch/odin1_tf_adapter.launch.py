# Copyright 2026 Odin1 integration contributors
# Licensed under the Apache License, Version 2.0
"""Launch the REP-105 TF adapter on its own.

Use this when the driver is already running (started elsewhere) AND was started
with its /tf remapped away:

    ros2 run odin_ros_driver host_sdk_sample --ros-args \\
        -r /tf:=/odin1/tf_raw -r /tf_static:=/odin1/tf_static_raw

If the driver still publishes on the real /tf, do not run this node: `map` would
get two parents (driver: odom -> map, adapter: map -> odom) and tf2 would report
a loop. odin1_full.launch.py sets the remapping for you.
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    pkg = get_package_share_directory('odin1_tf_adapter')
    default_params = os.path.join(pkg, 'config', 'tf_adapter.yaml')

    params_arg = DeclareLaunchArgument(
        'params_file', default_value=default_params,
        description='YAML parameter file for odin1_tf_adapter_node')
    retag_arg = DeclareLaunchArgument(
        'retag_frames', default_value='false',
        description='Also run odin1_frame_retag_node. Only needed when '
                    'frame_prefix is non-empty in params_file.')
    prefix_arg = DeclareLaunchArgument(
        'frame_prefix', default_value='odin1_',
        description='Prefix used by odin1_frame_retag_node when retag_frames is true. '
                    'Must match frame_prefix in params_file.')

    adapter = Node(
        package='odin1_tf_adapter',
        executable='odin1_tf_adapter_node',
        name='odin1_tf_adapter',
        output='screen',
        parameters=[LaunchConfiguration('params_file')],
    )

    retag = Node(
        package='odin1_tf_adapter',
        executable='odin1_frame_retag_node',
        name='odin1_frame_retag',
        output='screen',
        condition=IfCondition(LaunchConfiguration('retag_frames')),
        parameters=[{'frame_prefix': LaunchConfiguration('frame_prefix')}],
    )

    return LaunchDescription([params_arg, retag_arg, prefix_arg, adapter, retag])
