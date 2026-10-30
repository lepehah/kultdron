#!/usr/bin/env bash
# Кладёт matrix.json и скрипты полёта на обе OrangePi.
# Полёт сам не стартует — это отдельная команда ssh.
set -euo pipefail

DRONE1="${DRONE1:-sverk@192.168.1.19}"
DRONE2="${DRONE2:-sverk@192.168.1.27}"
REMOTE_DIR="${REMOTE_DIR:-/home/sverk/mission}"
HERE="$(cd "$(dirname "$0")" && pwd)"
UI_HOST="${UI_HOST:-$(python3 - <<'PY'
import socket
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    s.connect(("192.168.1.19", 80))
    print(s.getsockname()[0])
finally:
    s.close()
PY
)}"
UI_URL="${UI_URL:-http://${UI_HOST}:8080}"

MATRIX="${1:-}"
if [[ -z "$MATRIX" ]]; then
  if [[ -f "$HOME/Загрузки/matrix.json" ]]; then
    MATRIX="$HOME/Загрузки/matrix.json"
  elif [[ -f "$HERE/matrix.json" ]]; then
    MATRIX="$HERE/matrix.json"
  else
    echo "Укажи путь: $0 /путь/к/matrix.json"
    echo "Сначала на сайте нажми «Скачать matrix.json»."
    exit 1
  fi
fi

if [[ ! -f "$MATRIX" ]]; then
  echo "Нет файла: $MATRIX"
  exit 1
fi

echo "Матрица: $MATRIX"
python3 "$HERE/plan_paths.py" "$MATRIX"
echo

push_one() {
  local host="$1"
  echo "→ $host:$REMOTE_DIR"
  ssh -p 22 "$host" "mkdir -p '$REMOTE_DIR'"
  scp -P 22 "$MATRIX" "$host:$REMOTE_DIR/matrix.json"
  scp -P 22 "$HERE/plan_paths.py" "$HERE/a8gem.py" "$HERE/ui_report.py" "$HERE/mission_log.py" "$HERE/drone_ping.py" "$HERE/board_run.sh" "$HERE/numpy_fix.py" "$HERE/sverk_boot.py" "$host:$REMOTE_DIR/"
  ssh -p 22 "$host" "chmod +x '$REMOTE_DIR/board_run.sh'"
}

push_one "$DRONE1"
push_one "$DRONE2"

echo
echo "Панель на ноутбуке (оставь открытой):"
echo "  python3 $HERE/ui_server.py"
echo "  браузер: $UI_URL"
echo
echo "Потом два терминала:"
echo "  ssh $DRONE1  'python3 $REMOTE_DIR/a8gem.py --drone 1 $REMOTE_DIR/matrix.json --ui $UI_URL'"
echo "  ssh $DRONE2  'python3 $REMOTE_DIR/a8gem.py --drone 2 $REMOTE_DIR/matrix.json --ui $UI_URL'"
echo
echo "Логи:"
echo "  ноут:  $HERE/logs/ui_server.log"
echo "  дрон1: $REMOTE_DIR/logs/mission_d1.log"
echo "  дрон2: $REMOTE_DIR/logs/mission_d2.log"
