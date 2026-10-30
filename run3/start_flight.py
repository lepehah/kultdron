#!/usr/bin/env python3
"""SSH на платы: настройки с сайта, пароль или ключ, пинг и запуск миссии."""
from __future__ import annotations

import base64
import os
import shlex
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import mission_log as mlog

HERE = Path(__file__).resolve().parent
PAYLOAD = [
    "plan_paths.py", "a8gem.py", "ui_report.py", "mission_log.py",
    "drone_ping.py", "board_run.sh", "numpy_fix.py", "sverk_boot.py", "test.py",
    "first_flight.py",
]

LINKS: Dict[int, dict] = {
    1: {
        "user": os.environ.get("DRONE1_USER", "sverk"),
        "host": os.environ.get("DRONE1_HOST", "192.168.1.19"),
        "port": int(os.environ.get("DRONE1_PORT", "22")),
        "password": os.environ.get("DRONE1_PASSWORD", ""),
        "remote_dir": os.environ.get("REMOTE_DIR", "/home/sverk/mission"),
        "bridge_port": int(os.environ.get("DRONE1_BRIDGE", "9090")),
        "status": "offline",
        "detail": "ещё не подключали",
    },
    2: {
        "user": os.environ.get("DRONE2_USER", "sverk"),
        "host": os.environ.get("DRONE2_HOST", "192.168.1.27"),
        "port": int(os.environ.get("DRONE2_PORT", "22")),
        "password": os.environ.get("DRONE2_PASSWORD", ""),
        "remote_dir": os.environ.get("REMOTE_DIR", "/home/sverk/mission"),
        "bridge_port": int(os.environ.get("DRONE2_BRIDGE", "9090")),
        "status": "offline",
        "detail": "ещё не подключали",
    },
}

_KNOWN = HERE / "logs" / "known_hosts"
SSH_OPTS = [
    "-o", "ConnectTimeout=6",
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", f"UserKnownHostsFile={_KNOWN}",
]


def laptop_ip() -> str:
    env = os.environ.get("UI_HOST")
    if env:
        return env
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        host = LINKS[1]["host"]
        sock.connect((host, 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def ui_url() -> str:
    return os.environ.get("UI_URL") or f"http://{laptop_ip()}:8080"


def public_links() -> dict:
    out = {}
    for key, cfg in LINKS.items():
        item = dict(cfg)
        item["has_password"] = bool(cfg.get("password"))
        item["password"] = ""
        out[str(key)] = item
    return out


def update_link(drone_id: int, data: dict) -> dict:
    cfg = LINKS[int(drone_id)]
    if data.get("user"):
        cfg["user"] = str(data["user"]).strip()
    if data.get("host"):
        cfg["host"] = str(data["host"]).strip()
    if data.get("port"):
        cfg["port"] = int(data["port"])
    if "password" in data and data["password"] is not None and data["password"] != "":
        cfg["password"] = str(data["password"])
    if data.get("remote_dir"):
        cfg["remote_dir"] = str(data["remote_dir"]).strip()
    if data.get("bridge_port"):
        cfg["bridge_port"] = int(data["bridge_port"])
    return cfg


def host_for(drone_id: int) -> str:
    cfg = LINKS[int(drone_id)]
    return f"{cfg['user']}@{cfg['host']}:{cfg['port']}"


def _redact(cmd: Sequence[str]) -> str:
    parts = list(cmd)
    for i, p in enumerate(parts):
        if p == "-p" and i > 0 and parts[i - 1].endswith("sshpass"):
            if i + 1 < len(parts):
                parts[i + 1] = "***"
    return " ".join(parts)


def _askpass_script(password: str) -> Path:
    path = HERE / "logs" / f".askpass_{os.getpid()}.sh"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nprintf '%s\\n' " + shlex.quote(password) + "\n", encoding="utf-8")
    path.chmod(0o700)
    return path


def _ssh_prefix(cfg: dict) -> Tuple[List[str], List[str], dict]:
    prefix: List[str] = []
    opts = list(SSH_OPTS)
    extra_env: dict = {}
    if cfg.get("password"):
        if shutil.which("sshpass"):
            prefix = ["sshpass", "-p", cfg["password"]]
            opts += ["-o", "PreferredAuthentications=password,keyboard-interactive,publickey"]
        else:
            ask = _askpass_script(cfg["password"])
            extra_env = {
                "SSH_ASKPASS": str(ask),
                "SSH_ASKPASS_REQUIRE": "force",
                "DISPLAY": os.environ.get("DISPLAY", ":0"),
            }
            opts += ["-o", "PreferredAuthentications=password,keyboard-interactive,publickey",
                     "-o", "NumberOfPasswordPrompts=1"]
    else:
        opts += ["-o", "BatchMode=yes", "-o", "PreferredAuthentications=publickey"]
    return prefix, opts, extra_env


def _run(cmd: Sequence[str], timeout: int = 40, check: bool = True, env: Optional[dict] = None) -> str:
    mlog.info("exec: %s", _redact(cmd))
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    proc = subprocess.run(
        list(cmd),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=run_env,
        start_new_session=bool(env),
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        mlog.error("команда упала (%s):\n%s", proc.returncode, out.strip())
        if check:
            hint = ""
            if "Permission denied" in out or "publickey" in out:
                hint = " Ключ не подошёл — укажи пароль на сайте или ssh-copy-id."
            if "No route to host" in out or "Network is unreachable" in out:
                hint = " Плата не в этой сети. Проверь IP и что дрон включён."
            raise RuntimeError((out.strip() or f"exit {proc.returncode}") + hint)
    elif out.strip():
        mlog.info(out.strip())
    return out


def _ssh(drone_id: int, remote: str, timeout: int = 40, retries: int = 1,
         check: bool = True) -> str:
    cfg = LINKS[int(drone_id)]
    prefix, opts, extra_env = _ssh_prefix(cfg)
    target = f"{cfg['user']}@{cfg['host']}"
    cmd = [*prefix, "ssh", "-p", str(cfg["port"]), *opts, target, remote]
    last: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            return _run(cmd, timeout=timeout, check=check, env=extra_env or None)
        except RuntimeError as exc:
            last = exc
            text = str(exc)
            if "Connection reset" in text or "Connection refused" in text:
                time.sleep(1.2)
                continue
            raise
    assert last is not None
    raise last


def _scp(drone_id: int, local_files: List[str], remote_path: str) -> None:
    cfg = LINKS[int(drone_id)]
    prefix, opts, extra_env = _ssh_prefix(cfg)
    target = f"{cfg['user']}@{cfg['host']}:{remote_path}"
    cmd = [*prefix, "scp", "-P", str(cfg["port"]), *opts, *local_files, target]
    _run(cmd, timeout=90, env=extra_env or None)


def probe_tcp(host: str, port: int, timeout: float = 2.0) -> str:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return "tcp ok"
    except OSError as exc:
        raise RuntimeError(f"нет сети до {host}:{port} ({exc})") from exc


def connect_drone(drone_id: int) -> dict:
    cfg = LINKS[int(drone_id)]
    cfg["status"] = "checking"
    cfg["detail"] = "проверяю сеть..."
    try:
        probe_tcp(cfg["host"], cfg["port"])
        cfg["detail"] = "порт открыт, SSH..."
        who = _ssh(drone_id, "whoami; hostname; pwd", timeout=12, retries=1).strip()
        cfg["status"] = "ssh_ok"
        cfg["detail"] = who.replace("\n", " | ")
        mlog.info("дрон%s SSH ok: %s", drone_id, cfg["detail"])
        return {"ok": True, **public_links()[str(drone_id)]}
    except Exception as exc:
        cfg["status"] = "error"
        cfg["detail"] = str(exc)
        mlog.error("дрон%s connect: %s", drone_id, exc)
        return {"ok": False, "error": str(exc), **public_links()[str(drone_id)]}


def probe_bridge(drone_id: int) -> dict:
    """Мост Сверх на плате: HTTP :9090, сервис get_telemetry как в Python."""
    cfg = LINKS[int(drone_id)]
    host = cfg["host"]
    port = int(cfg.get("bridge_port") or 9090)
    url = f"http://{host}:{port}/"
    cfg["status"] = "checking"
    cfg["detail"] = f"мост {url}"
    try:
        probe_tcp(host, port, timeout=2.0)
    except Exception as exc:
        cfg["status"] = "error"
        cfg["detail"] = str(exc)
        return {"ok": False, "error": str(exc)}
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            snippet = resp.read(240).decode("utf-8", "replace")
            cfg["status"] = "bridge_ok"
            cfg["detail"] = f"HTTP {resp.status} {url} {snippet[:80]!r}"
            return {"ok": True, "detail": cfg["detail"]}
    except urllib.error.HTTPError as exc:
        cfg["status"] = "bridge_ok"
        cfg["detail"] = f"порт {port} отвечает HTTP {exc.code} (мост жив)"
        return {"ok": True, "detail": cfg["detail"]}
    except Exception as exc:
        try:
            probe_tcp(host, port)
            cfg["status"] = "bridge_ok"
            cfg["detail"] = f"TCP {host}:{port} открыт (часто это rosbridge/ws, не REST)"
            return {"ok": True, "detail": cfg["detail"]}
        except Exception:
            cfg["status"] = "error"
            cfg["detail"] = f"мост {url}: {exc}"
            return {"ok": False, "error": cfg["detail"]}


def run_py_test(drone_id: int) -> str:
    """Запускает ~/123.py (и соседние корни) на плате, возвращает stdout/stderr."""
    remote = r"""
set +e
found=""
for f in "$HOME/123.py" ./123.py /home/sverk/123.py /home/orangepi/123.py /root/123.py /123.py; do
  if [ -f "$f" ]; then
    found="$f"
    break
  fi
done
if [ -z "$found" ]; then
  echo "123.py не найден. Искал: \$HOME/123.py ./123.py /home/sverk /home/orangepi /root /"
  echo "pwd=$(pwd) HOME=$HOME user=$(id -un)"
  ls -la "$HOME" 2>/dev/null | head
  exit 2
fi
echo "файл: $found"
python3 "$found"
echo "exit:$?"
"""
    return _ssh(drone_id, remote, timeout=25, retries=0, check=False)


CAMERA_TOPICS = (
    "/camera_1/image_raw",
    "/camera/image_raw",
    "/image_raw",
)


def grab_camera(drone_id: int) -> dict:
    """Снимок с Web video server (:8080) — карточка «Web video server» на экране Сверх."""
    host = LINKS[int(drone_id)]["host"]
    last = "нет ответа"
    for topic in CAMERA_TOPICS:
        url = f"http://{host}:8080/snapshot?topic={topic}"
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = resp.read()
            if not data:
                last = f"пустой ответ {url}"
                continue
            return {
                "ok": True,
                "topic": topic,
                "snapshot": url,
                "stream": f"http://{host}:8080/stream?topic={topic}",
                "image_b64": base64.b64encode(data).decode("ascii"),
            }
        except Exception as exc:
            last = f"{url}: {exc}"
    return {
        "ok": False,
        "error": last,
        "hint": "На экране Сверх должна быть зелёная карточка Web video server :8080",
        "stream": f"http://{host}:8080/stream?topic=/camera_1/image_raw",
    }


def run_topics(drone_id: int) -> str:
    """ros2 topic list в окружении Сверх (source / docker), не голый python3."""
    cfg = LINKS[int(drone_id)]
    remote_dir = cfg["remote_dir"]
    local = HERE / "test.py"
    _ssh(drone_id, f"mkdir -p '{remote_dir}'", timeout=15)
    files = [str(HERE / "board_run.sh"), str(HERE / "test.py")]
    files = [p for p in files if Path(p).is_file()]
    _scp(drone_id, files, f"{remote_dir}/")
    remote = (
        f"chmod +x '{remote_dir}/board_run.sh'; "
        f"'{remote_dir}/board_run.sh' --cmd 'ros2 topic list' "
        f"|| '{remote_dir}/board_run.sh' test.py"
    )
    return _ssh(drone_id, remote, timeout=40, retries=0, check=False)


def ping_drone(drone_id: int) -> str:
    cfg = LINKS[int(drone_id)]
    url = ui_url()
    remote_dir = cfg["remote_dir"]
    _ssh(drone_id, f"mkdir -p '{remote_dir}/logs'", timeout=15)
    ping_files = [
        str(HERE / name)
        for name in (
            "drone_ping.py", "ui_report.py", "mission_log.py",
            "board_run.sh", "numpy_fix.py", "sverk_boot.py",
        )
        if (HERE / name).is_file()
    ]
    _scp(drone_id, ping_files, f"{remote_dir}/")
    remote = (
        f"chmod +x '{remote_dir}/board_run.sh'; "
        f"'{remote_dir}/board_run.sh' drone_ping.py --drone {int(drone_id)} --ui '{url}'"
    )
    return _ssh(drone_id, remote, timeout=60)


def parse_drones(raw) -> List[int]:
    if not raw:
        return [1, 2]
    out = []
    for item in raw:
        n = int(item)
        if n in (1, 2) and n not in out:
            out.append(n)
    return out or [1, 2]


def push_and_start(matrix_path: Path, on_status: Callable[[str, str], None],
                   drones: Optional[Sequence[int]] = None,
                   script: str = "first_flight.py") -> None:
    drones = parse_drones(drones)
    missing = [name for name in PAYLOAD if not (HERE / name).is_file()]
    if missing:
        raise RuntimeError("в run3 нет файлов: " + ", ".join(missing))
    if script not in PAYLOAD and not (HERE / script).is_file():
        raise RuntimeError(f"нет скрипта {script}")
    if not matrix_path.is_file():
        raise RuntimeError(f"нет {matrix_path}")

    url = ui_url()
    on_status("pushing", f"копирую на дроны {drones}")
    for drone_id in drones:
        remote_dir = LINKS[drone_id]["remote_dir"]
        on_status("pushing", f"копирую на {host_for(drone_id)}")
        _ssh(drone_id, f"mkdir -p '{remote_dir}/logs'", timeout=15)
        _scp(drone_id, [str(matrix_path)], f"{remote_dir}/matrix.json")
        _scp(drone_id, [str(HERE / name) for name in PAYLOAD if (HERE / name).is_file()], f"{remote_dir}/")

    on_status("starting", f"стартую {script} на {drones}")
    _KNOWN.parent.mkdir(parents=True, exist_ok=True)
    for drone_id in drones:
        remote_dir = LINKS[drone_id]["remote_dir"]
        extra = f"'{remote_dir}/matrix.json' --ui '{url}'" if script == "a8gem.py" else ""
        logf = f"{remote_dir}/logs/console_d{drone_id}.txt"
        # pkill -f по подстроке убивал саму SSH-сессию (в cmdline есть имя скрипта).
        remote = (
            f"chmod +x '{remote_dir}/board_run.sh'; "
            f"mkdir -p '{remote_dir}/logs'; "
            "for pid in $(pgrep -f '[p]ython3' || true); do "
            f"  tr '\\0' ' ' < /proc/$pid/cmdline 2>/dev/null | grep -q '{script} --drone {drone_id}' "
            f"  && kill $pid 2>/dev/null || true; "
            "done; "
            f"nohup '{remote_dir}/board_run.sh' {script} --drone {drone_id} {extra} "
            f"> '{logf}' 2>&1 < /dev/null & echo STARTED:$!"
        )
        out = _ssh(drone_id, remote, timeout=20, check=False)
        mlog.info("запущен %s --drone %s на %s (%s)", script, drone_id, host_for(drone_id), out.strip())
        time.sleep(2)
        try:
            tail = _ssh(drone_id, f"tail -n 40 '{logf}' || true", timeout=12, check=False)
            mlog.info("консоль дрон%s:\n%s", drone_id, tail)
            on_status("running", f"дрон {drone_id}: {(tail or out or 'стартовал')[-400:]}")
        except Exception as exc:
            mlog.warning("не прочитали консоль дрон%s: %s", drone_id, exc)
    on_status("running", f"летят: {drones} скрипт {script}")


def kill_all(on_status: Callable[[str, str], None]) -> None:
    on_status("stopping", "стоп на оба борта")
    for drone_id in (1, 2):
        try:
            _ssh(
                drone_id,
                "pkill -f a8gem.py >/dev/null 2>&1 || true; "
                "pkill -f first_flight.py >/dev/null 2>&1 || true; "
                "pkill -f drone_ping.py >/dev/null 2>&1 || true; true",
                timeout=12,
                check=False,
                retries=0,
            )
        except Exception as exc:
            mlog.warning("стоп дрон%s: %s", drone_id, exc)
    on_status("idle", "стоп отправлен")
