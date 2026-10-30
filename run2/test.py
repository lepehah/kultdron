#!/usr/bin/env python3
"""Список активных ROS 2 топиков. Запускать через board_run.sh (после source)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

print("python:", sys.executable)
print("ros2:", shutil.which("ros2") or "НЕТ В PATH")
print("ROS_DISTRO:", os.environ.get("ROS_DISTRO", "?"))
print("--- topic list ---")
if not shutil.which("ros2"):
    print("ros2 не найден. Нужен source setup.bash или docker sverk_ros2.")
    raise SystemExit(2)
proc = subprocess.run(["ros2", "topic", "list"], capture_output=True, text=True)
sys.stdout.write(proc.stdout or "")
if proc.stderr:
    sys.stderr.write(proc.stderr)
raise SystemExit(proc.returncode)
