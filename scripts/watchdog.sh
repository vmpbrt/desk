#!/bin/bash
# keeps exactly one desk process alive; restarts on exit or stale heartbeat (>180s).
cd /workspace/desk
PY=/workspace/desk/.venv/bin/python
while true; do
  if ! { [ -f logs/desk.pid ] && kill -0 "$(cat logs/desk.pid)" 2>/dev/null; }; then
    echo "$(date -Is) starting desk"
    nohup $PY -m desk.main >> logs/desk.out 2>&1 &
    echo $! > logs/desk.pid
    sleep 20
  fi
  if [ -f logs/heartbeat.json ]; then
    age=$(( $(date +%s) - $(stat -c %Y logs/heartbeat.json) ))
    if [ "$age" -gt 180 ]; then
      echo "$(date -Is) heartbeat stale ${age}s, restarting"
      kill "$(cat logs/desk.pid)" 2>/dev/null; sleep 3; kill -9 "$(cat logs/desk.pid)" 2>/dev/null
      rm -f logs/desk.pid
    fi
  fi
  sleep 15
done
