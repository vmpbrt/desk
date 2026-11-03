#!/bin/bash
cd /workspace/desk
kill "$(cat logs/watchdog.pid 2>/dev/null)" 2>/dev/null
kill "$(cat logs/desk.pid 2>/dev/null)" 2>/dev/null
rm -f logs/watchdog.pid logs/desk.pid; echo stopped
