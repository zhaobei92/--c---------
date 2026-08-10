#!/usr/bin/env python3
"""RclpyProvider helper for the Robot Doctor ros2 plugin.

Speaks one JSON object per line over stdin/stdout with the Rust plugin
process. Read-only by contract: it never publishes commands, calls
business services, sends actions, sets parameters or transitions
lifecycle states. The only service calls made are lifecycle `get_state`
reads (explicitly read-only).

Requests:  {"id": "1", "op": "graph", ...}
Responses: {"id": "1", "ok": true, "data": {...}}
           {"id": "1", "ok": false, "kind": "TYPE_SUPPORT_MISSING", "message": "..."}
Every operation is bounded by its own duration/timeout arguments.
"""

import json
import math
import os
import sys
import threading
import time

# rclpy import errors are reported over the protocol, not as crashes.
try:
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import (
        QoSProfile,
        ReliabilityPolicy,
        DurabilityPolicy,
        HistoryPolicy,
        LivelinessPolicy,
    )

    RCLPY_IMPORT_ERROR = None
except Exception as exc:  # noqa: BLE001
    rclpy = None
    RCLPY_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"


def _duration_to_obj(d):
    """rclpy Duration -> {seconds | unspecified} without inventing values."""
    try:
        ns = d.nanoseconds
        # DDS uses huge sentinel values for "infinite/unspecified".
        if ns <= 0 or ns >= 2**62:
            return {"unspecified": True}
        return {"seconds": ns / 1e9}
    except Exception:  # noqa: BLE001
        return {"unspecified": True}


def _enum_name(value, mapping):
    try:
        return mapping.get(value, "UNKNOWN")
    except Exception:  # noqa: BLE001
        return "UNKNOWN"


def qos_to_obj(qos):
    rel = {
        ReliabilityPolicy.RELIABLE: "RELIABLE",
        ReliabilityPolicy.BEST_EFFORT: "BEST_EFFORT",
        ReliabilityPolicy.SYSTEM_DEFAULT: "SYSTEM_DEFAULT",
    }
    dur = {
        DurabilityPolicy.VOLATILE: "VOLATILE",
        DurabilityPolicy.TRANSIENT_LOCAL: "TRANSIENT_LOCAL",
        DurabilityPolicy.SYSTEM_DEFAULT: "SYSTEM_DEFAULT",
    }
    hist = {
        HistoryPolicy.KEEP_LAST: "KEEP_LAST",
        HistoryPolicy.KEEP_ALL: "KEEP_ALL",
        HistoryPolicy.SYSTEM_DEFAULT: "SYSTEM_DEFAULT",
    }
    live = {
        LivelinessPolicy.AUTOMATIC: "AUTOMATIC",
        LivelinessPolicy.MANUAL_BY_TOPIC: "MANUAL_BY_TOPIC",
        LivelinessPolicy.SYSTEM_DEFAULT: "SYSTEM_DEFAULT",
    }
    try:
        return {
            "reliability": _enum_name(qos.reliability, rel),
            "durability": _enum_name(qos.durability, dur),
            "history": _enum_name(qos.history, hist),
            "depth": getattr(qos, "depth", None),
            "deadline": _duration_to_obj(qos.deadline),
            "lifespan": _duration_to_obj(qos.lifespan),
            "liveliness": _enum_name(qos.liveliness, live),
            "lease_duration": _duration_to_obj(qos.liveliness_lease_duration),
        }
    except Exception:  # noqa: BLE001
        return {}


class ProviderError(Exception):
    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind
        self.message = message


class Provider:
    def __init__(self):
        if rclpy is None:
            raise ProviderError("RCLPY_UNAVAILABLE", RCLPY_IMPORT_ERROR)
        rclpy.init()
        self.node = Node(
            "robot_doctor_observer",
            namespace="/",
            start_parameter_services=False,
            enable_rosout=False,
        )
        self.started = time.monotonic()

    # ── graph ──────────────────────────────────────────────────────────

    def wait_discovery(self, max_s=3.0, stable_s=0.6):
        """Bounded DDS discovery stabilization: wait until the node/topic
        counts stop changing for `stable_s`, or `max_s` elapses."""
        deadline = time.monotonic() + max_s
        last = None
        stable_since = time.monotonic()
        while time.monotonic() < deadline:
            rclpy.spin_once(self.node, timeout_sec=0.1)
            sig = (
                len(self.node.get_node_names_and_namespaces()),
                len(self.node.get_topic_names_and_types()),
            )
            now = time.monotonic()
            if sig != last:
                last = sig
                stable_since = now
            elif now - stable_since >= stable_s:
                break
        return int((time.monotonic() - max(self.started, time.monotonic() - max_s)) * 1000)

    def graph(self, args):
        t0 = time.monotonic()
        self.wait_discovery(max_s=float(args.get("discovery_s", 3.0)))
        discovery_ms = int((time.monotonic() - t0) * 1000)

        nodes = []
        own = self.node.get_fully_qualified_name()
        for name, ns in self.node.get_node_names_and_namespaces():
            full = (ns.rstrip("/") + "/" + name) if ns != "/" else "/" + name
            if full == own:
                continue  # do not report our own observer node
            try:
                pubs = [t for t, _ in self.node.get_publisher_names_and_types_by_node(name, ns)]
                subs = [t for t, _ in self.node.get_subscriber_names_and_types_by_node(name, ns)]
                srvs = [s for s, _ in self.node.get_service_names_and_types_by_node(name, ns)]
            except Exception:  # node may vanish mid-query
                pubs, subs, srvs = [], [], []
            actions = sorted(
                {s.rsplit("/_action/", 1)[0] for s in srvs if "/_action/" in s}
            )
            srv_plain = [s for s in srvs if "/_action/" not in s]
            nodes.append(
                {
                    "name": name,
                    "namespace": ns,
                    "full_name": full,
                    "publishers": sorted(pubs),
                    "subscriptions": sorted(subs),
                    "services": sorted(srv_plain),
                    "actions": actions,
                }
            )

        topics = []
        for tname, ttypes in self.node.get_topic_names_and_types():
            pubs, subs = [], []
            try:
                for info in self.node.get_publishers_info_by_topic(tname):
                    pubs.append(self._endpoint(info, "PUBLISHER"))
                for info in self.node.get_subscriptions_info_by_topic(tname):
                    subs.append(self._endpoint(info, "SUBSCRIPTION"))
            except Exception:  # noqa: BLE001
                pass
            # Exclude our own observer's endpoints.
            pubs = [p for p in pubs if p["node"] != own]
            subs = [s for s in subs if s["node"] != own]
            topics.append(
                {
                    "name": tname,
                    "types": sorted(ttypes),
                    "publisher_count": len(pubs),
                    "subscriber_count": len(subs),
                    "publishers": pubs,
                    "subscribers": subs,
                }
            )

        services = {}
        for node in nodes:
            full = node["full_name"]
            try:
                base = node["name"]
                ns = node["namespace"]
                for sname, stypes in self.node.get_service_names_and_types_by_node(base, ns):
                    if "/_action/" in sname:
                        continue
                    entry = services.setdefault(sname, {"types": set(), "providers": []})
                    entry["types"].update(stypes)
                    entry["providers"].append(full)
            except Exception:  # noqa: BLE001
                continue
        service_list = [
            {"name": k, "types": sorted(v["types"]), "providers": sorted(v["providers"])}
            for k, v in services.items()
        ]

        actions = {}
        for node in nodes:
            for aname in node["actions"]:
                entry = actions.setdefault(aname, {"types": set(), "servers": []})
                entry["servers"].append(node["full_name"])
        # Action types from action feedback topics (<action>/_action/feedback).
        for tname, ttypes in self.node.get_topic_names_and_types():
            if tname.endswith("/_action/feedback"):
                base = tname[: -len("/_action/feedback")]
                entry = actions.setdefault(base, {"types": set(), "servers": []})
                for t in ttypes:
                    entry["types"].add(t.replace("_FeedbackMessage", ""))
        action_list = [
            {"name": k, "types": sorted(v["types"]), "servers": sorted(set(v["servers"]))}
            for k, v in actions.items()
        ]

        return {
            "discovery_ms": discovery_ms,
            "nodes": nodes,
            "topics": topics,
            "services": service_list,
            "actions": action_list,
        }

    def _endpoint(self, info, kind):
        gid = None
        try:
            gid = bytes(info.endpoint_gid).hex()
        except Exception:  # noqa: BLE001
            pass
        ns = info.node_namespace.rstrip("/")
        return {
            "node": f"{ns}/{info.node_name}" if ns else f"/{info.node_name}",
            "endpoint_type": kind,
            "topic_type": info.topic_type,
            "gid": gid,
            "qos": qos_to_obj(info.qos_profile),
        }

    # ── qos compatibility ──────────────────────────────────────────────

    def qos_compat(self, args):
        """Deterministic pub/sub QoS compatibility via rclpy where available."""
        try:
            from rclpy.qos import qos_check_compatible, QoSCompatibility  # noqa: PLC0415
        except Exception as exc:  # noqa: BLE001
            raise ProviderError("PROVIDER_ERROR", f"qos_check_compatible unavailable: {exc}")
        topic = args["topic"]
        results = []
        pubs = self.node.get_publishers_info_by_topic(topic)
        subs = self.node.get_subscriptions_info_by_topic(topic)
        for p in pubs:
            for s in subs:
                try:
                    compat, reason = qos_check_compatible(p.qos_profile, s.qos_profile)
                    verdict = {
                        QoSCompatibility.OK: "COMPATIBLE",
                        QoSCompatibility.WARNING: "WARNING",
                        QoSCompatibility.ERROR: "INCOMPATIBLE",
                    }.get(compat, "UNKNOWN")
                except Exception:  # noqa: BLE001
                    verdict, reason = "UNKNOWN", "qos_check_compatible failed"
                results.append(
                    {
                        "publisher": self._endpoint(p, "PUBLISHER")["node"],
                        "subscriber": self._endpoint(s, "SUBSCRIPTION")["node"],
                        "verdict": verdict,
                        "reason": str(reason) if reason else None,
                    }
                )
        return {"topic": topic, "pairs": results}

    # ── topic sampling ─────────────────────────────────────────────────

    def sample(self, args):
        topic = args["topic"]
        duration = min(float(args.get("duration_s", 3.0)), 30.0)
        # Runtime type discovery — no compile-time hardcoding.
        types = dict(self.node.get_topic_names_and_types()).get(topic)
        if not types:
            raise ProviderError("PROVIDER_ERROR", f"topic '{topic}' not present in graph")
        type_name = types[0]
        try:
            from rosidl_runtime_py.utilities import get_message  # noqa: PLC0415

            msg_type = get_message(type_name)
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(
                "TYPE_SUPPORT_MISSING",
                f"runtime type support for '{type_name}' unavailable: {exc}",
            )

        arrivals = []
        last_stamp = [None]

        def cb(msg):
            arrivals.append(time.monotonic())
            header = getattr(msg, "header", None)
            stamp = getattr(header, "stamp", None) if header is not None else None
            if stamp is not None:
                last_stamp[0] = stamp.sec + stamp.nanosec / 1e9

        # Best-effort/volatile subscription is compatible with both
        # reliable and best-effort publishers.
        qos = QoSProfile(
            depth=100,
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE,
        )
        sub = self.node.create_subscription(msg_type, topic, cb, qos)
        t0 = time.monotonic()
        end = t0 + duration
        while time.monotonic() < end:
            rclpy.spin_once(self.node, timeout_sec=0.05)
        self.node.destroy_subscription(sub)
        elapsed = time.monotonic() - t0

        periods = [b - a for a, b in zip(arrivals, arrivals[1:])]
        result = {
            "topic": topic,
            "message_type": type_name,
            "sample_count": len(arrivals),
            "sampling_duration_s": elapsed,
            "observed_hz": (len(arrivals) - 1) / (arrivals[-1] - arrivals[0])
            if len(arrivals) >= 2 and arrivals[-1] > arrivals[0]
            else (None if len(arrivals) < 2 else None),
            "period_min_ms": min(periods) * 1000 if periods else None,
            "period_max_ms": max(periods) * 1000 if periods else None,
            "period_mean_ms": (sum(periods) / len(periods)) * 1000 if periods else None,
            "period_stddev_ms": (
                math.sqrt(
                    sum((p - sum(periods) / len(periods)) ** 2 for p in periods)
                    / len(periods)
                )
                * 1000
                if len(periods) >= 2
                else None
            ),
            # Header-stamp age vs ROS clock — only when a stamp exists.
            "message_stamp_age_s": (
                (self.node.get_clock().now().nanoseconds / 1e9) - last_stamp[0]
                if last_stamp[0] is not None
                else None
            ),
            # Receive age: time between last arrival and end of sampling.
            "receive_age_s": (time.monotonic() - arrivals[-1]) if arrivals else None,
        }
        return result

    # ── tf ─────────────────────────────────────────────────────────────

    def tf(self, args):
        listen = min(float(args.get("listen_s", 2.0)), 10.0)
        try:
            from tf2_msgs.msg import TFMessage  # noqa: PLC0415
        except Exception as exc:  # noqa: BLE001
            raise ProviderError("TF_UNAVAILABLE", f"tf2_msgs unavailable: {exc}")

        edges = {}

        def make_cb(is_static):
            def cb(msg):
                for tr in msg.transforms:
                    key = (tr.header.frame_id, tr.child_frame_id)
                    stamp = tr.header.stamp.sec + tr.header.stamp.nanosec / 1e9
                    prev = edges.get(key)
                    if prev is None or (prev["last_stamp"] or 0) < stamp or is_static:
                        edges[key] = {
                            "parent": tr.header.frame_id,
                            "child": tr.child_frame_id,
                            "is_static": is_static,
                            "last_stamp": stamp if not is_static else (stamp or None),
                            "broadcaster": None,
                        }

            return cb

        qos_static = QoSProfile(
            depth=100,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        qos_dyn = QoSProfile(depth=100, reliability=ReliabilityPolicy.BEST_EFFORT)
        s1 = self.node.create_subscription(TFMessage, "/tf", make_cb(False), qos_dyn)
        s2 = self.node.create_subscription(TFMessage, "/tf_static", make_cb(True), qos_static)
        end = time.monotonic() + listen
        while time.monotonic() < end:
            rclpy.spin_once(self.node, timeout_sec=0.05)
        self.node.destroy_subscription(s1)
        self.node.destroy_subscription(s2)

        frames = sorted({f for k in edges for f in k})
        # Connected components over undirected frame relationships.
        parent_map = {}

        def find(x):
            while parent_map.get(x, x) != x:
                parent_map[x] = parent_map.get(parent_map[x], parent_map[x])
                x = parent_map[x]
            return x

        for f in frames:
            parent_map.setdefault(f, f)
        for a, b in edges:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent_map[ra] = rb
        components = {}
        for f in frames:
            components.setdefault(find(f), []).append(f)

        return {
            "frames": frames,
            "edges": sorted(edges.values(), key=lambda e: (e["parent"], e["child"])),
            "connected_components": sorted(
                [sorted(v) for v in components.values()], key=lambda c: c[0]
            ),
            "listen_ms": int(listen * 1000),
        }

    def tf_query(self, args):
        source = args["source"]
        target = args["target"]
        timeout = min(float(args.get("timeout_s", 2.0)), 10.0)
        try:
            from tf2_ros import Buffer, TransformListener  # noqa: PLC0415
            from rclpy.time import Time  # noqa: PLC0415
            from rclpy.duration import Duration  # noqa: PLC0415
        except Exception as exc:  # noqa: BLE001
            raise ProviderError("TF_UNAVAILABLE", f"tf2_ros unavailable: {exc}")

        t0 = time.monotonic()
        buffer = Buffer()
        listener = TransformListener(buffer, self.node)
        deadline = time.monotonic() + timeout
        status, reason, stamp = "UNAVAILABLE", None, None
        while time.monotonic() < deadline:
            rclpy.spin_once(self.node, timeout_sec=0.05)
            try:
                if buffer.can_transform(target, source, Time(), timeout=Duration(seconds=0.0)):
                    tr = buffer.lookup_transform(target, source, Time())
                    stamp = tr.header.stamp.sec + tr.header.stamp.nanosec / 1e9
                    status = "AVAILABLE"
                    break
            except Exception as exc:  # noqa: BLE001
                reason = str(exc)
        if status != "AVAILABLE":
            if time.monotonic() >= deadline and reason is None:
                status = "TIMEOUT"
                reason = f"no transform {source} -> {target} within {timeout}s"
        self.node.destroy_subscription(listener.tf_sub)
        self.node.destroy_subscription(listener.tf_static_sub)
        return {
            "source": source,
            "target": target,
            "status": status,
            "reason": reason,
            "elapsed_ms": (time.monotonic() - t0) * 1000,
            "stamp": stamp,
        }

    # ── diagnostics ────────────────────────────────────────────────────

    def diagnostics(self, args):
        window = min(float(args.get("window_s", 2.0)), 10.0)
        try:
            from diagnostic_msgs.msg import DiagnosticArray  # noqa: PLC0415
        except Exception as exc:  # noqa: BLE001
            raise ProviderError("PROVIDER_ERROR", f"diagnostic_msgs unavailable: {exc}")

        statuses = {}

        def make_cb(topic):
            def cb(msg):
                stamp = msg.header.stamp.sec + msg.header.stamp.nanosec / 1e9
                for st in msg.status:
                    statuses[(topic, st.name)] = {
                        "name": st.name,
                        "hardware_id": st.hardware_id,
                        "level": int.from_bytes(
                            st.level if isinstance(st.level, bytes) else bytes([st.level]),
                            "little",
                        ),
                        "message": st.message,
                        "values": {kv.key: kv.value for kv in st.values},
                        "stamp": stamp,
                        "source_topic": topic,
                    }

            return cb

        subs = []
        present = dict(self.node.get_topic_names_and_types())
        for topic in ("/diagnostics", "/diagnostics_agg"):
            if topic in present:
                subs.append(
                    self.node.create_subscription(
                        DiagnosticArray, topic, make_cb(topic), 50
                    )
                )
        end = time.monotonic() + (window if subs else 0.0)
        while time.monotonic() < end:
            rclpy.spin_once(self.node, timeout_sec=0.05)
        for sub in subs:
            self.node.destroy_subscription(sub)
        return {
            "topics_present": [t for t in ("/diagnostics", "/diagnostics_agg") if t in present],
            "statuses": sorted(statuses.values(), key=lambda s: s["name"]),
            "window_s": window,
        }

    # ── lifecycle ──────────────────────────────────────────────────────

    def lifecycle(self, args):
        timeout = min(float(args.get("timeout_s", 2.0)), 10.0)
        try:
            from lifecycle_msgs.srv import GetState  # noqa: PLC0415
        except Exception as exc:  # noqa: BLE001
            raise ProviderError("LIFECYCLE_UNSUPPORTED", f"lifecycle_msgs unavailable: {exc}")

        states = []
        service_map = {}
        for name, ns in self.node.get_node_names_and_namespaces():
            try:
                for sname, stypes in self.node.get_service_names_and_types_by_node(name, ns):
                    if sname.endswith("/get_state") and "lifecycle_msgs/srv/GetState" in stypes:
                        full = (ns.rstrip("/") + "/" + name) if ns != "/" else "/" + name
                        service_map[full] = sname
            except Exception:  # noqa: BLE001
                continue

        for full, srv_name in sorted(service_map.items()):
            client = self.node.create_client(GetState, srv_name)
            available = client.wait_for_service(timeout_sec=min(timeout, 1.0))
            state_id, label = 0, "unknown"
            if available:
                future = client.call_async(GetState.Request())
                deadline = time.monotonic() + timeout
                while not future.done() and time.monotonic() < deadline:
                    rclpy.spin_once(self.node, timeout_sec=0.05)
                if future.done() and future.result() is not None:
                    state = future.result().current_state
                    state_id, label = state.id, state.label
            self.node.destroy_client(client)
            states.append(
                {
                    "node": full,
                    "state_id": state_id,
                    "state_label": label,
                    "services_available": bool(available),
                }
            )
        return {"states": states}

    # ── clock ──────────────────────────────────────────────────────────

    def clock(self, args):
        window = min(float(args.get("window_s", 1.0)), 5.0)
        topics = dict(self.node.get_topic_names_and_types())
        clock_present = "/clock" in topics
        advance = None
        if clock_present:
            try:
                from rosgraph_msgs.msg import Clock  # noqa: PLC0415

                seen = []

                def cb(msg):
                    seen.append(msg.clock.sec + msg.clock.nanosec / 1e9)

                sub = self.node.create_subscription(Clock, "/clock", cb, 10)
                end = time.monotonic() + window
                while time.monotonic() < end:
                    rclpy.spin_once(self.node, timeout_sec=0.05)
                self.node.destroy_subscription(sub)
                if len(seen) >= 2:
                    advance = seen[-1] - seen[0]
            except Exception:  # noqa: BLE001
                pass
        use_sim_time = None
        try:
            use_sim_time = bool(self.node.get_parameter("use_sim_time").value)
        except Exception:  # noqa: BLE001
            pass
        return {
            "system_time": time.time(),
            "ros_time": self.node.get_clock().now().nanoseconds / 1e9,
            "clock_topic_present": clock_present,
            "use_sim_time": use_sim_time,
            "clock_advance": advance,
        }


def main():
    out = sys.stdout
    provider = None
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as exc:
            out.write(json.dumps({"id": None, "ok": False, "kind": "PROVIDER_ERROR",
                                  "message": f"bad request json: {exc}"}) + "\n")
            out.flush()
            continue
        rid = req.get("id")
        op = req.get("op")
        try:
            if op == "hello":
                data = {
                    "python": sys.executable,
                    "python_version": sys.version.split()[0],
                    "rclpy": RCLPY_IMPORT_ERROR is None,
                    "rclpy_error": RCLPY_IMPORT_ERROR,
                    "pid": os.getpid(),
                }
            elif op == "shutdown":
                out.write(json.dumps({"id": rid, "ok": True, "data": {}}) + "\n")
                out.flush()
                break
            else:
                if provider is None:
                    provider = Provider()
                handler = {
                    "graph": provider.graph,
                    "sample": provider.sample,
                    "tf": provider.tf,
                    "tf_query": provider.tf_query,
                    "diagnostics": provider.diagnostics,
                    "lifecycle": provider.lifecycle,
                    "clock": provider.clock,
                    "qos_compat": provider.qos_compat,
                }.get(op)
                if handler is None:
                    raise ProviderError("PROVIDER_ERROR", f"unknown op '{op}'")
                data = handler(req.get("args", {}))
            out.write(json.dumps({"id": rid, "ok": True, "data": data}) + "\n")
        except ProviderError as exc:
            out.write(json.dumps({"id": rid, "ok": False, "kind": exc.kind,
                                  "message": exc.message}) + "\n")
        except Exception as exc:  # noqa: BLE001
            out.write(json.dumps({"id": rid, "ok": False, "kind": "PROVIDER_ERROR",
                                  "message": f"{type(exc).__name__}: {exc}"}) + "\n")
        out.flush()

    if provider is not None:
        try:
            provider.node.destroy_node()
            rclpy.shutdown()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    main()
