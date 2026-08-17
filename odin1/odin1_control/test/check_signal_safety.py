#!/usr/bin/env python3
# Copyright 2026 Odin1 integration contributors
# Licensed under the Apache License, Version 2.0
"""Static guard: nothing async-signal-unsafe may appear in the signal handler.

The rule is easy to state and easy to break by accident, because the unsafe
calls are exactly the ones you reach for when writing diagnostics: a log line, a
lock around shared state, a std::string to build a message. Each of those can
deadlock the process, because a handler runs on whichever thread was interrupted
- possibly one that already holds that very lock.

That is not theoretical in this codebase. An earlier revision called
ControlServer::beginTeardown() from the handler; it takes idle_mutex_ and waits
on idle_cv_, and ~DeviceSession takes the same mutex on the worker thread. Ctrl+C
landing in that window self-deadlocked, in precisely the "a save is running"
case the call had been added to protect.

So this check is wired into the test suite rather than left to review.

Usage:  check_signal_safety.py <odin1_control package dir>
"""

from __future__ import annotations

import pathlib
import re
import sys

# The handler, and the only helper it is allowed to call.
HANDLER = "odin1SignalTrampoline"
ALLOWED_HELPERS = {"emitRaw"}

# POSIX async-signal-safe functions this code actually uses. Anything else that
# looks like a call is reported.
ALLOWED_CALLS = {"write", "_exit"} | ALLOWED_HELPERS

# Substrings that must never appear inside a signal-context function.
FORBIDDEN = [
    ("RCLCPP_", "ROS logger: allocates and takes locks"),
    ("rclcpp::", "rclcpp API"),
    ("std::mutex", "mutex"),
    ("lock_guard", "mutex"),
    ("unique_lock", "mutex"),
    ("condition_variable", "condition variable"),
    ("std::string", "allocates"),
    ("std::function", "may allocate; indirect call into arbitrary code"),
    ("std::thread", "thread API"),
    ("std::cout", "iostreams are not reentrant"),
    ("std::cerr", "iostreams are not reentrant"),
    ("printf", "stdio is not reentrant"),
    ("fprintf", "stdio is not reentrant"),
    ("malloc", "allocator is not reentrant"),
    ("new ", "allocates"),
    ("lidar_", "vendor SDK: locks and USB I/O"),
    ("exit(", "exit() runs atexit handlers and static destructors"),
]

CALL_RE = re.compile(r"(?:::)?\b([A-Za-z_]\w*)\s*\(")
# Control keywords and casts that the call regex would otherwise flag.
NOT_CALLS = {
    "if", "while", "for", "switch", "return", "sizeof", "static_cast",
    "reinterpret_cast", "const_cast", "dynamic_cast", "do", "catch",
}


def extract_function(text: str, name: str) -> str:
    """Returns the body of `name`, brace-matched."""
    m = re.search(r"\b" + re.escape(name) + r"\s*\([^)]*\)\s*\{", text)
    if not m:
        sys.exit(f"ERROR: could not find {name}() - did it get renamed?")
    i = text.index("{", m.start())
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[i:j + 1]
    sys.exit(f"ERROR: unbalanced braces in {name}()")


def strip_comments_and_strings(s: str) -> str:
    s = re.sub(r"//[^\n]*", "", s)
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    s = re.sub(r'"(\\.|[^"\\])*"', '""', s)
    return s


def check(body: str, label: str) -> list[str]:
    problems = []
    code = strip_comments_and_strings(body)

    for needle, why in FORBIDDEN:
        # `exit(` also matches `_exit(`; only flag a bare exit.
        if needle == "exit(":
            if re.search(r"(?<![_\w])exit\s*\(", code):
                problems.append(f"  [{label}] forbidden: exit()  -- {why}")
            continue
        if needle in code:
            problems.append(f"  [{label}] forbidden: {needle!r}  -- {why}")

    for call in set(CALL_RE.findall(code)) - NOT_CALLS:
        if call not in ALLOWED_CALLS:
            problems.append(
                f"  [{label}] call to {call}() is not on the async-signal-safe allowlist")
    return problems


def main() -> int:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    pkg = pathlib.Path(sys.argv[1])
    src = pkg / "src" / "signal_shutdown.cpp"
    if not src.is_file():
        sys.exit(f"ERROR: {src} not found")
    text = src.read_text(encoding="utf-8")

    problems = check(extract_function(text, HANDLER), HANDLER)
    for helper in ALLOWED_HELPERS:
        problems += check(extract_function(text, helper), helper)

    # The handler must not reach the guard's std::function members either: they
    # live on the object, and the handler has no way to see it. Confirm that by
    # checking it touches nothing but the file-static sig_atomic_t state.
    body = strip_comments_and_strings(extract_function(text, HANDLER))
    for member in ("drain_", "deferred_", "thread_", "grace_", "installed_"):
        if member in body:
            problems.append(f"  [{HANDLER}] touches guard member {member}")

    if problems:
        print("ASYNC-SIGNAL-SAFETY VIOLATIONS:")
        print("\n".join(problems))
        return 1

    print(f"ok: {HANDLER}() and {sorted(ALLOWED_HELPERS)} use only "
          f"{sorted(ALLOWED_CALLS - ALLOWED_HELPERS)} and volatile sig_atomic_t state")
    return 0


if __name__ == "__main__":
    sys.exit(main())
