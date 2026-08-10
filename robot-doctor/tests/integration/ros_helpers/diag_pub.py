#!/usr/bin/env python3
"""Tiny test node: publishes one WARN DiagnosticStatus at 2 Hz.

Used by smoke-ros because `ros2 topic pub` cannot express the byte-typed
`level` field reliably. Real rclpy node — not a mock.
"""
import rclpy
from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
from rclpy.node import Node


class DiagPub(Node):
    def __init__(self):
        super().__init__("smoke_diag_pub")
        self.pub = self.create_publisher(DiagnosticArray, "/diagnostics", 10)
        self.timer = self.create_timer(0.5, self.tick)

    def tick(self):
        msg = DiagnosticArray()
        msg.header.stamp = self.get_clock().now().to_msg()
        status = DiagnosticStatus()
        status.level = DiagnosticStatus.WARN
        status.name = "smoke_component"
        status.message = "warming up"
        status.hardware_id = "hw0"
        status.values = [KeyValue(key="temperature", value="61.5")]
        msg.status = [status]
        self.pub.publish(msg)


def main():
    rclpy.init()
    node = DiagPub()
    rclpy.spin(node)


if __name__ == "__main__":
    main()
