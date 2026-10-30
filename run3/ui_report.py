#!/usr/bin/env python3
"""Отправка плана, позиции и находок на панель Сверх.Рой (HTTP, без ROS)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Optional

import mission_log as mlog


COLOR_TO_UI = {
    "красный": "red",
    "жёлтый": "yellow",
    "зелёный": "green",
    "синий": "blue",
    "red": "red",
    "yellow": "yellow",
    "green": "green",
    "blue": "blue",
}


def _cells(seq) -> list:
    out = []
    for p in seq or []:
        out.append([float(p[0]), float(p[1])])
    return out


class UiReporter:
    def __init__(self, base_url: Optional[str]):
        self.base = (base_url or "").rstrip("/")
        self.ok = bool(self.base)
        if self.ok:
            mlog.info("UI репортёр: %s", self.base)
        else:
            mlog.warning("UI адрес не задан, сайт обновляться не будет")

    def post(self, path: str, payload: dict) -> None:
        if not self.ok:
            return
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.base + path,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                resp.read()
            mlog.debug("UI POST %s ok", path)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            mlog.error("UI POST %s%s не дошёл: %s", self.base, path, exc)

    def send_plan(self, plan: dict, matrix) -> None:
        self.post("/api/plan", {
            "matrix": matrix,
            "drone1": list(plan.get("drone1") or [0, 0]),
            "drone2": list(plan.get("drone2") or [4, 0]),
            "route1": _cells(plan.get("route1")),
            "route2": _cells(plan.get("route2")),
            "region1": _cells(plan.get("region1")),
            "region2": _cells(plan.get("region2")),
            "gaps": _cells(plan.get("gaps")),
            "assignment": plan.get("assignment") or {},
            "split": plan.get("split") or {},
        })

    def send_telemetry(self, drone_id: int, x: float, y: float, battery: Optional[float] = None) -> None:
        payload: dict[str, Any] = {
            "drone_id": int(drone_id),
            "x": round(float(x), 2),
            "y": round(float(y), 2),
        }
        if battery is not None:
            payload["battery"] = round(float(battery), 2)
        self.post("/api/telemetry", payload)

    def send_vision(self, drone_id: int, x: float, y: float, z: float,
                    color: str, message: str = "Найден квадрат",
                    confidence: float = 0.95, image_b64: Optional[str] = None) -> None:
        self.post("/api/vision", {
            "drone_id": int(drone_id),
            "x": int(round(float(x))),
            "y": int(round(float(y))),
            "z": round(float(z), 2),
            "color": COLOR_TO_UI.get(color, color),
            "confidence": round(float(confidence), 2),
            "message": message,
            "image_b64": image_b64,
        })
