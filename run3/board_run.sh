#!/bin/bash
# Запуск python-скрипта миссии в окружении Сверх (ROS 2 / контейнер sverk_ros2).
# Использование: board_run.sh drone_ping.py --drone 1 --ui http://...
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

docker_bin() {
  if command -v docker >/dev/null 2>&1 && docker ps >/dev/null 2>&1; then
    echo docker
  elif sudo -n docker ps >/dev/null 2>&1; then
    echo "sudo docker"
  else
    echo ""
  fi
}

source_ws() {
  set +u
  local f
  for f in \
    /opt/ros/humble/setup.bash \
    /opt/ros/jazzy/setup.bash \
    /opt/ros/iron/setup.bash \
    "$HOME/sverk_ws/install/setup.bash" \
    "$HOME/ros2_ws/install/setup.bash" \
    /home/sverk/sverk_ws/install/setup.bash \
    /home/orangepi/sverk_ws/install/setup.bash \
    /root/sverk_ws/install/setup.bash \
    /ros2_ws/install/setup.bash
  do
    if [ -f "$f" ]; then
      # shellcheck disable=SC1090
      . "$f"
    fi
  done
}

has_sverk() {
  python3 -c "import sverk_interfaces" >/dev/null 2>&1
}

source_ws

if [ "${1:-}" = "--cmd" ]; then
  shift
  if command -v ros2 >/dev/null 2>&1; then
    exec bash -lc "$*"
  fi
  D="$(docker_bin)"
  CTR=""
  if [ -n "$D" ]; then
    CTR=$($D ps --format '{{.Names}}' 2>/dev/null | grep -iE 'sverk' | head -1 || true)
  fi
  if [ -n "${CTR:-}" ]; then
    INNER=$(printf '%q ' "$@")
    $D exec "$CTR" bash -lc "
      set +u
      for f in /opt/ros/humble/setup.bash /opt/ros/jazzy/setup.bash /ros2_ws/install/setup.bash /root/sverk_ws/install/setup.bash /home/sverk/sverk_ws/install/setup.bash; do
        [ -f \"\$f\" ] && . \"\$f\"
      done
      $INNER
    "
    exit $?
  fi
  echo "ros2 нет в PATH и контейнер sverk не найден" >&2
  echo "PATH=$PATH" >&2
  exit 127
fi

if has_sverk; then
  exec python3 "$@"
fi

D="$(docker_bin)"
CTR=""
if [ -n "$D" ]; then
  CTR=$($D ps --format '{{.Names}}' 2>/dev/null | grep -iE 'sverk' | head -1 || true)
fi

if [ -n "${CTR:-}" ]; then
  echo "host python без sverk_interfaces → контейнер $CTR" >&2
  $D exec "$CTR" mkdir -p /tmp/sverk_mission
  $D cp "$DIR/." "$CTR:/tmp/sverk_mission/" 2>/dev/null || {
    for f in a8gem.py drone_ping.py plan_paths.py ui_report.py mission_log.py numpy_fix.py sverk_boot.py first_flight.py test.py matrix.json; do
      [ -f "$DIR/$f" ] && $D cp "$DIR/$f" "$CTR:/tmp/sverk_mission/"
    done
  }
  INNER=$(printf '%q ' "$@")
  INNER=${INNER//\/home\/sverk\/mission/\/tmp\/sverk_mission}
  $D exec -w /tmp/sverk_mission "$CTR" bash -lc "
    set +u
    for f in /opt/ros/humble/setup.bash /opt/ros/jazzy/setup.bash /ros2_ws/install/setup.bash /root/sverk_ws/install/setup.bash /home/sverk/sverk_ws/install/setup.bash; do
      [ -f \"\$f\" ] && . \"\$f\"
    done
    python3 -c 'import sverk_interfaces' || { echo 'в контейнере тоже нет sverk_interfaces' >&2; python3 -c 'import sys; print(sys.path)' >&2; }
    python3 $INNER
  "
  exit $?
fi

echo "sverk_interfaces не найден (ни host python, ни docker sverk)." >&2
echo "python3=$(command -v python3) user=$(id -un)" >&2
python3 -c "import sys; print('sys.path=', sys.path)" >&2
exec python3 "$@"
