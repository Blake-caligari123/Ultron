#!/bin/bash
# Always-on supervisor for ULTRON paper floor + floor view. Usage: ./run.sh start|stop|status
cd "${ULTRON_HOME:-$(dirname "$0")}"
loop() { # name, cmd...
  local n=$1; shift
  while true; do ulimit -v 3000000; nice -n 10 "$@" >> logs/$n.log 2>&1; echo "$(date) $n exited $?, restarting in 10s" >> logs/$n.log; sleep 10; done
}
case "$1" in
  start)
    [ -f logs/sup_live.pid ] && kill -0 $(cat logs/sup_live.pid) 2>/dev/null || { (loop live .venv/bin/python -W ignore -m ultron.live) & echo $! > logs/sup_live.pid; }
    [ -f logs/sup_floor.pid ] && kill -0 $(cat logs/sup_floor.pid) 2>/dev/null || { (loop floor .venv/bin/uvicorn ultron.floor:app --host 0.0.0.0 --port 8795) & echo $! > logs/sup_floor.pid; }
    echo started;;
  stop) for s in live floor; do pkill -P $(cat logs/sup_$s.pid) 2>/dev/null; kill $(cat logs/sup_$s.pid) 2>/dev/null; done; pkill -f "ultron.live|ultron.floor:app"; echo stopped;;
  status) for s in live floor; do kill -0 $(cat logs/sup_$s.pid 2>/dev/null) 2>/dev/null && echo "$s supervisor up" || echo "$s DOWN"; done; pgrep -af "ultron.live|ultron.floor" ;;
esac
