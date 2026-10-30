#!/usr/bin/env python3
"""
Квадрат по ArUco: (0,0) → (4,0) → (4,4) → (0,4) → (0,0), печать координат, посадка.

На дроне (в контейнере / после source ROS):

  python3 square_flight.py

Если import sverk_interfaces не находится:

  source /opt/ros/humble/setup.bash
  source ~/sverk_ws/install/setup.bash
  python3 square_flight.py

или:

  docker exec -it $(docker ps --format '{{.Names}}' | grep -i sverk | head -1) bash
  python3 /home/sverk/mission/square_flight.py
"""
import sys

SYSTEM = "/usr/lib/python3/dist-packages"
_paths = [p for p in sys.path if p != SYSTEM]
_first_local = next((i for i, p in enumerate(_paths) if p.startswith("/usr/local/lib/python3")), None)
if SYSTEM not in sys.path:
    pass
elif _first_local is not None:
    _paths.insert(_first_local, SYSTEM)
    sys.path[:] = _paths

import math
import time

import sverk_interfaces

try:
    import cv2
except Exception as exc:
    cv2 = None
    print("cv2 нет, метки в кадре не ищем:", exc)

ARUCO_SCALE = 0.75
FLY_Z = 1.2
SQUARE = [(0, 0), (4, 0), (4, 4), (0, 4), (0, 0)]


def init_drone():
    try:
        return sverk_interfaces.init(Nodename="square_flight")
    except TypeError:
        return sverk_interfaces.init(node_name="square_flight")


def goto_cell(drone, cx, cy, z=FLY_Z, arm=False):
    x, y = cx * ARUCO_SCALE, cy * ARUCO_SCALE
    print(f"летим в клетку ({cx},{cy})  aruco_map=({x:.2f},{y:.2f})")
    return drone.control.navigate_wait(
        x=x, y=y, z=z, yaw=float("nan"),
        speed=0.35, frame_id="aruco_map", auto_arm=arm, timeout=40.0, tolerance=0.18,
    )


def print_pose(drone, label):
    t = drone.control.get_telemetry(frame_id="aruco_map")
    ax = t.x / ARUCO_SCALE if not math.isnan(t.x) else float("nan")
    ay = t.y / ARUCO_SCALE if not math.isnan(t.y) else float("nan")
    print(f"{label}  map=({t.x:.2f},{t.y:.2f},{t.z:.2f})  клетка≈({ax:.1f},{ay:.1f})")


def scan_ids(drone):
    if cv2 is None:
        return
    frame = None
    cam = getattr(drone, "camera", None)
    if cam is not None and hasattr(cam, "read_numpy"):
        try:
            frame = cam.read_numpy(encoding="rgb8", timeout=0.5)
        except Exception:
            frame = None
    if frame is None:
        image = getattr(drone, "image", None)
        if image is not None and hasattr(image, "take_picture"):
            try:
                bgr = image.take_picture(timeout=1.5)
                if bgr is not None:
                    frame = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            except Exception:
                frame = None
    if frame is None:
        print("  камера: нет кадра")
        return
    gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_1000)
    try:
        if hasattr(cv2.aruco, "detectMarkers"):
            params = (
                cv2.aruco.DetectorParameters_create()
                if hasattr(cv2.aruco, "DetectorParameters_create")
                else cv2.aruco.DetectorParameters()
            )
            _c, ids, _ = cv2.aruco.detectMarkers(gray, dictionary, parameters=params)
        else:
            ids = None
    except Exception as exc:
        print("  ArUco:", exc)
        return
    if ids is None:
        print("  ArUco в кадре нет")
        return
    print("  ArUco ID:", sorted({int(i) for i in ids.reshape(-1).tolist()}))


def land(drone, timeout=40.0):
    drone.control.land()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        st = drone.control.get_telemetry(frame_id="map")
        if not st.armed and math.hypot(st.vx, st.vy, st.vz) < 0.1:
            print("LANDED")
            return
        time.sleep(0.1)
    print("посадка не подтверждена по телеметрии")


def main():
    print("квадрат", SQUARE)
    drone = init_drone()
    try:
        cam = getattr(drone, "camera", None)
        if cam is not None and hasattr(cam, "configure"):
            try:
                cam.configure(width=320, height=240, fov=65.0, frame_rate=10)
            except Exception as exc:
                print("камера configure:", exc)

        print("взлёт body")
        drone.control.navigate_wait(
            x=0, y=0, z=1.0, yaw=float("nan"),
            speed=0.35, frame_id="body", auto_arm=True, timeout=30.0, tolerance=0.18,
        )

        for i, (cx, cy) in enumerate(SQUARE):
            goto_cell(drone, cx, cy, arm=True)
            print_pose(drone, f"точка {i + 1}/{len(SQUARE)}")
            scan_ids(drone)
            time.sleep(0.3)

        print("посадка")
        land(drone)
    finally:
        try:
            drone.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
