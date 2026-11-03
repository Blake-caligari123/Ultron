#!/bin/bash
# Always-on supervisor for ULTRON PAPER floors: main floor + experimental Scalp Pit (ULTRON_FLOOR=scalp), each with its own
# live paper process (ultron.live) and continuous breeder (ultron.breeder); one floor view on :8795 serves both.
# Usage: ./run.sh start|stop|restart|status
cd "${ULTRON_HOME:-$(dirname "$0")}"
export NUMBA_NUM_THREADS=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
loop() { # name, vmem_kb, cmd...
  local n=$1 vm=$2; shift 2
  local back=10 t0 rc
  while true; do
    t0=$(date +%s)
    ( ulimit -v $vm; exec nice -n 10 "$@" ) >> logs/$n.log 2>&1; rc=$?          # capture the child's real exit code (review R1)
    [ $(( $(date +%s) - t0 )) -ge 60 ] && back=10                                  # ran a while: reset the backoff
    echo "$(date) $n exited rc=$rc, restarting in ${back}s" >> logs/$n.log; sleep $back
    back=$(( back * 2 )); [ $back -gt 300 ] && back=300                             # crash loop (<60s up): 10s, 20s, 40s ... 300s
  done
}
SVCS="live breed live_scalp breed_scalp floor"
start() {
  up() { [ -f logs/sup_$1.pid ] && kill -0 $(cat logs/sup_$1.pid) 2>/dev/null; }
  up live  || { (loop live 3000000 .venv/bin/python -W ignore -m ultron.live) & echo $! > logs/sup_live.pid; }
  up breed || { (loop breed 4500000 .venv/bin/python -W ignore -m ultron.breeder) & echo $! > logs/sup_breed.pid; }
  up live_scalp  || { (export ULTRON_FLOOR=scalp; loop live_scalp 3000000 .venv/bin/python -W ignore -m ultron.live) & echo $! > logs/sup_live_scalp.pid; }
  up breed_scalp || { (export ULTRON_FLOOR=scalp; loop breed_scalp 4500000 .venv/bin/python -W ignore -m ultron.breeder) & echo $! > logs/sup_breed_scalp.pid; }
  up floor || { (loop floor 3000000 bash -c 'while ss -ltn | grep -q ":8795 "; do echo "$(date) floor port 8795 busy, waiting"; sleep 10; done; exec .venv/bin/uvicorn "$@"' _ ultron.floor:app --host 0.0.0.0 --port 8795) & echo $! > logs/sup_floor.pid; }
  rm -f logs/live.pid   # stale single-floor pid file; live now writes logs/live_main.pid / logs/live_scalp.pid
  echo started
}
stop() {
  for s in $SVCS; do [ -f logs/sup_$s.pid ] && kill $(cat logs/sup_$s.pid) 2>/dev/null; done
  sleep 0.5; pkill -f "ultron.live|ultron.breeder|ultron.floor:app"
  for i in $(seq 15); do pgrep -f "ultron.live|ultron.breeder|ultron.floor:app" >/dev/null || break; sleep 1; done
  pkill -9 -f "ultron.live|ultron.breeder|ultron.floor:app" 2>/dev/null
  rm -f logs/live.pid logs/sup_*.pid; echo stopped
}
case "$1" in
  start) start;;
  stop) stop;;
  restart) stop; sleep 2; start;;
  status) for s in $SVCS; do kill -0 $(cat logs/sup_$s.pid 2>/dev/null) 2>/dev/null && echo "$s supervisor up" || echo "$s DOWN"; done; pgrep -af "ultron.live|ultron.breeder|ultron.floor" ;;
esac
