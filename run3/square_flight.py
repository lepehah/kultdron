#!/usr/bin/env python3
"""
Квадрат по карте ArUco Сверх (Left_TSU.txt / Right_TSU.txt).

Формат карты:
  # dictionary: DICT_4X4_100
  # id length x y z rot_z rot_y rot_x
  25  0.225  -2  2  0  0  0  0

Координаты уже в метрах, кадр aruco_map. Не умножать на 0.75.

  python3 square_flight.py
  python3 square_flight.py Right_TSU.txt
"""
import os
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

try:
    import cv2
except Exception as exc:
    cv2 = None
    print("cv2 нет, ID меток в кадре не ищем:", exc)

HERE = os.path.dirname(os.path.abspath(__file__))
FLY_Z = 1.2
DICT_NAME_DEFAULT = "DICT_4X4_100"


def load_map(path):
    dict_name = DICT_NAME_DEFAULT
    markers = []
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            if line.lower().startswith("# dictionary:"):
                dict_name = line.split(":", 1)[1].strip()
                continue
            if line.startswith("#"):
                continue
            parts = line.replace(",", " ").split()
            markers.append({
                "id": int(parts[0]),
                "size": float(parts[1]),
                "x": float(parts[2]),
                "y": float(parts[3]),
                "z": float(parts[4]) if len(parts) > 4 else 0.0,
            })
    if not markers:
        raise RuntimeError("в карте нет меток: " + path)
    return dict_name, markers


def find_map_file(cli_path=None):
    names = []
    if cli_path:
        names.append(cli_path)
    names += [
        os.path.join(HERE, "Right_TSU.txt"),
        os.path.join(HERE, "Left_TSU.txt"),
        os.path.join(os.path.expanduser("~"), "Right_TSU.txt"),
        os.path.join(os.path.expanduser("~"), "Загрузки", "1234", "Right_TSU.txt"),
        "/home/sverk/mission/Right_TSU.txt",
        "/home/sverk/Right_TSU.txt",
    ]
    for path in names:
        if path and os.path.isfile(path):
            return path
    raise FileNotFoundError("не найден Right_TSU.txt / Left_TSU.txt")


def square_meters(markers):
    xs = [m["x"] for m in markers]
    ys = [m["y"] for m in markers]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    # обход по периметру, старт — левый верх карты (как первая строка файла)
    return [
        (xmin, ymax),
        (xmax, ymax),
        (xmax, ymin),
        (xmin, ymin),
        (xmin, ymax),
    ]


def nearest_marker(markers, x, y):
    return min(markers, key=lambda m: (m["x"] - x) ** 2 + (m["y"] - y) ** 2)


def init_drone():
    try:
        return sverk_interfaces.init(Nodename="square_flight")
    except TypeError:
        return sverk_interfaces.init(node_name="square_flight")


def goto_xy(drone, x, y, z=FLY_Z, arm=False):
    print(f"летим в aruco_map ({x:.2f}, {y:.2f}, {z:.2f})")
    return drone.control.navigate_wait(
        x=x, y=y, z=z, yaw=float("nan"),
        speed=0.35, frame_id="aruco_map", auto_arm=arm, timeout=40.0, tolerance=0.18,
    )


def print_pose(drone, label):
    t = drone.control.get_telemetry(frame_id="aruco_map")
    print(f"{label}  aruco_map=({t.x:.2f}, {t.y:.2f}, {t.z:.2f})")
    return t


def scan_ids(drone, dict_name):
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
    dic = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, dict_name, cv2.aruco.DICT_4X4_100))
    try:
        params = (
            cv2.aruco.DetectorParameters_create()
            if hasattr(cv2.aruco, "DetectorParameters_create")
            else cv2.aruco.DetectorParameters()
        )
        _c, ids, _ = cv2.aruco.detectMarkers(gray, dic, parameters=params)
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
    map_path = find_map_file(sys.argv[1] if len(sys.argv) > 1 else None)
    dict_name, markers = load_map(map_path)
    route = square_meters(markers)
    print("карта:", map_path)
    print("словарь:", dict_name, "меток:", len(markers))
    print("квадрат (м):", route)
    for x, y in route:
        m = nearest_marker(markers, x, y)
        print(f"  угол ({x:.1f},{y:.1f}) → id {m['id']} @ ({m['x']},{m['y']})")

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

        for i, (x, y) in enumerate(route):
            m = nearest_marker(markers, x, y)
            print(f"точка {i + 1}/{len(route)} цель id={m['id']}")
            goto_xy(drone, x, y, arm=True)
            print_pose(drone, f"пришли к id {m['id']}")
            scan_ids(drone, dict_name)
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
