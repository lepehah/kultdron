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

        print("режим уже", getattr(t0, "mode", "?"),
              "— auto_arm идёт сервисом set_position, не navigate_wait")
        res = drone.control.set_position(
            x=0.0, y=0.0, z=0.4,
            yaw=float("nan"),
            frame_id="body",
            auto_arm=True,
            timeout=15.0,
        )
        print("set_position auto_arm:", res)
        for name in ("success", "message", "result"):
            if hasattr(res, name):
                print(f"  .{name}={getattr(res, name)}")

        time.sleep(1.5)
        t = dump(drone, "после ARM")
        armed = bool(getattr(t, "armed", False))
        print("ARMED =", armed)
        if not armed:
            print(
                "PX4 не дал ARM (не ошибка Python). Проверь:\n"
                "  1) предохранитель / safety на плате\n"
                "  2) батарея и пропеллеры\n"
                "  3) QGC / Sverk web: почему prearm\n"
                "  4) пульт: разрешён offboard, не kill\n"
                "Скрипт видит дрон (connected + OFFBOARD + координаты) — дальше блокирует автопилот."
            )
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
