#!/usr/bin/env python3
"""Пинг с OrangePi: SSH, ROS/sverk, телеметрия, камера → карточка на сайте."""
from __future__ import annotations

import numpy_fix  # noqa: F401 — до cv2/numpy/sverk_interfaces

import argparse
import base64
import math
import os
import sys
import traceback

import mission_log as mlog
from sverk_boot import init_drone
from ui_report import UiReporter

ARUCO_SCALE = 0.75


def _attr(obj, names):
    for name in names:
        if obj is None:
            return None
        val = getattr(obj, name, None)
        if val is None:
            continue
        if isinstance(val, float) and math.isnan(val):
            continue
        return val
    return None


def read_battery(drone):
    telem = None
    try:
        telem = drone.control.get_telemetry(frame_id="map")
    except Exception:
        telem = None
    val = _attr(telem, ("voltage", "battery", "batt_voltage", "battery_voltage", "vbat", "voltage_v"))
    if val is not None:
        return float(val)
    try:
        from sensor_msgs.msg import BatteryState
        msg = drone.topic.wait_for_message(BatteryState, "/fmu/out/battery_status", timeout=1.0)
        if msg is not None and getattr(msg, "voltage", 0) not in (None, 0):
            return float(msg.voltage)
    except Exception:
        return val
    return None


def grab_frame_b64(drone):
    frame = None
    note = ""
    cam = getattr(drone, "camera", None)
    if cam is not None and hasattr(cam, "configure"):
        try:
            cam.configure(width=320, height=240, fov=65.0, frame_rate=10)
        except Exception as exc:
            mlog.warning("камера configure: %s", exc)
    if cam is not None and hasattr(cam, "read_numpy"):
        try:
            frame = cam.read_numpy(encoding="rgb8", timeout=1.5)
            note = "read_numpy"
        except Exception as exc:
            mlog.warning("camera.read_numpy: %s", exc)
            note = str(exc)
    if frame is None:
        image = getattr(drone, "image", None)
        if image is not None and hasattr(image, "take_picture"):
            try:
                bgr = image.take_picture(timeout=2.0)
                if bgr is not None:
                    import cv2
                    frame = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                    note = "take_picture"
            except Exception as exc:
                mlog.warning("image.take_picture: %s", exc)
                note = str(exc)
    if frame is None:
        return None, note or "пустой кадр"
    try:
        import cv2
        bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        small = cv2.resize(bgr, (120, 120))
        ok, buf = cv2.imencode(".jpg", small, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
        if not ok:
            return None, "jpeg не собрался"
        return base64.b64encode(buf).decode("utf-8"), f"{note} {frame.shape[1]}x{frame.shape[0]}"
    except Exception as exc:
        return None, str(exc)


def map_to_aruco(x, y):
    return (x + 1.5) / ARUCO_SCALE, (y + 1.5) / ARUCO_SCALE


def main() -> int:
    parser = argparse.ArgumentParser(description="Пинг дрона на панель")
    parser.add_argument("--drone", type=int, choices=[1, 2], required=True)
    parser.add_argument("--ui", default=os.environ.get("SVERK_UI", ""))
    args = parser.parse_args()

    mlog.setup(name=f"ping_d{args.drone}", filename=f"ping_d{args.drone}.log")
    ui = UiReporter(args.ui)
    lines = [f"пинг борта {args.drone}"]

    try:
        import sverk_interfaces
        lines.append("sverk_interfaces: импорт ok")
        drone = init_drone(f"drone_ping_{args.drone}")
        try:
            drone.drone_id = f"drone_{args.drone}"
        except Exception:
            pass
        lines.append("init ok")
        try:
            try:
                telem = drone.control.get_telemetry(frame_id="map", timeout=2.0)
            except TypeError:
                telem = drone.control.get_telemetry(frame_id="map")
            ax, ay = map_to_aruco(float(telem.x), float(telem.y))
            lines.append(
                f"связь={getattr(telem, 'connected', '?')} armed={getattr(telem, 'armed', '?')} "
                f"mode={getattr(telem, 'mode', '?')} map=({telem.x:.2f},{telem.y:.2f},{telem.z:.2f}) "
                f"aruco=({ax:.1f},{ay:.1f})"
            )
            bat = read_battery(drone)
            if bat is not None:
                lines.append(f"батарея {bat:.2f} В")
                ui.send_telemetry(args.drone, ax, ay, battery=bat)
            else:
                lines.append("батарея: нет в телеметрии")
                ui.send_telemetry(args.drone, ax, ay)

            img_b64, cam_note = grab_frame_b64(drone)
            lines.append("камера: " + cam_note)
            ui.send_vision(
                args.drone, ax, ay, float(telem.z),
                color="",
                message=" | ".join(lines),
                confidence=1.0,
                image_b64=img_b64,
            )
            mlog.info("%s", " | ".join(lines))
            return 0
        finally:
            try:
                drone.close()
            except Exception:
                pass
    except Exception as exc:
        mlog.exception("пинг упал")
        lines.append("ОШИБКА: " + str(exc))
        lines.append(traceback.format_exc().splitlines()[-1])
        try:
            ui.send_vision(
                args.drone, 0, 0, 0,
                color="",
                message=" | ".join(lines),
                confidence=0.0,
            )
            return 0
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
