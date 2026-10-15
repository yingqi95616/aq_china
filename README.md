# CNEMC Air Quality Downloader & Database Updater

Tools to download China's national air-quality data — real-time **station**
readings (into `national_data/`) and hourly/daily **city** readings (into
`national_city_data/`) — and to keep those databases updated on a schedule.

Data sources:

- Station (点位空气质量): <https://www.cnemc.cn/sssj/qgkqzlyb/> (redirects to
  `https://air.cnemc.cn:18007/`).
- City (城市空气质量): the CNEMC homepage API
  `POST https://www.cnemc.cn/getIndexData.do` (hourly + daily, linked from
  <https://www.cnemc.cn/sssj/>).

## Paths

Throughout this document `$AQPGPATH` refers to the root directory where the
project is installed (the folder containing `aq_download.py`). Set it once per
shell, pointing at your install location:

```bash
export AQPGPATH=/path/to/aqdownload
cd "$AQPGPATH"
```

All commands below are run from `$AQPGPATH` unless stated otherwise.

`$REMOTE_BASE` is the base directory **on the remote host** under which the
databases are mirrored (the wrapper's `REMOTE_BASE` variable), with the two
databases living under it:

```
$REMOTE_BASE/national_data        # station database
$REMOTE_BASE/national_city_data   # city database
```

For example, with `REMOTE_BASE=/data/obs/china`, the station data is synced to
`/data/obs/china/national_data` and the city data to
`/data/obs/china/national_city_data`.

## Contents

| File | Purpose |
| --- | --- |
| `aq_download.py` | Download the latest nationwide station readings. |
| `history_download.py` | Download the recent (last ~24 h) station history/backfill. |
| `update_database.py` | Write the downloaded readings into `national_data/` and register new stations. |
| `city_download.py` | Download hourly/daily city readings and update `national_city_data/`. |
| `run_hourly.sh` | Wrapper: download then update (station + city), with locking and syncing. |
| `national_data/` | Station database (per-province/city/station yearly CSVs). |
| `national_city_data/` | City database (per-province/city yearly CSVs). |
| `logs/hourly.log` | Output of scheduled runs. |

## Requirements

- Python 3.8+
- `requests` (needed by `aq_download.py`, `history_download.py`,
  `city_download.py`; `update_database.py` is stdlib-only)
- Optional: `pandas` + `openpyxl` for Excel export (`-f xlsx`)

```bash
python3 -m pip install requests
```

## 1. `aq_download.py`

Downloads the most recent hourly readings for all stations (about 1600 stations,
~1.3 MB). The endpoint is slow and can take several minutes.

```bash
python3 aq_download.py                    # CSV, auto-named station_aqi_<timestamp>.csv
python3 aq_download.py -o latest.csv      # explicit output path
python3 aq_download.py -f json -o aq.json # raw JSON (English API fields)
python3 aq_download.py -f xlsx            # Excel (needs pandas + openpyxl)
python3 aq_download.py --no-save          # print summary only
python3 aq_download.py --timeout 600 --retries 3
```

Options:

| Option | Default | Description |
| --- | --- | --- |
| `-o, --output` | `station_aqi_<timestamp>.<fmt>` | Output file path. |
| `-f, --format` | `csv` | `csv`, `json`, or `xlsx`. |
| `--timeout` | `600` | Per-request timeout in seconds. |
| `--retries` | `3` | Retry attempts with backoff. |
| `--no-save` | off | Do not write a file; only print the summary. |

CSV columns: 数据时间, 城市/地区, 点位名称, 点位编码, AQI, 空气质量等级,
首要污染物, PM2.5(μg/m³), PM2.5_24h(μg/m³), PM10(μg/m³), PM10_24h(μg/m³),
SO2(μg/m³), SO2_24h(μg/m³), NO2(μg/m³), NO2_24h(μg/m³), CO(mg/m³),
CO_24h(mg/m³), O3(μg/m³), O3_24h(μg/m³), O3_8h(μg/m³), O3_8h_24h(μg/m³),
纬度, 经度, 省份ID, 城市代码, 健康影响, 建议措施, 时间(ISO).

## 2. `update_database.py`

Reads a downloaded file (CSV or JSON) and updates the database. Re-running the
same file is idempotent. If the database directory does not exist, it is created
automatically (including the year CSV grid and the station registry).

```bash
python3 update_database.py                 # auto-pick newest station_aqi_*.csv/json
python3 update_database.py -i latest.csv   # explicit input
python3 update_database.py -d national_data
python3 update_database.py --dry-run -v    # preview, no writes, per-station detail
python3 update_database.py --no-info       # do not touch province_city_stations_info.txt
python3 update_database.py --info-file path/to/province_city_stations_info.txt
```

Options:

| Option | Default | Description |
| --- | --- | --- |
| `-i, --input` | newest `station_aqi_*` | Input CSV/JSON file. |
| `-d, --data-dir` | `<script dir>/national_data` | Database root. |
| `--dry-run` | off | Preview actions without writing. |
| `-v, --verbose` | off | Print every station/row updated. |
| `--no-info` | off (info update on) | Skip the station-info registry. |
| `--info-file` | `<data-dir>/province_city_stations_info.txt` | Registry path. |

### Database layout

```
national_data/<ProvinceCode>/<CityCode>/<StationCode>/<year>.csv
```

Each CSV is a full-year hourly grid with header `datetime,pm25,pm10,o3,co,so2,no2`.
Missing hours are blank rows. A new year file is created (with a complete grid)
when data for that year first arrives; existing rows are updated in place.

Mapping: `城市代码` -> `<CityCode>` and `110000`-style `<ProvinceCode>` (first two
digits + `0000`); `点位编码` -> `<StationCode>`. `o3` is taken from the 1-hour
`O3` field. Values are stored with one decimal (blank when `NA`/`—`).

Creating a database from scratch: point `-d` at any non-existent directory, e.g.

```bash
python3 update_database.py -d "$AQPGPATH/new_national" -i station_aqi_latest.csv
```

All province/city/station folders, the yearly CSV grids, and
`province_city_stations_info.txt` (with header and every station) are created.

### Station registry

New stations (by `StationCode`) are appended to
`national_data/province_city_stations_info.txt` inside their province's block.
Columns are tab-separated:
`ProvinceCode  ProvinceName  Area  CityCode  Latitude  Longitude  StationCode  UniqueCode  PositionName`.
`ProvinceName` is looked up from the file; `UniqueCode` is generated as
`CityCode` + the next free 3-digit suffix (the download does not provide one).
Existing rows are never modified.

## 3. `history_download.py`

Downloads recent history for all stations. **Note:** the CNEMC platform only
keeps roughly the last 24 hours of hourly station snapshots (the
`GetAQIHistoryByConditionHis` endpoint); it has no multi-year history service.
This program backfills that recent window and can merge it into the database.
Long-term records are built up over time by the hourly job.

```bash
python3 history_download.py                            # last 24 hours
python3 history_download.py --hours 6                  # last 6 hours
python3 history_download.py --end "2026-10-11 15:00:00" --hours 3
python3 history_download.py --update-db                # merge into national_data
python3 history_download.py --raw-dir raw_history      # also keep raw JSON per hour
```

Options:

| Option | Default | Description |
| --- | --- | --- |
| `--hours` | `24` | Number of hours to backfill. |
| `--end` | current hour | End time `YYYY-MM-DD HH:MM:SS` (floored to the hour). |
| `-o, --output` | `station_history_<stamp>_<n>h.csv` | Combined output file. |
| `--update-db` | off | Merge the result into the database. |
| `-d, --data-dir` | `./national_data` | Database root / station registry. |
| `--raw-dir` | none | Also save the raw JSON for each hour. |
| `--no-live-map` | off | Do not use the live API to fill missing city codes. |
| `--timeout` / `--retries` | `600` / `3` | Per-request timeout / attempts. |

Notes:

- Each hour returns ~1600 stations and the endpoint is slow (~1.5–3.5 min per
  request), so a full 24-hour backfill can take 30–60 minutes.
- The history endpoint does not include `CityCode`, so the program fills it from
  `national_data/province_city_stations_info.txt`, falling back to the live
  snapshot for stations not in the registry.
- Hours with no data are skipped; responses are validated against the requested
  hour so the latest snapshot is never duplicated across hours.
- The output CSV uses the same columns as `aq_download.py`, so it also works
  with `update_database.py` directly.

## 4. `city_download.py`

Downloads **city-level** air quality from the CNEMC homepage API
(`POST https://www.cnemc.cn/getIndexData.do`). It requires a browser
`User-Agent`/`Referer`/`Origin` (the endpoint is behind a WAF). Two feeds:

- `-t hourly` (default): `airList` — the current hour for ~338 cities, with AQI,
  level, primary pollutant and PM2.5 / PM10 / SO2 / NO2 / CO / O3.
- `-t daily`: `dayAirList` — the latest 城市空气日报 (daily AQI, level and
  primary pollutant per city).

```bash
python3 city_download.py                          # hourly -> CSV
python3 city_download.py -t daily                 # daily report -> CSV
python3 city_download.py -o city.csv -f json      # raw JSON
python3 city_download.py --update-db              # merge into national_city_data
python3 city_download.py --update-db --no-save
```

Options:

| Option | Default | Description |
| --- | --- | --- |
| `-t, --type` | `hourly` | `hourly` (城市小时报) or `daily` (城市空气日报). |
| `-o, --output` | `city_aqi_<type>_<timestamp>.<fmt>` | Output file. |
| `-f, --format` | `csv` | `csv`, `json`, or `xlsx`. |
| `--update-db` | off | Write hourly data into `national_city_data` (hourly only). |
| `-d, --data-dir` | `./national_city_data` | City database root. |
| `--local-place` | `北京` | City used to locate the homepage data. |
| `--timeout` / `--retries` | `120` / `3` | Per-request timeout / attempts. |
| `--no-save` | off | Do not write a file; only print the summary. |

### City database layout

```
national_city_data/<ProvinceCode>/<CityCode>/<year>.csv
```

Same schema as `national_data` (`datetime,pm25,pm10,o3,co,so2,no2`, full-year
hourly grid; there is no station level). `--update-db` writes the current hour
and is idempotent; a new year file is created with a complete grid. New cities
are registered in `national_city_data/cities_info.txt`, tab-separated:
`ProvinceCode  ProvinceName  CityCode  CityName`.

Only the latest hour (or day) is returned, so history is built up over
successive runs.

## 5. `run_hourly.sh` and crontab

`run_hourly.sh` performs both pipelines: station data (`aq_download.py` then
`update_database.py`) and city data (`city_download.py --update-db`). On success
it syncs `national_data/` and `national_city_data/` to the remote HPC. It uses
`flock` to prevent overlapping runs and appends output to `logs/hourly.log`.

Run manually:

```bash
"$AQPGPATH/run_hourly.sh"
tail -f "$AQPGPATH/logs/hourly.log"
```

### Remote sync

After each successful update the wrapper mirrors both databases to:

```
<user_id>@<host_address>:$REMOTE_BASE/national_data
<user_id>@<host_address>:$REMOTE_BASE/national_city_data
```

It uses `rsync -rltz` over SSH (passwordless login is assumed), so only new and
changed files are transferred; nothing is deleted on the remote. Each remote
directory is created if missing. Sync is skipped if the corresponding download or
update fails.

The behaviour can be overridden with environment variables (e.g. in the crontab
line or your shell):

| Variable | Default | Purpose |
| --- | --- | --- |
| `SYNC_REMOTE` | `1` | Set to `0` to disable the remote copy. |
| `REMOTE_USER` | `<user_id>` | SSH user. |
| `REMOTE_HOST` | `<host_address>` | SSH host. |
| `REMOTE_BASE` | your remote base path | Base remote directory (the DBs below live under it). |
| `REMOTE_DIR` | `$REMOTE_BASE/national_data` | Station database destination. |
| `REMOTE_CITY_DIR` | `$REMOTE_BASE/national_city_data` | City database destination. |
| `PY` | interpreter baked into `run_hourly.sh` | Python used to run the scripts; override if yours lives elsewhere (cron may not have your env on `PATH`). |

Test connectivity before relying on cron:

```bash
ssh -o BatchMode=yes <user_id>@<host_address> true && echo ok
```

A one-off manual sync (useful for the large first copy) can be run directly:

```bash
rsync -rltz --stats "$AQPGPATH/national_data/" \
  <user_id>@<host_address>:$REMOTE_BASE/national_data/
rsync -rltz --stats "$AQPGPATH/national_city_data/" \
  <user_id>@<host_address>:$REMOTE_BASE/national_city_data/
```

### Set up the hourly cron job

CNEMC publishes data shortly after each hour, so the job runs at minute 35.

```bash
crontab -e
```

Add this line (substitute the real path for `$AQPGPATH`, and adjust the minute
if needed):

```cron
35 * * * * $AQPGPATH/run_hourly.sh
```

Cron does not expand shell variables, so the stored crontab line must contain the
literal path, e.g. `echo "$AQPGPATH"` to print it first.

Or install it directly (the shell expands `$AQPGPATH` for you):

```bash
printf '# CNEMC hourly air-quality update\n35 * * * * %s/run_hourly.sh\n' "$AQPGPATH" | crontab -
```

Manage the schedule:

```bash
crontab -l     # view current jobs
crontab -e     # edit
crontab -r     # remove all jobs
```

Verify cron is running:

```bash
systemctl is-active cron     # expect: active
```

### Notes on scheduling

- Cron uses the system local time. If the server is not on Beijing time,
  pick the minute accordingly (the data time-stamp is China Standard Time).
- A full run downloads ~1.3 MB and may take a few minutes; `flock` skips a run
  if the previous one is still active.
- The wrapper always writes the same `station_aqi_latest.csv`, so no files
  accumulate. `update_database.py` auto-selects the newest `station_aqi_*` file.
- Logs are appended to `logs/hourly.log`; trim it periodically if desired,
  e.g. `truncate -s 0 logs/hourly.log`.
