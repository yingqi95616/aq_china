#!/usr/bin/env bash
set -u
cd "$(dirname "$(readlink -f "$0")")"

PY=${PY:-/home/qying/miniconda3/bin/python3}
DATA_DIR=national_data
CITY_DIR=national_city_data
LOG=logs/hourly.log

SYNC_REMOTE=${SYNC_REMOTE:-1}
REMOTE_USER=${REMOTE_USER:-qying}
REMOTE_HOST=${REMOTE_HOST:-hpc4.ust.hk}
REMOTE_BASE=${REMOTE_BASE:-/project/hkustaqmodel/data/obs_data/china}
REMOTE_DIR=${REMOTE_DIR:-$REMOTE_BASE/national_data}
REMOTE_CITY_DIR=${REMOTE_CITY_DIR:-$REMOTE_BASE/national_city_data}
SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=30"

mkdir -p logs

exec 9>logs/.hourly.lock
if ! flock -n 9; then
    echo "$(date '+%F %T') previous run still active, skipping" >>"$LOG"
    exit 0
fi

sync_dir() {
    local src="$1" dst="$2" name="$3"
    echo "----- $(date '+%F %T') sync $name to ${REMOTE_HOST} -----" >>"$LOG"
    if ! ssh $SSH_OPTS "${REMOTE_USER}@${REMOTE_HOST}" "mkdir -p '$dst'" >>"$LOG" 2>&1; then
        echo "$(date '+%F %T') ssh failed, $name sync skipped" >>"$LOG"
        return 1
    fi
    if rsync -rltz --stats -e "ssh $SSH_OPTS" "$src/" \
        "${REMOTE_USER}@${REMOTE_HOST}:${dst}/" >>"$LOG" 2>&1; then
        echo "$(date '+%F %T') $name sync complete" >>"$LOG"
    else
        echo "$(date '+%F %T') $name rsync failed" >>"$LOG"
        return 1
    fi
}

echo "===== $(date '+%F %T') start =====" >>"$LOG"

if "$PY" aq_download.py -o station_aqi_latest.csv >>"$LOG" 2>&1; then
    if "$PY" update_database.py -i station_aqi_latest.csv >>"$LOG" 2>&1; then
        if [ "$SYNC_REMOTE" = "1" ]; then
            sync_dir "$DATA_DIR" "$REMOTE_DIR" "national_data"
        fi
    else
        echo "$(date '+%F %T') station update failed, sync skipped" >>"$LOG"
    fi
else
    echo "$(date '+%F %T') station download failed, database not updated" >>"$LOG"
fi

if "$PY" city_download.py --update-db -d "$CITY_DIR" --no-save >>"$LOG" 2>&1; then
    if [ "$SYNC_REMOTE" = "1" ]; then
        sync_dir "$CITY_DIR" "$REMOTE_CITY_DIR" "national_city_data"
    fi
else
    echo "$(date '+%F %T') city download/update failed" >>"$LOG"
fi

echo "===== $(date '+%F %T') done =====" >>"$LOG"
