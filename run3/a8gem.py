import numpy_fix  # noqa: F401 — до cv2/numpy/sverk_interfaces, иначе в контейнере падает OpenCV

import argparse
import base64
import math
import os
import sys
import time
import sverk_interfaces
import cv2
import numpy as np

from plan_paths import load_matrix_json, plan_mission, ascii_plan
from sverk_boot import init_drone
from ui_report import UiReporter
import mission_log as mlog

# Константы камеры из рабочего примера
CAM_WIDTH = 320
CAM_HEIGHT = 240
CAM_FOV_DEG = 65.0
CAMERA_FORWARD_M = 0.0655  # Смещение нижней камеры вперёд по body FLU

COLORS_HSV = {
    "красный": ([0, 120, 120], [10, 255, 255]),
    "жёлтый": ([18, 120, 150], [32, 255, 255]),
    "зелёный": ([45, 90, 120], [75, 255, 255]),
}

dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_1000)
detector = cv2.aruco.ArucoDetector(dictionary, cv2.aruco.DetectorParameters())


def require_success(result, action):
    success = result.get("success", False) if isinstance(result, dict) else getattr(result, "success", False)
    if not success:
        mlog.error("%s: команда не подтверждена, ответ=%s", action, result)
        raise RuntimeError(action + ": команда не подтверждена")
    mlog.debug("%s: ok", action)


def prepare_camera(drone):
    cam = getattr(drone, "camera", None)
    if cam is not None and hasattr(cam, "configure"):
        require_success(
            cam.configure(width=CAM_WIDTH, height=CAM_HEIGHT, fov=CAM_FOV_DEG, frame_rate=10),
            "Камера"
        )
        return
    mlog.info("курс-камера configure нет, берём кадры через drone.image.take_picture")


def goto(drone, x, y, z, frame="map", arm=False):
    return drone.control.navigate_wait(
        x=x, y=y, z=z, yaw=float("nan"),
        speed=0.35, frame_id=frame, auto_arm=arm, timeout=30.0, tolerance=0.18
    )


def land_and_wait(drone, timeout=40.0):
    require_success(drone.control.land(), "Посадка")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = drone.control.get_telemetry(frame_id="map")
        if not state.armed and math.hypot(state.vx, state.vy, state.vz) < 0.1:
            mlog.info("Посадка подтверждена (%s)", drone.drone_id)
            return
        time.sleep(0.1)
    mlog.error("Посадка не подтверждена за %.0f с", timeout)
    raise TimeoutError("Посадка не подтверждена")


def close_after_landing(drone):
    try:
        land_and_wait(drone)
    except Exception:
        mlog.exception("Ошибка при посадке")
        raise
    finally:
        try:
            drone.close()
        except Exception:
            mlog.exception("Ошибка drone.close()")


def pixel_to_aruco_coords(drone, row, col, aruco_scale=0.75):
    """
    Переводит пиксель (row, col) на кадре 320x240 (fov=65):
1. Пиксель -> смещение по body (в метрах) с учётом реальной высоты и fov=65

    2. body -> глобальная карта map (с учётом текущего положения и угла yaw дрона)
    3. map -> координаты ArUco-сетки (0..4)
    """
    telem = drone.control.get_telemetry(frame_id="map")
    altitude = telem.z if telem.z > 0.2 else 1.2
    yaw = 0.0 if math.isnan(telem.yaw) else telem.yaw

    # Фокусное расстояние в пикселях из горизонтального угла обзора fov=65°
    focal_px = (CAM_WIDTH / 2.0) / math.tan(math.radians(CAM_FOV_DEG / 2.0))

    # 1. Смещение относительно центра кадра в системе координат корпуса (body FLU):
    # Верх кадра (row < 120) — это впереди дрона (+forward)
    # Левая часть кадра (col < 160) — это слева от дрона (+left)
    center_row = CAM_HEIGHT / 2.0
    center_col = CAM_WIDTH / 2.0

    body_forward = CAMERA_FORWARD_M + ((center_row - row) / focal_px) * altitude
    body_left = ((center_col - col) / focal_px) * altitude

    # 2. Перевод из body в обычную карту (map) с учётом поворота дрона (yaw)
    map_x = telem.x + body_forward * math.cos(yaw) - body_left * math.sin(yaw)
    map_y = telem.y + body_forward * math.sin(yaw) + body_left * math.cos(yaw)

    # 3. Перевод из обычной карты (от -1.5 до +1.5 м) в сетку ArUco (от 0 до 4)
    aruco_x = (map_x + 1.5) / aruco_scale
    aruco_y = (map_y + 1.5) / aruco_scale

    return map_x, map_y, aruco_x, aruco_y


def generate_dense_route(waypoints, step_aruco=0.5):
    """
    Разбивает длинные пролёты (например, от (0,0) до (0,4)) на промежуточные точки
    с шагом 0.5 клетки ArUco (0.375 м), чтобы дрон делал чёткие замеры без пропусков.
    """
    dense = []
    for i in range(len(waypoints)):
        if i == 0:
            dense.append(waypoints[0])
            continue
        x0, y0 = waypoints[i - 1]
        x1, y1 = waypoints[i]
        dist = math.hypot(x1 - x0, y1 - y0)
        steps = max(1, int(round(dist / step_aruco)))
        for s in range(1, steps + 1):
            t = s / steps
            dense.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
    return dense


def grab_frame_rgb(drone):
    """RGB-кадр: сначала курс-API camera.read_numpy, иначе официальный image.take_picture (BGR)."""
    cam = getattr(drone, "camera", None)
    if cam is not None and hasattr(cam, "read_numpy"):
        try:
            img = cam.read_numpy(encoding="rgb8", timeout=0.5)
            if img is not None:
                return img
        except Exception as exc:
            mlog.warning("camera.read_numpy: %s", exc)
    image = getattr(drone, "image", None)
    if image is not None and hasattr(image, "take_picture"):
        try:
            bgr = image.take_picture(timeout=1.5)
            if bgr is not None:
                return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        except Exception as exc:
            mlog.warning("image.take_picture: %s", exc)
    return None


def frame_to_b64(frame_img):
    bgr = cv2.cvtColor(frame_img, cv2.COLOR_RGB2BGR)
    small = cv2.resize(bgr, (120, 120))
    ok, buf = cv2.imencode(".jpg", small, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
    if not ok:
        return None
    return base64.b64encode(buf).decode("utf-8")


def read_battery(drone):
    try:
        telem = drone.control.get_telemetry(frame_id="map")
        for attr in ("voltage", "battery", "batt_voltage"):
            val = getattr(telem, attr, None)
            if val is not None and not (isinstance(val, float) and math.isnan(val)):
                return float(val)
    except Exception:
        return None
    return None


def scan_frame(drone, found_colors, found_aruco, aruco_scale=0.75, min_pixels=800,
               ui=None, drone_num=1, z=1.2):
    frame_img = grab_frame_rgb(drone)
    if frame_img is None:
        mlog.warning("[%s] пустой кадр камеры", getattr(drone, "drone_id", "?"))
        return

    gray = cv2.cvtColor(frame_img, cv2.COLOR_RGB2GRAY)
    corners, ids, _ = detector.detectMarkers(gray)
    if ids is not None and len(ids) > 0:
        for marker_id in ids.reshape(-1).tolist():
            if marker_id not in found_aruco:
                found_aruco.add(marker_id)
                mlog.info("[%s] ArUco ID=%s", drone.drone_id, marker_id)

    hsv = cv2.cvtColor(frame_img, cv2.COLOR_RGB2HSV)
    for color_name, (low_vals, high_vals) in COLORS_HSV.items():
        mask = cv2.inRange(hsv, np.array(low_vals), np.array(high_vals))
        if cv2.countNonZero(mask) < min_pixels:
            continue
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < min_pixels * 0.7:
                continue
            M = cv2.moments(cnt)
            if M["m00"] == 0:
                continue
            col = M["m10"] / M["m00"]
            row = M["m01"] / M["m00"]
            map_x, map_y, aruco_x, aruco_y = pixel_to_aruco_coords(
                drone, row, col, aruco_scale=aruco_scale
            )
            is_new = True
            for (ex_ax, ex_ay) in found_colors[color_name]:
                if abs(aruco_x - ex_ax) < 1.0 and abs(aruco_y - ex_ay) < 1.0:
                    is_new = False
                    break
            if is_new:
                found_colors[color_name].append((aruco_x, aruco_y))
                mlog.info(
                    "[%s] новый '%s' площадь=%s px map=(%.2f, %.2f) aruco=(%.2f, %.2f)",
                    drone.drone_id, color_name, int(area), map_x, map_y, aruco_x, aruco_y,
                )
                if ui:
                    ui.send_vision(
                        drone_num, aruco_x, aruco_y, z, color_name,
                        message=f"Найден {color_name}",
                        image_b64=frame_to_b64(frame_img),
                    )


def print_scan_summary(found_aruco, found_colors):
    mlog.info("=== ИТОГИ ПОИСКОВОЙ МИССИИ ===")
    mlog.info("ArUco: %s шт. ID=%s", len(found_aruco), sorted(list(found_aruco)))
    total_colors = 0
    for color_name, markers_list in found_colors.items():
        count = len(markers_list)
        total_colors += count
        coords_str = ", ".join([f"({ax:.1f}, {ay:.1f})" for ax, ay in markers_list])
        mlog.info("  %s: %s шт. [%s]", color_name, count, coords_str)
    mlog.info("Всего цветных меток: %s", total_colors)


def fly_and_scan_one(drone, route, aruco_scale=0.75, z=1.2,
                     frame="map", tolerance=0.18, min_pixels=800,
                     ui=None, drone_num=1):
    """Один дрон: своя зона из matrix.json, на каждой точке — кадр и поиск цветов."""
    dense = generate_dense_route(route, step_aruco=0.5)
    found_colors = {color: [] for color in COLORS_HSV.keys()}
    found_aruco = set()

    mlog.info("Маршрут: %s точек (плотный %s)", len(route), len(dense))
    for i, p in enumerate(dense):
        map_x = (p[0] * aruco_scale) - 1.5
        map_y = (p[1] * aruco_scale) - 1.5
        mlog.debug("точка %s/%s aruco=(%.2f, %.2f) map=(%.2f, %.2f)",
                   i + 1, len(dense), p[0], p[1], map_x, map_y)
        try:
            drone.control.navigate(x=map_x, y=map_y, z=z, yaw=0.0, speed=0.4, frame_id=frame)
        except Exception:
            mlog.exception("navigate к (%.2f, %.2f) не удался", map_x, map_y)
            raise
        deadline = time.monotonic() + 30.0
        while True:
            try:
                t = drone.control.get_telemetry(frame_id="navigate_target")
            except Exception:
                mlog.exception("телеметрия navigate_target недоступна")
                raise
            if math.hypot(t.x, t.y, t.z) < tolerance:
                break
            if time.monotonic() > deadline:
                mlog.error("не долетели до (%.2f, %.2f) за 30 с, остаток hypot=%.3f",
                           map_x, map_y, math.hypot(t.x, t.y, t.z))
                break
            time.sleep(0.08)
        mlog.info("прибыл в клетку (%.1f, %.1f)", p[0], p[1])
        if ui:
            ui.send_telemetry(drone_num, p[0], p[1], battery=read_battery(drone))
        scan_frame(
            drone, found_colors, found_aruco,
            aruco_scale=aruco_scale, min_pixels=min_pixels,
            ui=ui, drone_num=drone_num, z=z,
        )

    print_scan_summary(found_aruco, found_colors)


def default_matrix_path():
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, "matrix.json"),
        os.path.join(here, "matrix_1 (1)", "matrix (1).json"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def load_plan(matrix_path):
    data = load_matrix_json(matrix_path)
    plan = plan_mission(data["matrix"], data["drone1"], data["drone2"])
    mlog.info("\n%s", ascii_plan(data["matrix"], plan))
    return plan


def main():
    parser = argparse.ArgumentParser(
        description="Полёт одного дрона по своей зоне из matrix.json (на каждой OrangePi свой --drone)"
    )
    parser.add_argument(
        "matrix",
        nargs="?",
        default=default_matrix_path(),
        help="Путь к matrix.json (кнопка «Скачать matrix.json» на сайте)",
    )
    parser.add_argument(
        "--drone",
        type=int,
        choices=[1, 2],
        required=True,
        help="Какой это борт: 1 = 192.168.1.19, 2 = 192.168.1.27",
    )
    parser.add_argument(
        "--ui",
        default=os.environ.get("SVERK_UI", ""),
        help="Адрес панели на ноутбуке, например http://192.168.1.10:8080",
    )
    args = parser.parse_args()
    log_file = mlog.setup(name=f"mission_d{args.drone}", filename=f"mission_d{args.drone}.log")
    mlog.info("старт argv=%s log=%s", sys.argv, log_file)
    if not args.matrix:
        mlog.error("Нет matrix.json")
        raise SystemExit("Не найден matrix.json. Скачайте его с сайта и закиньте на плату.")

    try:
        data = load_matrix_json(args.matrix)
        plan = load_plan(args.matrix)
        route = plan["route1"] if args.drone == 1 else plan["route2"]
        zone = plan["assignment"]["drone1"] if args.drone == 1 else plan["assignment"]["drone2"]
        mlog.info("борт %s зона=%s точек=%s ui=%s", args.drone, zone, len(route), args.ui or "(нет)")

        ui = UiReporter(args.ui)
        ui.send_plan(plan, data["matrix"])

        drone = init_drone(f"course_pixel_aruco_{args.drone}")
        try:
            drone.drone_id = f"drone_{args.drone}"
        except Exception:
            pass
        mlog.info("sverk_interfaces.init ok борт=%s", args.drone)
        try:
            prepare_camera(drone)
            mlog.info("взлёт на месте")
            goto(drone, 0, 0, 1, frame="body", arm=True)
            start = route[0] if route else (0, 0)
            ui.send_telemetry(args.drone, start[0], start[1], battery=read_battery(drone))
            fly_and_scan_one(drone, route, z=1.2, ui=ui, drone_num=args.drone)
            mlog.info("миссия борта %s завершена", args.drone)
        finally:
            close_after_landing(drone)
    except Exception:
        mlog.exception("миссия оборвалась")
        raise


if __name__ == "__main__":
    main()