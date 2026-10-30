#!/usr/bin/env python3
"""Первый полёт одного дрона (из zz6bezaruci): змейка своей половины, поиск цветов."""
import numpy_fix  # noqa: F401

import argparse
import math
import time

import cv2
import numpy as np

from sverk_boot import init_drone

CAM_WIDTH = 320
CAM_HEIGHT = 240
CAM_FOV_DEG = 65.0
CAMERA_FORWARD_M = 0.0655
ARUCO_SCALE = 0.75

COLORS_HSV = {
    "красный": ([0, 120, 120], [10, 255, 255]),
    "жёлтый": ([18, 120, 150], [32, 255, 255]),
    "зелёный": ([45, 90, 120], [75, 255, 255]),
}

ROUTE_LEFT = [(0, 0), (0, 4), (1, 4), (1, 0), (2, 0), (2, 4)]
ROUTE_RIGHT = [(3, 0), (3, 4), (4, 4), (4, 0)]


def parse_route(route_str):
    route = []
    for pt in route_str.replace(";", " ").split():
        x_str, y_str = pt.split(",")
        route.append((float(x_str), float(y_str)))
    return route


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


def goto(drone, x, y, z, frame="aruco_map", arm=False):
    return drone.control.navigate_wait(
        x=x, y=y, z=z, yaw=float("nan"),
        speed=0.35, frame_id=frame, auto_arm=arm, timeout=30.0, tolerance=0.18,
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


def pixel_to_aruco_coords(drone, row, col, aruco_scale=ARUCO_SCALE):
    telem = drone.control.get_telemetry(frame_id="aruco_map")
    if math.isnan(telem.x) or math.isnan(telem.y):
        return None, None, None, None
    altitude = telem.z if telem.z > 0.2 else 1.2
    yaw = 0.0 if math.isnan(telem.yaw) else telem.yaw
    focal_px = (CAM_WIDTH / 2.0) / math.tan(math.radians(CAM_FOV_DEG / 2.0))
    body_forward = CAMERA_FORWARD_M + ((CAM_HEIGHT / 2.0 - row) / focal_px) * altitude
    body_left = ((CAM_WIDTH / 2.0 - col) / focal_px) * altitude
    aruco_map_x = telem.x + body_forward * math.cos(yaw) - body_left * math.sin(yaw)
    aruco_map_y = telem.y + body_forward * math.sin(yaw) + body_left * math.cos(yaw)
    return aruco_map_x, aruco_map_y, aruco_map_x / aruco_scale, aruco_map_y / aruco_scale


def generate_dense_route(waypoints, step_aruco=0.5):
    if not waypoints:
        return []
    dense = [waypoints[0]]
    for i in range(1, len(waypoints)):
        x0, y0 = waypoints[i - 1]
        x1, y1 = waypoints[i]
        dist = math.hypot(x1 - x0, y1 - y0)
        steps = max(1, int(round(dist / step_aruco)))
        for s in range(1, steps + 1):
            t = s / steps
            dense.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
    return dense


def fly_and_scan(drone, route, z=1.2, frame="aruco_map", tolerance=0.18, min_pixels=800):
    dense = generate_dense_route(route, step_aruco=0.5)
    found = {name: [] for name in COLORS_HSV}

    for p in dense:
        map_x = p[0] * ARUCO_SCALE
        map_y = p[1] * ARUCO_SCALE
        drone.control.navigate(x=map_x, y=map_y, z=z, yaw=0.0, speed=0.4, frame_id=frame)
        while True:
            t = drone.control.get_telemetry(frame_id="navigate_target")
            if math.hypot(t.x, t.y, t.z) < tolerance:
                break
            time.sleep(0.08)

        frame_img = grab_frame_rgb(drone)
        if frame_img is None:
            continue
        hsv = cv2.cvtColor(frame_img, cv2.COLOR_RGB2HSV)
        for color_name, (low_vals, high_vals) in COLORS_HSV.items():
            mask = cv2.inRange(hsv, np.array(low_vals), np.array(high_vals))
            if cv2.countNonZero(mask) < min_pixels:
                continue
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for cnt in contours:
                if cv2.contourArea(cnt) < min_pixels * 0.7:
                    continue
                M = cv2.moments(cnt)
                if M["m00"] == 0:
                    continue
                col = M["m10"] / M["m00"]
                row = M["m01"] / M["m00"]
                map_x, map_y, ax, ay = pixel_to_aruco_coords(drone, row, col)
                if ax is None:
                    continue
                if any(abs(ax - ex) < 1.0 and abs(ay - ey) < 1.0 for ex, ey in found[color_name]):
                    continue
                found[color_name].append((ax, ay))
                print(f"новый '{color_name}' aruco=({ax:.2f}, {ay:.2f})")

    print("=== ИТОГИ ===")
    for name, pts in found.items():
        print(f"  {name}: {len(pts)} {pts}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--drone", type=int, choices=[1, 2], default=1)
    parser.add_argument("route", nargs="?", help="Маршрут: '0,0 0,4 1,4'")
    args = parser.parse_args()

    if args.route:
        route = parse_route(args.route)
    else:
        route = ROUTE_LEFT if args.drone == 1 else ROUTE_RIGHT
    print(f"борт {args.drone} маршрут {route}")

    drone = init_drone(f"first_flight_{args.drone}")
    try:
        drone.drone_id = f"drone_{args.drone}"
    except Exception:
        pass

    try:
        prepare_camera(drone)
        print("Взлёт...")
        goto(drone, 0, 0, 1, frame="body", arm=True)
        start_x, start_y = route[0][0] * ARUCO_SCALE, route[0][1] * ARUCO_SCALE
        goto(drone, start_x, start_y, 1, frame="aruco_map", arm=True)
        fly_and_scan(drone, route, z=1.2)
    finally:
        try:
            land_and_wait(drone)
        finally:
            drone.close()


if __name__ == "__main__":
    main()
