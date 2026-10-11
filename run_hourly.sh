#!/usr/bin/env bash
set -u
cd "$(dirname "$(readlink -f "$0")")"

PY=${PY:-/home/qying/miniconda3/bin/python3}
DATA_DIR=national_data
LOG=logs/hourly.log

SYNC_REMOTE=${SYNC_REMOTE:-1}
REMOTE_USER=${REMOTE_USER:-qying}
REMOTE_HOST=${REMOTE_HOST:-hpc4.ust.hk}
REMOTE_DIR=${REMOTE_DIR:-/project/hkustaqmodel/data/obs_data/china/national_data}
SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=30"

mkdir -p logs

exec 9>logs/.hourly.lock
if ! flock -n 9; then
    echo "$(date '+%F %T') previous run still active, skipping" >>"$LOG"
    exit 0
fi

sync_remote() {
    echo "----- $(date '+%F %T') sync to ${REMOTE_HOST} -----" >>"$LOG"
    if ! ssh $SSH_OPTS "${REMOTE_USER}@${REMOTE_HOST}" "mkdir -p '$REMOTE_DIR'" >>"$LOG" 2>&1; then
        echo "$(date '+%F %T') ssh failed, sync skipped" >>"$LOG"
        return 1
    fi
    if rsync -rltz --stats -e "ssh $SSH_OPTS" "$DATA_DIR/" \
        "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/" >>"$LOG" 2>&1; then
        echo "$(date '+%F %T') sync complete" >>"$LOG"
    else
        echo "$(date '+%F %T') rsync failed" >>"$LOG"
        return 1
    fi
}

echo "===== $(date '+%F %T') start =====" >>"$LOG"
if "$PY" aq_download.py -o station_aqi_latest.csv >>"$LOG" 2>&1; then
    if "$PY" update_database.py -i station_aqi_latest.csv >>"$LOG" 2>&1; then
        if [ "$SYNC_REMOTE" = "1" ]; then
            sync_remote
        fi
    else
        echo "$(date '+%F %T') update failed, sync skipped" >>"$LOG"
    fi
else
    echo "$(date '+%F %T') download failed, database not updated" >>"$LOG"
fi
echo "===== $(date '+%F %T') done =====" >>"$LOG"
