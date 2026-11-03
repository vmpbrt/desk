#!/bin/bash
# start the desk under the watchdog (login shell so GH_DESK_TOKEN/TG_TOKEN are present)
cd /workspace/desk
mkdir -p logs
if [ -f logs/watchdog.pid ] && kill -0 "$(cat logs/watchdog.pid)" 2>/dev/null; then echo "watchdog already running"; exit 0; fi
nohup bash scripts/watchdog.sh >> logs/watchdog.log 2>&1 &
echo $! > logs/watchdog.pid
echo "watchdog pid $(cat logs/watchdog.pid)"
