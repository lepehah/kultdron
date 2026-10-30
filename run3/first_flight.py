#!/usr/bin/env python3
"""Первый полёт: квадрат по меткам ArUco, печать координат, возврат на старт."""
import numpy_fix  # noqa: F401

import argparse
import math
import time

import cv2

from sverk_boot import init_drone

CAM_WIDTH = 320
CAM_HEIGHT = 240
CAM_FOV_DEG = 65.0
ARUCO_SCALE = 0.75
FLY_Z = 1.2

# Квадрат по углам поля 5×5 и обратно в (0,0)
SQUARE = [(0, 0), (4, 0), (4, 4), (0, 4), (0, 0)]

def make_aruco_tools():
    """На плате часто OpenCV 4.5: нет ArucoDetector, есть detectMarkers."""
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_1000)
    params = cv2.aruco.DetectorParameters_create() if hasattr(cv2.aruco, "DetectorParameters_create") else cv2.aruco.DetectorParameters()
    detector = None
    if hasattr(cv2.aruco, "ArucoDetector"):
        try:
            detector = cv2.aruco.ArucoDetector(dictionary, params)
        except Exception:
            detector = None
    return dictionary, params, detector


ARUCO_DICT, ARUCO_PARAMS, ARUCO_DETECTOR = make_aruco_tools()


def require_success(result, action):
    success = result.get("success", False) if isinstance(result, dict) else getattr(result, "success", False)
    if not success:
        raise RuntimeError(action + ": команда не подтверждена")


def prepare_camera(drone):
    cam = getattr(drone, "camera", None)
    if cam is not None and hasattr(cam, "configure"):
        require_success(
            cam.configure(width=CAM_WIDTH, height=CAM_HEIGHT, fov=CAM_FOV_DEG, frame_rate=10),
            "Камера",
        )


def goto_aruco(drone, cell_x, cell_y, z=FLY_Z, arm=False):
    x = cell_x * ARUCO_SCALE
    y = cell_y * ARUCO_SCALE
    print(f"летим в клетку ({cell_x:.0f},{cell_y:.0f})  aruco_map=({x:.2f},{y:.2f})")
    return drone.control.navigate_wait(
        x=x, y=y, z=z, yaw=float("nan"),
        speed=0.35, frame_id="aruco_map", auto_arm=arm, timeout=40.0, tolerance=0.18,
    )


def land_and_wait(drone, timeout=40.0):
    require_success(drone.control.land(), "Посадка")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = drone.control.get_telemetry(frame_id="map")
        if not state.armed and math.hypot(state.vx, state.vy, state.vz) < 0.1:
            print("LANDED")
            return
        time.sleep(0.1)
    raise TimeoutError("Посадка не подтверждена")


def grab_frame_rgb(drone):
    cam = getattr(drone, "camera", None)
    if cam is not None and hasattr(cam, "read_numpy"):
        try:
            img = cam.read_numpy(encoding="rgb8", timeout=0.5)
            if img is not None:
                return img
        except Exception:
            pass
    image = getattr(drone, "image", None)
    if image is not None and hasattr(image, "take_picture"):
        bgr = image.take_picture(timeout=1.5)
        if bgr is not None:
            return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    return None


def print_pose(drone, label):
    telem = drone.control.get_telemetry(frame_id="aruco_map")
    ax = telem.x / ARUCO_SCALE if not math.isnan(telem.x) else float("nan")
    ay = telem.y / ARUCO_SCALE if not math.isnan(telem.y) else float("nan")
    print(
        f"{label}  aruco_map=({telem.x:.2f}, {telem.y:.2f}, {telem.z:.2f})  "
        f"клетка≈({ax:.1f}, {ay:.1f})"
    )
    return telem


def scan_aruco(drone):
    frame = grab_frame_rgb(drone)
    if frame is None:
        print("  камера: нет кадра")
        return []
    gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
    corners, ids, _ = detector.detectMarkers(gray)
    if ids is None or len(ids) == 0:
        print("  ArUco в кадре нет")
        return []
    found = sorted({int(i) for i in ids.reshape(-1).tolist()})
    print(f"  ArUco в кадре: {found}")
    return found


def fly_square(drone, square):
    seen = []
    start = square[0]
    print("Взлёт на месте...")
    drone.control.navigate_wait(
        x=0, y=0, z=1.0, yaw=float("nan"),
        speed=0.35, frame_id="body", auto_arm=True, timeout=30.0, tolerance=0.18,
    )
    for i, (cx, cy) in enumerate(square):
        goto_aruco(drone, cx, cy, z=FLY_Z, arm=True)
        print_pose(drone, f"точка {i + 1}/{len(square)} клетка ({cx},{cy})")
        ids = scan_aruco(drone)
        seen.append({"cell": (cx, cy), "ids": ids})
        time.sleep(0.4)

    print("=== КООРДИНАТЫ КВАДРАТА ===")
    for item in seen:
        print(f"  клетка {item['cell']}  метки {item['ids']}")
    print(f"возврат в старт {start} и посадка")
    return seen


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--drone", type=int, choices=[1, 2], default=1)
    args = parser.parse_args()
    print(f"борт {args.drone} квадрат {SQUARE}")

    drone = init_drone(f"first_flight_{args.drone}")
    try:
        drone.drone_id = f"drone_{args.drone}"
    except Exception:
        pass

    try:
        prepare_camera(drone)
        fly_square(drone, SQUARE)
    finally:
        try:
            land_and_wait(drone)
        finally:
            drone.close()


if __name__ == "__main__":
    main()
