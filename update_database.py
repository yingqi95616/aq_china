#!/usr/bin/env python3
"""将最近下载的空气质量数据更新到 national_data 数据库中。

数据库结构:
    national_data/<ProvinceCode>/<CityCode>/<StationCode>/<year>.csv
    csv 表头: datetime,pm25,pm10,o3,co,so2,no2

输入文件可以是 aq_download.py 生成的 CSV 或 JSON, 例如:
    station_aqi_20261011_112836.csv

用法示例:
    python update_database.py                     # 自动选取最新的下载文件
    python update_database.py -i aq.csv           # 指定输入
    python update_database.py -d national_data    # 指定数据库目录
    python update_database.py --dry-run           # 只预览, 不写文件
"""

import argparse
import csv
import glob
import json
import os
import re
import sys
from datetime import datetime, timedelta

DEFAULT_DATA_DIR = "national_data"
HEADER = ["datetime", "pm25", "pm10", "o3", "co", "so2", "no2"]

MISSING = {"", "na", "nan", "—", "-", "null", "none"}

CITY_COLUMNS = {
    "datetime": ["时间(ISO)", "TimePoint"],
    "station": ["点位编码", "StationCode"],
    "city": ["城市代码", "CityCode"],
    "area": ["城市/地区", "Area"],
    "name": ["点位名称", "PositionName"],
    "lat": ["纬度", "Latitude"],
    "lon": ["经度", "Longitude"],
    "pm25": ["PM2.5(μg/m³)", "PM2_5"],
    "pm10": ["PM10(μg/m³)", "PM10"],
    "o3": ["O3(μg/m³)", "O3"],
    "co": ["CO(mg/m³)", "CO"],
    "so2": ["SO2(μg/m³)", "SO2"],
    "no2": ["NO2(μg/m³)", "NO2"],
}

POLLUTANTS = ["pm25", "pm10", "o3", "co", "so2", "no2"]

INFO_HEADER = (
    "ProvinceCode\tProvinceName\tArea\tCityCode\tLatitude\tLongitude\t"
    "StationCode\tUniqueCode\tPositionName"
)

PROVINCE_NAMES = {
    "110000": "北京",
    "120000": "天津",
    "130000": "河北",
    "140000": "山西",
    "150000": "内蒙古",
    "210000": "辽宁",
    "220000": "吉林",
    "230000": "黑龙江",
    "310000": "上海",
    "320000": "江苏",
    "330000": "浙江",
    "340000": "安徽",
    "350000": "福建",
    "360000": "江西",
    "370000": "山东",
    "410000": "河南",
    "420000": "湖北",
    "430000": "湖南",
    "440000": "广东",
    "450000": "广西",
    "460000": "海南",
    "500000": "重庆",
    "510000": "四川",
    "520000": "贵州",
    "530000": "云南",
    "540000": "西藏",
    "610000": "陕西",
    "620000": "甘肃",
    "630000": "青海",
    "640000": "宁夏",
    "650000": "新疆",
}

CN_TIME_RE = re.compile(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})\D+(\d{1,2})")


def pick_value(record, keys):
    for key in keys:
        if key in record and record[key] not in (None, ""):
            return record[key]
    return None


def clean_num(value):
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return f"{float(value):.1f}"
    text = str(value).strip()
    if text.lower() in MISSING:
        return ""
    try:
        return f"{float(text):.1f}"
    except ValueError:
        return ""


def clean_text(value):
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in MISSING:
        return ""
    return text


def parse_datetime(record):
    raw = pick_value(record, CITY_COLUMNS["datetime"])
    if raw:
        text = str(raw).strip()
        if "T" in text:
            try:
                return datetime.fromisoformat(text).replace(
                    minute=0, second=0, microsecond=0, tzinfo=None
                )
            except ValueError:
                pass
        if text.startswith("/Date(") and text.endswith(")/"):
            try:
                ms = int(text[6:-2])
                base = datetime(1970, 1, 1) + timedelta(hours=8)
                dt = datetime.utcfromtimestamp(ms / 1000) + timedelta(hours=8)
                return dt.replace(minute=0, second=0, microsecond=0)
            except ValueError:
                pass
    cn = pick_value(record, ["数据时间", "TimePointStr"])
    if cn:
        m = CN_TIME_RE.search(str(cn))
        if m:
            y, mo, d, h = (int(x) for x in m.groups())
            return datetime(y, mo, d, h)
    return None


def normalize(record):
    dt = parse_datetime(record)
    station = pick_value(record, CITY_COLUMNS["station"])
    city = pick_value(record, CITY_COLUMNS["city"])
    if dt is None or not station or not city:
        return None
    city = str(city).strip()
    digits = re.sub(r"\D", "", city)
    if len(digits) not in (5, 6):
        return None
    city_code = digits.zfill(6)
    province_code = city_code[:2] + "0000"
    row = {
        "datetime": dt,
        "station": str(station).strip(),
        "city_code": city_code,
        "province_code": province_code,
        "area": clean_text(pick_value(record, CITY_COLUMNS["area"])),
        "name": clean_text(pick_value(record, CITY_COLUMNS["name"])),
        "lat": clean_text(pick_value(record, CITY_COLUMNS["lat"])),
        "lon": clean_text(pick_value(record, CITY_COLUMNS["lon"])),
    }
    for key in POLLUTANTS:
        row[key] = clean_num(pick_value(record, CITY_COLUMNS[key]))
    return row


def load_records(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".json":
        with open(path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
        records = data if isinstance(data, list) else data.get("data", [])
    else:
        with open(path, encoding="utf-8-sig", newline="") as fh:
            records = list(csv.DictReader(fh))
    rows = []
    skipped = 0
    for rec in records:
        row = normalize(rec)
        if row is None:
            skipped += 1
        else:
            rows.append(row)
    return rows, skipped


def year_grid(year):
    start = datetime(year, 1, 1)
    end = datetime(year + 1, 1, 1)
    out = []
    cur = start
    while cur < end:
        out.append(cur.strftime("%Y-%m-%d %H:%M:%S"))
        cur += timedelta(hours=1)
    return out


def format_row(dt_key, values):
    return ",".join([dt_key] + [values.get(p, "") for p in POLLUTANTS]) + "\n"


def load_year_file(path):
    with open(path, encoding="utf-8", newline="") as fh:
        lines = fh.readlines()
    header = lines[0].rstrip("\r\n") if lines else ",".join(HEADER)
    data = {}
    order = []
    for line in lines[1:]:
        line = line.rstrip("\r\n")
        if not line:
            continue
        key = line.split(",", 1)[0]
        data[key] = line
        order.append(key)
    return header, data, order


def build_new_file(path, updates, year):
    data = {dt: "" for dt in year_grid(year)}
    for dt, values in updates:
        data[dt] = format_row(dt, values)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(",".join(HEADER) + "\n")
        for key in sorted(data):
            line = data[key]
            fh.write(line if line else format_row(key, {}))


def update_existing_file(path, updates):
    header, data, order = load_year_file(path)
    added = []
    for dt, values in updates:
        line = format_row(dt, values)
        if dt in data:
            data[dt] = line.rstrip("\n")
        else:
            data[dt] = line.rstrip("\n")
            added.append(dt)
    def sort_key(k):
        try:
            return (0, datetime.strptime(k[:19], "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            return (1, k)

    keys = sorted(data.keys(), key=sort_key)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(header + "\n")
        for key in keys:
            fh.write(data[key] + "\n")


def gen_unique_code(city_code, existing_codes):
    prefix = city_code
    max_suffix = 0
    for code in existing_codes:
        if code.startswith(prefix) and code[len(prefix):].isdigit():
            max_suffix = max(max_suffix, int(code[len(prefix):]))
    seq = max_suffix + 1
    while f"{prefix}{seq:03d}" in existing_codes:
        seq += 1
    return f"{prefix}{seq:03d}"


def update_info_file(info_path, stations, dry_run=False, verbose=False):
    if os.path.exists(info_path):
        with open(info_path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    else:
        lines = []
    if not lines:
        lines = [INFO_HEADER]

    header = lines[0]
    cols = header.split("\t")
    pos = {name: i for i, name in enumerate(cols)}
    data_lines = lines[1:]

    existing = set()
    unique_codes = set()
    province_names = {}
    province_last = {}
    for i, line in enumerate(data_lines):
        parts = line.split("\t")
        station = parts[pos["StationCode"]] if len(parts) > pos["StationCode"] else ""
        existing.add(station)
        if len(parts) > pos["UniqueCode"]:
            unique_codes.add(parts[pos["UniqueCode"]])
        if len(parts) > pos["ProvinceCode"]:
            province_names.setdefault(parts[pos["ProvinceCode"]], parts[pos["ProvinceName"]])
            province_last[parts[pos["ProvinceCode"]]] = i

    pending = {}
    for station, meta in stations.items():
        if station in existing:
            continue
        prov = meta["province_code"]
        unique = gen_unique_code(meta["city_code"], unique_codes)
        unique_codes.add(unique)
        row = [
            prov,
            province_names.get(prov) or PROVINCE_NAMES.get(prov, ""),
            meta["area"],
            meta["city_code"],
            meta["lat"],
            meta["lon"],
            station,
            unique,
            meta["name"],
        ]
        pending.setdefault(prov, []).append(row)
        existing.add(station)

    for prov in pending:
        pending[prov].sort(key=lambda r: (r[pos["CityCode"]], r[pos["StationCode"]]))

    added = sum(len(v) for v in pending.values())
    if added == 0:
        return 0
    if verbose or dry_run:
        for prov in sorted(pending):
            for row in pending[prov]:
                print("  [新增点位] " + "\t".join(row))

    if dry_run:
        return added

    result = [header]
    for i, line in enumerate(data_lines):
        result.append(line)
        pc = line.split("\t")[pos["ProvinceCode"]]
        if province_last.get(pc) == i and pc in pending:
            result.extend("\t".join(row) for row in pending.pop(pc))
    for pc in sorted(pending):
        result.extend("\t".join(row) for row in pending[pc])

    os.makedirs(os.path.dirname(os.path.abspath(info_path)), exist_ok=True)
    with open(info_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(result) + "\n")
    return added


def find_latest_input(directory):
    patterns = ["station_aqi_*.csv", "station_aqi_*.json"]
    found = []
    for pat in patterns:
        found.extend(glob.glob(os.path.join(directory, pat)))
    if not found:
        return None
    return max(found, key=os.path.getmtime)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="用最新下载的数据更新 national_data 数据库",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("-i", "--input", help="下载的数据文件 (csv/json)")
    parser.add_argument("-d", "--data-dir", help="数据库根目录")
    parser.add_argument("--dry-run", action="store_true", help="只预览, 不写入文件")
    parser.add_argument("-v", "--verbose", action="store_true", help="打印每个点位的更新")
    parser.add_argument(
        "--no-info", dest="update_info", action="store_false", help="不更新省份城市点位信息文件"
    )
    parser.add_argument("--info-file", help="省份城市点位信息文件路径")
    args = parser.parse_args(argv)

    base = os.path.dirname(os.path.abspath(__file__))
    if not args.data_dir:
        args.data_dir = os.path.join(base, DEFAULT_DATA_DIR)
    input_path = args.input or find_latest_input(base)
    if not input_path:
        print("未找到下载文件, 请用 -i 指定", file=sys.stderr)
        return 1
    if not os.path.exists(input_path):
        print(f"输入文件不存在: {input_path}", file=sys.stderr)
        return 1
    if not os.path.isdir(args.data_dir):
        print(f"数据库目录不存在, 将创建: {args.data_dir}")
        if not args.dry_run:
            os.makedirs(args.data_dir, exist_ok=True)

    rows, skipped = load_records(input_path)
    print(f"输入文件 : {input_path}")
    print(f"有效记录 : {len(rows)}  (跳过 {skipped})")
    if not rows:
        return 1

    groups = {}
    station_meta = {}
    for row in rows:
        key = (
            row["province_code"],
            row["city_code"],
            row["station"],
            row["datetime"].year,
        )
        groups.setdefault(key, []).append((row["datetime"], row))
        station_meta.setdefault(
            row["station"],
            {
                "province_code": row["province_code"],
                "city_code": row["city_code"],
                "area": row["area"],
                "name": row["name"],
                "lat": row["lat"],
                "lon": row["lon"],
            },
        )

    files_updated = 0
    files_created = 0
    stations_new = 0
    hours = 0
    stations_seen = set()

    for (prov, city, station, year), items in sorted(groups.items()):
        rel = os.path.join(args.data_dir, prov, city, station)
        path = os.path.join(rel, f"{year}.csv")
        updates = []
        seen = {}
        for dt, row in items:
            key = dt.strftime("%Y-%m-%d %H:%M:%S")
            seen[key] = {p: row[p] for p in POLLUTANTS}
        for key in sorted(seen):
            updates.append((key, seen[key]))

        station_id = (prov, city, station)
        if station_id not in stations_seen:
            stations_seen.add(station_id)
            if not os.path.isdir(rel):
                stations_new += 1

        action = "更新" if os.path.exists(path) else "新建"
        if args.verbose or args.dry_run:
            print(f"  [{action}] {prov}/{city}/{station}/{year}.csv  ({len(updates)} 条)")

        if args.dry_run:
            files_updated += 1
            hours += len(updates)
            continue

        if os.path.exists(path):
            update_existing_file(path, updates)
            files_updated += 1
        else:
            build_new_file(path, updates, year)
            files_created += 1
        hours += len(updates)

    print()
    if args.dry_run:
        print("[dry-run] 计划更新文件 :", files_updated)
    else:
        print("已更新文件 :", files_updated)
        print("新建文件   :", files_created)
    print("新增点位目录:", stations_new)
    print("写入数据行 :", hours)

    if args.update_info:
        info_path = args.info_file or os.path.join(
            args.data_dir, "province_city_stations_info.txt"
        )
        existed = os.path.exists(info_path)
        added = update_info_file(
            info_path, station_meta, dry_run=args.dry_run, verbose=args.verbose
        )
        if not args.dry_run and not existed:
            print(f"已创建点位信息文件: {info_path}")
        prefix = "[dry-run] 计划新增点位信息行" if args.dry_run else "已新增点位信息行"
        print(f"{prefix}: {added}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
