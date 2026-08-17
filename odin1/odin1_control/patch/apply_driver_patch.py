#!/usr/bin/env python3
# Copyright 2026 Odin1 integration contributors
# Licensed under the Apache License, Version 2.0
"""Wire odin1_control into the vendor driver (manifoldsdk/odin_ros_driver).

WHY A PATCHER AND NOT A .patch FILE
-----------------------------------
Only one process may hold the Odin1 USB device, so the ROS control surface has
to live inside host_sdk_sample. That means touching vendor sources we do not
own and that get updated upstream. A unified diff breaks on any nearby edit and
can misapply silently; this script instead matches exact, verified anchors and
refuses to do anything if an anchor is missing or ambiguous. It is idempotent
and fully reversible via marker comments.

Verified against odin_ros_driver v0.14.1 (CHANGELOG 20260809, commit 6f993cc).
If an anchor is gone, the script tells you which one and stops without writing.

Usage
    ./apply_driver_patch.py --driver <ws>/src/odin_ros_driver          # apply
    ./apply_driver_patch.py --driver <ws>/src/odin_ros_driver --check  # dry run
    ./apply_driver_patch.py --driver <ws>/src/odin_ros_driver --revert # undo
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

MARKER = "ODIN1_CONTROL_PATCH"

# --------------------------------------------------------------------------
# Hunks.
#
# kind="after"   insert `text` immediately after the unique `anchor`
# kind="before"  insert `text` immediately before the unique `anchor`
#
# Every inserted region is delimited by MARKER comments so --revert can strip it.
# --------------------------------------------------------------------------

CPP = "src/host_sdk_sample.cpp"
CMAKE = "CMakeLists.txt"

HUNKS = [
    # ---- 1. include -------------------------------------------------------
    dict(
        file=CPP,
        name="include",
        kind="after",
        anchor='    #include "odin_ros_driver/srv/set_awb.hpp"\n',
        text=(
            f"    // >>> {MARKER}\n"
            "    #include <chrono>\n"
            "    #include <memory>\n"
            '    #include "odin1_control/control_server.hpp"\n'
            '    #include "odin1_control/signal_shutdown.hpp"\n'
            f"    // <<< {MARKER}\n"
        ),
    ),
    # ---- 2. globals -------------------------------------------------------
    dict(
        file=CPP,
        name="globals",
        kind="after",
        anchor="static std::shared_ptr<odin_ros_driver::YamlParser> g_parser = nullptr;\n",
        text=(
            f"// >>> {MARKER}: control surface + cached device status\n"
            "#ifdef ROS2\n"
            "static std::unique_ptr<odin1_control::ControlServer> g_control_server;\n"
            "#endif\n"
            "static std::mutex g_dev_status_mutex;\n"
            "static lidar_device_status_t g_dev_status_cache{};\n"
            "static uint64_t g_dev_status_stamp_ns = 0;\n"
            "static bool g_dev_status_valid = false;\n"
            f"// <<< {MARKER}\n"
        ),
    ),
    # ---- 3. cache the DEV_STATUS sample -----------------------------------
    # Optional in principle (GetDeviceState degrades to status.valid=false
    # without it) but cheap, so it is applied by default.
    dict(
        file=CPP,
        name="dev-status-cache",
        kind="after",
        anchor=(
            "            dev_info_data = (lidar_device_status_t *)"
            "data->stream.imageList[0].pAddr;\n"
        ),
        text=(
            f"            // >>> {MARKER}: cache for GetDeviceState / device_status\n"
            "            if (dev_info_data) {\n"
            "                std::lock_guard<std::mutex> odin1_status_lock(g_dev_status_mutex);\n"
            "                g_dev_status_cache = *dev_info_data;\n"
            "                g_dev_status_stamp_ns = static_cast<uint64_t>(\n"
            "                    std::chrono::duration_cast<std::chrono::nanoseconds>(\n"
            "                        std::chrono::system_clock::now().time_since_epoch())\n"
            "                        .count());\n"
            "                g_dev_status_valid = true;\n"
            "            }\n"
            f"            // <<< {MARKER}\n"
        ),
    ),
    # ---- 4. construct the control server ----------------------------------
    dict(
        file=CPP,
        name="construct",
        kind="after",
        anchor="    g_ros_object = std::make_shared<MultiSensorPublisher>(node);\n",
        text=(
            f"    // >>> {MARKER}: bring up the standard ROS 2 control surface.\n"
            "    // All accessors read lazily, so it is safe to construct here, before\n"
            "    // control_command.yaml has been parsed and before a device is attached.\n"
            "    {\n"
            "        odin1_control::DeviceContext odin_ctx;\n"
            "        odin_ctx.get_device = []() -> device_handle { return odinDevice; };\n"
            "        odin_ctx.is_connected = []() { return deviceConnected.load(); };\n"
            "        // The driver hard-codes SLAM mode at host_sdk_sample.cpp:1271.\n"
            "        odin_ctx.get_stream_mode = []() { return static_cast<int>(LIDAR_MODE_SLAM); };\n"
            "        odin_ctx.get_map_mode = []() { return g_custom_map_mode; };\n"
            "        odin_ctx.set_map_mode = [](int m) { g_custom_map_mode = m; };\n"
            "        odin_ctx.get_reloc_map_path = []() { return g_relocalization_map_abs_path; };\n"
            "        odin_ctx.set_reloc_map_path =\n"
            "            [](const std::string & p) { g_relocalization_map_abs_path = p; };\n"
            "        odin_ctx.get_configured_map_dir = []() { return g_mapping_result_dest_dir; };\n"
            "        odin_ctx.get_configured_map_name = []() { return g_mapping_result_file_name; };\n"
            "        odin_ctx.get_default_map_dir = []() { return map_root_dir_.string(); };\n"
            "        odin_ctx.get_stream_flags = []() {\n"
            "            odin1_control::StreamFlags f;\n"
            "            f.rgb = g_sendrgb != 0;\n"
            "            f.imu = g_sendimu != 0;\n"
            "            f.odom = g_sendodom != 0;\n"
            "            f.dtof = g_senddtof != 0;\n"
            "            f.cloud_slam = g_sendcloudslam != 0;\n"
            "            return f;\n"
            "        };\n"
            "        odin_ctx.is_map_transfer_in_progress =\n"
            "            []() { return g_map_transfer_in_progress.load(); };\n"
            "        odin_ctx.set_map_transfer_in_progress =\n"
            "            [](bool v) { g_map_transfer_in_progress.store(v); };\n"
            "        odin_ctx.get_device_status =\n"
            "            [](lidar_device_status_t & out, uint64_t & stamp_ns) {\n"
            "                std::lock_guard<std::mutex> lk(g_dev_status_mutex);\n"
            "                if (!g_dev_status_valid) { return false; }\n"
            "                out = g_dev_status_cache;\n"
            "                stamp_ns = g_dev_status_stamp_ns;\n"
            "                return true;\n"
            "            };\n"
            "        odin_ctx.driver_version = ros_driver_version;\n"
            "        g_control_server =\n"
            "            std::make_unique<odin1_control::ControlServer>(node, odin_ctx);\n"
            "    }\n"
            f"    // <<< {MARKER}\n"
        ),
    ),
    # ---- 5d. replace the vendor's signal handler with a safe trampoline ----
    #
    # signal(SIGINT, signal_handler) above installs a handler that logs with
    # RCLCPP_*, calls fclose(), touches C++ objects, calls the SDK and ends in
    # exit(). None of that is async-signal-safe: the handler runs on whichever
    # thread was interrupted, so if that thread holds the logger mutex - or
    # ControlServer's idle_mutex_ - the handler deadlocks and the process hangs
    # with no output. That is the likely case, not the unlikely one, because the
    # whole point is that a worker is busy.
    #
    # sigaction() here overrides both registrations with a handler that only
    # stores a sig_atomic_t and writes one byte to a self-pipe. A normal thread
    # then drains, and calls signal_handler() itself as an ordinary function -
    # where every one of those calls is legal again.
    dict(
        file=CPP,
        name="signal-trampoline",
        kind="after",
        anchor=(
            "    signal(SIGINT, signal_handler);\n"
            "    signal(SIGTERM, signal_handler);\n"
        ),
        text=(
            f"    // >>> {MARKER}: async-signal-safe SIGINT/SIGTERM front end\n"
            "    #ifdef ROS2\n"
            "    static odin1_control::SignalShutdownGuard odin1_signal_guard(\n"
            "        [](std::chrono::milliseconds grace) {\n"
            "            // Runs on the shutdown thread, so locking and logging are fine.\n"
            "            return !g_control_server || g_control_server->beginTeardown(grace);\n"
            "        },\n"
            "        [](int sig) { signal_handler(sig); },\n"
            "        std::chrono::seconds(3));\n"
            "    (void)odin1_signal_guard;\n"
            "    #endif\n"
            f"    // <<< {MARKER}\n"
        ),
    ),
    # ---- 5c. tear the control server down before static destruction can ----
    #
    # By here beginTeardown() has already proved there is nothing in flight, the
    # SDK is down, and rclcpp is still up. Destroying explicitly leaves static
    # destruction with nothing to do, which is the third leg of the race the
    # vendor comment right below this anchor already worries about.
    dict(
        file=CPP,
        name="shutdown-signal-reset",
        kind="before",
        anchor=(
            "            if (g_ros_object) {\n"
            "                g_ros_object.reset();\n"
            "            }\n"
            "            rclcpp::shutdown();\n"
        ),
        text=f"            g_control_server.reset();  // {MARKER}\n",
    ),
    # ---- 5b. do not recycle the device handle under a running operation ----
    #
    # The attach path does a bare `if (odinDevice) { odinDevice = nullptr; ... }`
    # on every reconnect. If a control operation is mid-sequence it would then be
    # holding a handle the driver has abandoned. DeviceSession detects the swap
    # and fails the operation, but only at the next step boundary - waiting here
    # closes the window where a call is already in flight.
    dict(
        file=CPP,
        name="reconnect-barrier",
        kind="before",
        anchor=(
            "        if (odinDevice) {\n"
            "            odinDevice = nullptr;\n"
        ),
        text=(
            f"        // >>> {MARKER}: never recycle the device handle under a live SDK call.\n"
            "        // The driver drops the old pointer without lidar_destory_device(), so an\n"
            "        // in-flight call is not a use-after-free - but lidar_create_device()\n"
            "        // resets SDK-global state (lidar_get_device_state() takes no handle), so\n"
            "        // overlapping the two corrupts the control channel. Guarded: common code,\n"
            "        // ROS2-only symbol.\n"
            "        #ifdef ROS2\n"
            "        if (g_control_server) {\n"
            "            g_control_server->notifyDeviceInvalidated();\n"
            "            if (!g_control_server->waitForDeviceIdle(std::chrono::seconds(3))) {\n"
            "                RCLCPP_FATAL(rclcpp::get_logger(\"device_cb\"),\n"
            "                    \"Refusing to recycle the device handle: a control operation is \"\n"
            "                    \"still inside the SDK. Skipping this attach - replug the device \"\n"
            "                    \"once it finishes.\");\n"
            "                return;\n"
            "            }\n"
            "        }\n"
            "        #endif\n"
            f"        // <<< {MARKER}\n"
        ),
    ),
    # ---- 6. stop it on the no-device early exit ---------------------------
    dict(
        file=CPP,
        name="shutdown-early-exit",
        kind="before",
        anchor=(
            "        node.reset();              // destroy the node first\n"
            "        rclcpp::shutdown();\n"
        ),
        text=f"        g_control_server.reset();  // {MARKER}\n",
    ),
    # ---- 7. stop it on the normal exit ------------------------------------
    dict(
        file=CPP,
        name="shutdown-normal",
        kind="before",
        anchor=(
            "        rclcpp::shutdown();\n"
            "    #else\n"
            "        // Create 10Hz Rate object\n"
            "        ros::Rate rate(10);\n"
        ),
        text=f"        g_control_server.reset();  // {MARKER}\n",
    ),
    # ---- 8. CMake: find the packages --------------------------------------
    dict(
        file=CMAKE,
        name="cmake-find",
        kind="after",
        anchor="    find_package(ament_index_cpp REQUIRED)\n",
        text=(
            f"    # >>> {MARKER}\n"
            "    find_package(odin1_interfaces REQUIRED)\n"
            "    find_package(odin1_control REQUIRED)\n"
            f"    # <<< {MARKER}\n"
        ),
    ),
    # ---- 9. CMake: link them ----------------------------------------------
    # ament_target_dependencies is additive, so a second call is enough and we
    # never have to edit the vendor's own dependency list.
    dict(
        file=CMAKE,
        name="cmake-link",
        kind="after",
        anchor=(
            "    rosidl_get_typesupport_target(cpp_typesupport_target\n"
            '        ${PROJECT_NAME} "rosidl_typesupport_cpp")\n'
            '    target_link_libraries(host_sdk_sample "${cpp_typesupport_target}")\n'
        ),
        text=(
            f"    # >>> {MARKER}\n"
            "    ament_target_dependencies(host_sdk_sample odin1_interfaces odin1_control)\n"
            f"    # <<< {MARKER}\n"
        ),
    ),
]


def load(driver: pathlib.Path, rel: str) -> str:
    p = driver / rel
    if not p.is_file():
        sys.exit(f"ERROR: {p} not found. Is --driver pointing at an odin_ros_driver checkout?")
    return p.read_text(encoding="utf-8")


def already_applied(text: str) -> bool:
    return MARKER in text


def apply_all(driver: pathlib.Path, dry_run: bool) -> int:
    contents = {rel: load(driver, rel) for rel in {h["file"] for h in HUNKS}}

    if any(already_applied(t) for t in contents.values()):
        print(f"Already patched ({MARKER} markers present). Nothing to do.")
        print("Run with --revert first if you want to re-apply.")
        return 0

    problems = []
    for h in HUNKS:
        n = contents[h["file"]].count(h["anchor"])
        if n != 1:
            head = h["anchor"].splitlines()[0][:78]
            problems.append(f"  [{h['name']}] {h['file']}: {n} matches for anchor -> {head!r}")

    if problems:
        print("ERROR: anchors did not match exactly once. Refusing to patch.")
        print("The vendor driver has probably changed; update HUNKS in this script.")
        print("\n".join(problems))
        return 1

    for h in HUNKS:
        text = contents[h["file"]]
        if h["kind"] == "after":
            contents[h["file"]] = text.replace(h["anchor"], h["anchor"] + h["text"], 1)
        else:
            contents[h["file"]] = text.replace(h["anchor"], h["text"] + h["anchor"], 1)

    for rel, text in contents.items():
        if dry_run:
            print(f"[check] would write {driver / rel} ({len(text)} bytes)")
        else:
            (driver / rel).write_text(text, encoding="utf-8")
            print(f"patched {driver / rel}")

    print(f"\n{len(HUNKS)} hunks {'validated' if dry_run else 'applied'}.")
    if not dry_run:
        print("Next: colcon build --packages-select "
              "odin1_interfaces odin1_control odin1_tf_adapter odin_ros_driver")
    return 0


BLOCK_RE = re.compile(
    r"[ \t]*(?://|#) *>>> " + MARKER + r".*?(?://|#) *<<< " + MARKER + r"[^\n]*\n",
    re.DOTALL,
)
LINE_RE = re.compile(r"^.*(?://|#) *" + MARKER + r"[^\n]*\n", re.MULTILINE)


def revert(driver: pathlib.Path, dry_run: bool) -> int:
    touched = 0
    for rel in {h["file"] for h in HUNKS}:
        text = load(driver, rel)
        if MARKER not in text:
            continue
        cleaned = BLOCK_RE.sub("", text)
        cleaned = LINE_RE.sub("", cleaned)
        if MARKER in cleaned:
            print(f"ERROR: {rel} still contains {MARKER} after revert; edit it by hand.")
            return 1
        if dry_run:
            print(f"[check] would restore {driver / rel}")
        else:
            (driver / rel).write_text(cleaned, encoding="utf-8")
            print(f"reverted {driver / rel}")
        touched += 1
    if touched == 0:
        print("Nothing to revert.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--driver", required=True, type=pathlib.Path,
                    help="path to the odin_ros_driver checkout")
    ap.add_argument("--check", action="store_true", help="validate anchors, write nothing")
    ap.add_argument("--revert", action="store_true", help="remove the patch")
    args = ap.parse_args()

    driver = args.driver.expanduser().resolve()
    if args.revert:
        return revert(driver, args.check)
    return apply_all(driver, args.check)


if __name__ == "__main__":
    sys.exit(main())
