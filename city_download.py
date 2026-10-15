#!/usr/bin/env python3
"""下载中国环境监测总站「城市空气质量」小时报/日报数据。

数据来源: https://www.cnemc.cn/ 首页通过 POST /getIndexData.do 获取
(返回 airList 城市小时报、dayAirList 城市空气日报)。

说明: 每次仅返回最新一期 (一个小时间/一个日期), 需依靠定时运行累积历史。

用法示例:
    python city_download.py                       # 城市小时报 -> CSV
    python city_download.py -t daily              # 城市空气日报 -> CSV
    python city_download.py -o city.csv -f json   # 指定输出/格式
    python city_download.py --no-save             # 只打印摘要
"""

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

INDEX_URL = "https://www.cnemc.cn/getIndexData.do"
HOME = "https://www.cnemc.cn/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": HOME,
    "Origin": HOME.rstrip("/"),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
}

HOURLY_COLUMNS = [
    ("DATATIME", "数据时间"),
    ("CITYNAME", "城市"),
    ("cityCode", "城市代码"),
    ("AQI", "AQI"),
    ("LEVELINDEX", "空气质量等级"),
    ("MAXPOLL", "首要污染物"),
    ("PM25", "PM2.5(μg/m³)"),
    ("PM10", "PM10(μg/m³)"),
    ("SO2", "SO2(μg/m³)"),
    ("NO2", "NO2(μg/m³)"),
    ("CO", "CO(mg/m³)"),
    ("o3", "O3(μg/m³)"),
]

DAILY_COLUMNS = [
    ("DATATIME", "数据时间"),
    ("CITYNAME", "城市"),
    ("cityCode", "城市代码"),
    ("AQI", "AQI"),
    ("AQILEVEL", "空气质量等级"),
    ("MAX_POLLUTION", "首要污染物"),
]

TAG_RE = re.compile(r"<[^>]+>")

CITIES_HEADER = "ProvinceCode\tProvinceName\tCityCode\tCityName"

POLLUTANT_FIELDS = {
    "pm25": "PM25",
    "pm10": "PM10",
    "o3": "o3",
    "co": "CO",
    "so2": "SO2",
    "no2": "NO2",
}


def strip_html(value):
    if value is None:
        return ""
    return TAG_RE.sub("", str(value)).strip()


def parse_datatime(record):
    raw = str(record.get("DATATIME") or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def update_cities_info(data_dir, records):
    import update_database as ud

    path = os.path.join(data_dir, "cities_info.txt")
    rows = {}
    header = None
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        if lines:
            header = lines[0]
            cols = header.split("\t")
            ci = cols.index("CityCode") if "CityCode" in cols else 2
            for line in lines[1:]:
                parts = line.split("\t")
                if len(parts) > ci:
                    rows[parts[ci]] = parts[:4]

    added = 0
    for rec in records:
        code = city_code(rec)
        if len(code) != 6 or code in rows:
            continue
        province = code[:2] + "0000"
        name = str(rec.get("CITYNAME") or "").strip()
        rows[code] = [province, ud.PROVINCE_NAMES.get(province, ""), code, name]
        added += 1

    if added or not os.path.exists(path):
        os.makedirs(data_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write((header or CITIES_HEADER) + "\n")
            for code in sorted(rows):
                row = (list(rows[code]) + ["", "", "", ""])[:4]
                fh.write("\t".join(row) + "\n")
    return added


def update_city_db(records, data_dir):
    import update_database as ud

    groups = {}
    skipped = 0
    for rec in records:
        dt = parse_datatime(rec)
        code = city_code(rec)
        if dt is None or len(code) != 6:
            skipped += 1
            continue
        province = code[:2] + "0000"
        values = {p: ud.clean_num(rec.get(f)) for p, f in POLLUTANT_FIELDS.items()}
        key = dt.strftime("%Y-%m-%d %H:%M:%S")
        groups.setdefault((province, code, dt.year), {})[key] = values

    updated = created = rows = 0
    for (province, code, year), updates in sorted(groups.items()):
        path = os.path.join(data_dir, province, code, f"{year}.csv")
        items = sorted(updates.items())
        if os.path.exists(path):
            ud.update_existing_file(path, items)
            updated += 1
        else:
            ud.build_new_file(path, items, year)
            created += 1
        rows += len(items)
    print(
        f"并入城市库 {data_dir}: 更新 {updated} 个文件, 新建 {created} 个, "
        f"写入 {rows} 行, 跳过 {skipped}"
    )
    print(f"cities_info.txt: 新增 {update_cities_info(data_dir, records)} 个城市")
    return updated + created


def city_code(record):
    raw = str(record.get("CITY_AIR_ID") or record.get("ID") or "")
    digits = re.sub(r"\D", "", raw)
    return digits[:6] if len(digits) >= 6 else digits


def fetch_index_data(local_place="北京", timeout=120, retries=3):
    session = requests.Session()
    session.headers.update(HEADERS)
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            start = time.time()
            resp = session.post(
                INDEX_URL,
                data={"localPlace": local_place},
                timeout=(30, timeout),
                verify=False,
            )
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, dict) or "detail" in data:
                raise RuntimeError(f"接口返回异常: {str(data)[:120]}")
            print(f"下载完成, 用时 {time.time()-start:.1f}s")
            return data
        except Exception as exc:
            last_err = exc
            print(f"第 {attempt} 次下载失败: {exc}", file=sys.stderr)
            if attempt < retries:
                wait = 3 * attempt
                print(f"{wait}s 后重试...", file=sys.stderr)
                time.sleep(wait)
    raise RuntimeError(f"下载失败 (已重试 {retries} 次): {last_err}")


def normalize(records, columns):
    rows = []
    for rec in records:
        row = {}
        for key, label in columns:
            if key == "cityCode":
                row[label] = city_code(rec)
            elif key in ("MAXPOLL", "MAX_POLLUTION"):
                row[label] = strip_html(rec.get(key))
            else:
                val = rec.get(key)
                row[label] = "" if val is None else val
        rows.append(row)
    return rows


def write_csv(rows, columns, path):
    fields = [label for _, label in columns]
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_xlsx(rows, columns, path):
    try:
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError("导出 Excel 需要 pandas: pip install pandas openpyxl") from exc
    pd.DataFrame(rows, columns=[label for _, label in columns]).to_excel(path, index=False)


def summarize(records, rows, kind):
    if not records:
        print("未获取到数据")
        return
    when = records[0].get("DATATIME", "")
    label = "小时报" if kind == "hourly" else "日报"
    print(f"数据时间 : {when}")
    print(f"城市数量 : {len(records)}")
    print(f"\n前 5 条 ({label}):")
    for row in rows[:5]:
        print(
            f"  {str(row['城市']):<10} AQI={str(row['AQI']):<4} "
            f"{row['空气质量等级']}  首要污染物={row['首要污染物']}"
        )


def default_name(kind, fmt):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"city_aqi_{kind}_{stamp}.{fmt}"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="下载全国城市空气质量小时报/日报数据",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-t", "--type", choices=["hourly", "daily"], default="hourly", help="数据类型"
    )
    parser.add_argument("-o", "--output", help="输出文件路径")
    parser.add_argument(
        "-f", "--format", choices=["csv", "xlsx", "json"], default="csv", help="输出格式"
    )
    parser.add_argument("--local-place", default="北京", help="用于定位的城市名")
    parser.add_argument("--timeout", type=int, default=120, help="单次请求超时(秒)")
    parser.add_argument("--retries", type=int, default=3, help="失败重试次数")
    parser.add_argument("--no-save", action="store_true", help="不保存文件, 仅打印摘要")
    parser.add_argument(
        "--update-db", action="store_true", help="写入 national_city_data 城市数据库"
    )
    parser.add_argument(
        "-d",
        "--data-dir",
        default="./national_city_data",
        help="城市数据库根目录 (配合 --update-db)",
    )
    args = parser.parse_args(argv)

    if args.update_db and args.type != "hourly":
        print("--update-db 仅支持小时数据 (-t hourly)", file=sys.stderr)
        return 1

    try:
        data = fetch_index_data(
            local_place=args.local_place, timeout=args.timeout, retries=args.retries
        )
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1

    if args.type == "hourly":
        records = data.get("airList") or []
        columns = HOURLY_COLUMNS
    else:
        records = data.get("dayAirList") or []
        columns = DAILY_COLUMNS

    if not records:
        print("接口未返回对应数据", file=sys.stderr)
        return 1

    records.sort(key=lambda r: (r.get("CITYNAME") or ""))
    rows = normalize(records, columns)
    summarize(records, rows, args.type)

    if args.update_db:
        update_city_db(records, args.data_dir)

    if args.no_save:
        return 0

    out = args.output or default_name(args.type, args.format)
    if args.format == "csv":
        write_csv(rows, columns, out)
    elif args.format == "xlsx":
        write_xlsx(rows, columns, out)
    else:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(records, fh, ensure_ascii=False, indent=2)
    print(f"\n已保存: {os.path.abspath(out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
