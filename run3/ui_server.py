#!/usr/bin/env python3
"""Панель Сверх.Рой: отдаёт сайт и принимает план/позицию/находки с двух OrangePi."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import mission_log as mlog
import start_flight

HERE = Path(__file__).resolve().parent
_HTML_CANDIDATES = (
    HERE / "index-v2.html",
    HERE / "saytik__tutor" / "index-v2.html",
)
HTML_PATH = next((p for p in _HTML_CANDIDATES if p.is_file()), HERE / "index-v2.html")
HOST = "0.0.0.0"
PORT = 8080

_lock = threading.Lock()
STATE = {
    "live": False,
    "matrix": None,
    "drone1": [0, 0],
    "drone2": [4, 0],
    "route1": [],
    "route2": [],
    "region1": [],
    "region2": [],
    "gaps": [],
    "assignment": {},
    "split": {},
    "batteries": {},
    "events": [],
    "event_id": 0,
    "launch_status": "idle",
    "launch_message": "",
    "links": start_flight.public_links(),
}
_launch_lock = threading.Lock()


def set_launch(status: str, message: str) -> None:
    with _lock:
        STATE["launch_status"] = status
        STATE["launch_message"] = message
    mlog.info("запуск: %s — %s", status, message)


def inject_log(drone_id: int, message: str, x: int = 0, y: int = 0, z: float = 0.0,
               image_b64: str | None = None) -> None:
    with _lock:
        STATE["event_id"] += 1
        event = {
            "id": STATE["event_id"],
            "drone_id": int(drone_id),
            "x": int(x),
            "y": int(y),
            "z": float(z),
            "message": message,
        }
        if image_b64:
            event["image_b64"] = image_b64
        STATE["events"].append(event)
        STATE["events"] = STATE["events"][-80:]
    mlog.info("лог в панель дрон%s: %s", drone_id, message[:200])


def refresh_links():
    with _lock:
        STATE["links"] = start_flight.public_links()


def snapshot():
    with _lock:
        STATE["links"] = start_flight.public_links()
        return json.loads(json.dumps(STATE))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        line = fmt % args
        if "GET /api/state" in line:
            return
        mlog.info("%s %s", self.address_string(), line)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            data = HTML_PATH.read_bytes()
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/api/state":
            body = json.dumps(snapshot()).encode("utf-8")
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            mlog.error("битый JSON на %s от %s", path, self.address_string())
            self.send_error(400, "bad json")
            return

        with _lock:
            if path == "/api/plan":
                STATE["live"] = True
                for key in ("matrix", "drone1", "drone2", "route1", "route2",
                            "region1", "region2", "gaps", "assignment", "split"):
                    if key in data:
                        STATE[key] = data[key]
                mlog.info(
                    "план: дрон1=%s дрон2=%s маршрут1=%s маршрут2=%s назначение=%s",
                    data.get("drone1"), data.get("drone2"),
                    len(data.get("route1") or []), len(data.get("route2") or []),
                    data.get("assignment"),
                )
            elif path == "/api/telemetry":
                STATE["live"] = True
                drone_id = int(data.get("drone_id", 0))
                xy = [data.get("x", 0), data.get("y", 0)]
                if drone_id == 1:
                    STATE["drone1"] = xy
                elif drone_id == 2:
                    STATE["drone2"] = xy
                if data.get("battery") is not None:
                    STATE["batteries"][str(drone_id)] = data["battery"]
                mlog.info("телеметрия дрон%s -> %s батарея=%s", drone_id, xy, data.get("battery"))
            elif path == "/api/vision":
                STATE["live"] = True
                STATE["event_id"] += 1
                event = dict(data)
                event["id"] = STATE["event_id"]
                STATE["events"].append(event)
                STATE["events"] = STATE["events"][-80:]
                mlog.info(
                    "находка дрон%s %s в (%s,%s)",
                    event.get("drone_id"), event.get("color"), event.get("x"), event.get("y"),
                )
            elif path in ("/api/start", "/api/kill", "/api/test-log", "/api/connect",
                          "/api/bridge", "/api/pytest", "/api/topics", "/api/camera"):
                pass
            else:
                mlog.warning("неизвестный POST %s", path)
                self.send_error(404)
                return

        if path == "/api/start":
            self._start_flight(data)
            return
        if path == "/api/kill":
            self._kill_flight()
            return
        if path == "/api/test-log":
            self._test_log(data)
            return
        if path == "/api/connect":
            self._connect(data)
            return
        if path == "/api/bridge":
            self._bridge(data)
            return
        if path == "/api/pytest":
            self._pytest(data)
            return
        if path == "/api/topics":
            self._topics(data)
            return
        if path == "/api/camera":
            self._camera(data)
            return

        body = b'{"ok":true}'
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _start_flight(self, data: dict) -> None:
        if not data.get("matrix"):
            self._json(400, {"ok": False, "error": "в запросе нет matrix"})
            return
        if not _launch_lock.acquire(blocking=False):
            self._json(409, {"ok": False, "error": "запуск уже идёт"})
            return

        if data.get("link1"):
            start_flight.update_link(1, data["link1"])
        if data.get("link2"):
            start_flight.update_link(2, data["link2"])

        matrix_path = HERE / "matrix.json"
        cfg = {
            "matrix": data["matrix"],
            "drone1": data.get("drone1") or [0, 0],
            "drone2": data.get("drone2") or [4, 0],
        }
        matrix_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        drones = start_flight.parse_drones(data.get("drones") or data.get("fly"))
        script = data.get("script") or "first_flight.py"
        set_launch("pushing", f"сохранил matrix.json, дроны {drones}, {script}")

        def worker():
            try:
                start_flight.push_and_start(
                    matrix_path, set_launch, drones=drones, script=script,
                )
            except Exception as exc:
                mlog.exception("автозапуск не удался")
                set_launch("error", str(exc))
            finally:
                _launch_lock.release()

        threading.Thread(target=worker, name="start-flight", daemon=True).start()
        self._json(200, {"ok": True, "status": "pushing"})

    def _kill_flight(self) -> None:
        def worker():
            try:
                start_flight.kill_all(set_launch)
            except Exception as exc:
                mlog.exception("стоп не удался")
                set_launch("error", str(exc))

        threading.Thread(target=worker, name="kill-flight", daemon=True).start()
        self._json(200, {"ok": True, "status": "stopping"})

    def _connect(self, data: dict) -> None:
        drone_id = int(data.get("drone_id") or 0)
        if drone_id not in (1, 2):
            self._json(400, {"ok": False, "error": "нужен drone_id 1 или 2"})
            return
        start_flight.update_link(drone_id, data)
        inject_log(drone_id, f"Подключаю {start_flight.host_for(drone_id)} ...")

        def worker():
            result = start_flight.connect_drone(drone_id)
            refresh_links()
            if result.get("ok"):
                inject_log(drone_id, f"SSH ок: {result.get('detail')}")
            else:
                inject_log(drone_id, f"SSH нет: {result.get('error')}")

        threading.Thread(target=worker, name=f"connect-d{drone_id}", daemon=True).start()
        self._json(200, {"ok": True, "drone_id": drone_id, "links": start_flight.public_links()})

    def _bridge(self, data: dict) -> None:
        drone_id = int(data.get("drone_id") or 0)
        if drone_id not in (1, 2):
            self._json(400, {"ok": False, "error": "нужен drone_id 1 или 2"})
            return
        start_flight.update_link(drone_id, data)
        inject_log(drone_id, f"Мост 9090 {start_flight.LINKS[drone_id]['host']}:{start_flight.LINKS[drone_id].get('bridge_port', 9090)}")

        def worker():
            result = start_flight.probe_bridge(drone_id)
            refresh_links()
            if result.get("ok"):
                inject_log(drone_id, f"Мост ок: {result.get('detail')}")
            else:
                inject_log(drone_id, f"Мост нет: {result.get('error')}")

        threading.Thread(target=worker, name=f"bridge-d{drone_id}", daemon=True).start()
        self._json(200, {"ok": True, "drone_id": drone_id})

    def _pytest(self, data: dict) -> None:
        drone_id = int(data.get("drone_id") or 0)
        if drone_id not in (1, 2):
            self._json(400, {"ok": False, "error": "нужен drone_id 1 или 2"})
            return
        start_flight.update_link(drone_id, data)
        inject_log(drone_id, "py.test: запускаю 123.py на плате...")

        def worker():
            try:
                out = start_flight.run_py_test(drone_id).strip() or "(пусто)"
                if len(out) > 1500:
                    out = out[:1500] + "…"
                inject_log(drone_id, "py.test:\n" + out)
            except Exception as exc:
                mlog.exception("py.test дрон%s", drone_id)
                inject_log(drone_id, f"py.test ошибка: {exc}")

        threading.Thread(target=worker, name=f"pytest-d{drone_id}", daemon=True).start()
        self._json(200, {"ok": True, "drone_id": drone_id})

    def _topics(self, data: dict) -> None:
        drone_id = int(data.get("drone_id") or 0)
        if drone_id not in (1, 2):
            self._json(400, {"ok": False, "error": "нужен drone_id 1 или 2"})
            return
        start_flight.update_link(drone_id, data)
        inject_log(drone_id, "Топики: ros2 topic list ...")

        def worker():
            try:
                out = start_flight.run_topics(drone_id).strip() or "(пусто)"
                if len(out) > 2500:
                    out = out[:2500] + "…"
                inject_log(drone_id, "топики:\n" + out)
            except Exception as exc:
                mlog.exception("topics дрон%s", drone_id)
                inject_log(drone_id, f"топики ошибка: {exc}")

        threading.Thread(target=worker, name=f"topics-d{drone_id}", daemon=True).start()
        self._json(200, {"ok": True, "drone_id": drone_id})

    def _camera(self, data: dict) -> None:
        drone_id = int(data.get("drone_id") or 0)
        if drone_id not in (1, 2):
            self._json(400, {"ok": False, "error": "нужен drone_id 1 или 2"})
            return
        start_flight.update_link(drone_id, data)
        inject_log(drone_id, "Камера: снимок с :8080 ...")

        def worker():
            result = start_flight.grab_camera(drone_id)
            if result.get("ok"):
                inject_log(
                    drone_id,
                    f"камера {result.get('topic')}  stream {result.get('stream')}",
                    image_b64=result.get("image_b64"),
                )
            else:
                inject_log(
                    drone_id,
                    f"камера нет: {result.get('error')}  {result.get('hint')}",
                )

        threading.Thread(target=worker, name=f"cam-d{drone_id}", daemon=True).start()
        host = start_flight.LINKS[drone_id]["host"]
        self._json(200, {
            "ok": True,
            "drone_id": drone_id,
            "stream": f"http://{host}:8080/stream?topic=/camera_1/image_raw",
            "snapshot": f"http://{host}:8080/snapshot?topic=/camera_1/image_raw",
        })

    def _test_log(self, data: dict) -> None:
        drone_id = int(data.get("drone_id") or 0)
        if drone_id not in (1, 2):
            self._json(400, {"ok": False, "error": "нужен drone_id 1 или 2"})
            return
        start_flight.update_link(drone_id, data)
        host = start_flight.host_for(drone_id)
        inject_log(drone_id, f"Пинг: стучимся на {host} по SSH...")

        def worker():
            try:
                out = start_flight.ping_drone(drone_id)
                if out.strip():
                    mlog.info("ping d%s stdout:\n%s", drone_id, out.strip())
            except Exception as exc:
                mlog.exception("пинг дрон%s не удался", drone_id)
                inject_log(drone_id, f"Пинг НЕ ПРОШЁЛ ({host}): {exc}")

        threading.Thread(target=worker, name=f"ping-d{drone_id}", daemon=True).start()
        self._json(200, {"ok": True, "drone_id": drone_id})


def main():
    log_file = mlog.setup(name="ui_server", filename="ui_server.log")
    if not HTML_PATH.is_file():
        mlog.error("Нет файла сайта: %s", HTML_PATH)
        raise SystemExit(f"Нет файла сайта: {HTML_PATH}")
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    mlog.info("Панель http://127.0.0.1:%s/  лог %s", PORT, log_file)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        mlog.info("панель остановлена")
    except Exception:
        mlog.exception("панель упала")
        raise


if __name__ == "__main__":
    main()
