#!/bin/bash
# Restart loop for the multi-hour Arctic Shift fetch (spec §7.2: "these are
# multi-hour jobs and they will be killed").
#
# Survives: laptop sleep (macOS suspends and resumes the process; dropped
# sockets are handled by fetch_social.py's own backoff), transient network
# failure, and any non-zero exit.
# Does NOT survive: reboot, logout, shutdown. After one of those, just run
# this script again — --resume continues from the per-page checkpoint.
#
#   nohup build/run_social_fetch.sh > /dev/null 2>&1 &
#   tail -f data/checkpoints/social_fetch.log
#   pkill -f run_social_fetch          # stop; re-run this script to continue
#
# To stop the machine sleeping for the duration (leave it plugged in):
#   caffeinate -i -w $(pgrep -f 'build/fetch_social.py')

cd "$(dirname "$0")/.." || exit 1
LOG=data/checkpoints/social_fetch.log
mkdir -p data/checkpoints

while true; do
    echo "[$(date)] starting fetch_social.py --resume" >> "$LOG"
    .venv/bin/python -u build/fetch_social.py --resume >> "$LOG" 2>&1
    rc=$?
    if [ $rc -eq 0 ]; then
        echo "[$(date)] COMPLETE (exit 0)" >> "$LOG"
        break
    fi
    echo "[$(date)] exited $rc — restarting in 60s" >> "$LOG"
    sleep 60
done
