#!/usr/bin/env python3
"""
Проверка ARM. Без сайта, один файл.

  source /opt/ros/humble/setup.bash
  source ~/sverk_ws/install/setup.bash
  python3 arm_test.py

Моторы должны закрутиться / дрон чуть оторваться (body z=0.4 м), в терминале armed=True, затем посадка.
"""
import sys

SYSTEM = "/usr/lib/python3/dist-packages"
_paths = [p for p in sys.path if p != SYSTEM]
_first_local = next((i for i, p in enumerate(_paths) if p.startswith("/usr/local/lib/python3")), None)
if SYSTEM in sys.path and _first_local is not None:
    _paths.insert(_first_local, SYSTEM)
    sys.path[:] = _paths

import math
import time

import sverk_interfaces


def init_drone():
    try:
        return sverk_interfaces.init(Nodename="arm_test")
    except TypeError:
        return sverk_interfaces.init(node_name="arm_test")


def dump(drone, title):
    print("===", title, "===")
    t = drone.control.get_telemetry(frame_id="map")
    fields = (
        "connected", "armed", "mode",
        "x", "y", "z", "yaw",
        "vx", "vy", "vz",
    )
    for name in fields:
        print(f"  {name}={getattr(t, name, '?')}")
    return t


def main():
    print("init sverk_interfaces...")
    drone = init_drone()
    try:
        dump(drone, "до ARM")

        armed = False
        if hasattr(drone.control, "arm"):
            print("вызов control.arm()")
            res = drone.control.arm()
            print("  ответ:", res)
        else:
            print("control.arm() нет — взлёт body z=0.4 с auto_arm=True")
            res = drone.control.navigate_wait(
                x=0.0, y=0.0, z=0.4,
                yaw=float("nan"),
                speed=0.3,
                frame_id="body",
                auto_arm=True,
                timeout=20.0,
                tolerance=0.2,
            )
            print("  navigate_wait:", res)

        time.sleep(1.0)
        t = dump(drone, "после ARM")
        armed = bool(getattr(t, "armed", False))
        print("ARMED =" , armed)
        if not armed:
            print("НЕ заармился. Часто: нет offboard, не видит ArUco, предохранитель, RC не в offboard.")
        else:
            print("ок, держим 3 секунды")
            time.sleep(3.0)

        print("посадка / disarm")
        if hasattr(drone.control, "land"):
            drone.control.land()
        elif hasattr(drone.control, "disarm"):
            drone.control.disarm()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            st = drone.control.get_telemetry(frame_id="map")
            if not st.armed:
                print("DISARMED")
                break
            time.sleep(0.2)
        dump(drone, "в конце")
    finally:
        try:
            drone.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
