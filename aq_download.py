#!/usr/bin/env python3
"""下载全国城市空气质量实时发布平台「点位空气质量」最近一期数据。

数据来源: https://www.cnemc.cn/sssj/qgkqzlyb/ -> https://air.cnemc.cn:18007/StationPublish/Index

用法示例:
    python aq_download.py                     # 保存为当前时间命名的 CSV
    python aq_download.py -o aq.csv           # 指定输出文件
    python aq_download.py -f xlsx             # 保存为 Excel (需 openpyxl)
    python aq_download.py -f json             # 保存原始 JSON
    python aq_download.py --no-save           # 只打印摘要
"""

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE = "https://air.cnemc.cn:18007"
DATA_URL = BASE + "/HourChangesPublish/GetAllAQIPublishLive"
REFERER = BASE + "/StationPublish/Index"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": REFERER,
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
}

COLUMNS = [
    ("TimePointStr", "数据时间"),
    ("Area", "城市/地区"),
    ("PositionName", "点位名称"),
    ("StationCode", "点位编码"),
    ("AQI", "AQI"),
    ("Quality", "空气质量等级"),
    ("PrimaryPollutant", "首要污染物"),
    ("PM2_5", "PM2.5(μg/m³)"),
    ("PM2_5_24h", "PM2.5_24h(μg/m³)"),
    ("PM10", "PM10(μg/m³)"),
    ("PM10_24h", "PM10_24h(μg/m³)"),
    ("SO2", "SO2(μg/m³)"),
    ("SO2_24h", "SO2_24h(μg/m³)"),
    ("NO2", "NO2(μg/m³)"),
    ("NO2_24h", "NO2_24h(μg/m³)"),
    ("CO", "CO(mg/m³)"),
    ("CO_24h", "CO_24h(mg/m³)"),
    ("O3", "O3(μg/m³)"),
    ("O3_24h", "O3_24h(μg/m³)"),
    ("O3_8h", "O3_8h(μg/m³)"),
    ("O3_8h_24h", "O3_8h_24h(μg/m³)"),
    ("Latitude", "纬度"),
    ("Longitude", "经度"),
    ("ProvinceId", "省份ID"),
    ("CityCode", "城市代码"),
    ("Unheathful", "健康影响"),
    ("Measure", "建议措施"),
]


def fetch_raw(timeout=600, retries=3):
    session = requests.Session()
    session.headers.update(HEADERS)
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            start = time.time()
            with session.post(
                DATA_URL, data={}, stream=True, timeout=(30, timeout), verify=False
            ) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("Content-Length") or 0)
                chunks = []
                received = 0
                shown = -1
                for chunk in resp.iter_content(64 * 1024):
                    if not chunk:
                        continue
                    chunks.append(chunk)
                    received += len(chunk)
                    if total:
                        pct = int(received * 100 / total)
                        if pct - shown >= 5:
                            shown = pct
                            sys.stderr.write(
                                f"\r下载中 {pct:3d}%  {received/1024:.0f}/"
                                f"{total/1024:.0f} KB"
                            )
                            sys.stderr.flush()
                    else:
                        sys.stderr.write(f"\r下载中 {received/1024:.0f} KB")
                        sys.stderr.flush()
                sys.stderr.write("\n")
                text = b"".join(chunks).decode("utf-8-sig")
                data = json.loads(text)
            print(f"下载完成: {len(data)} 个点位, 用时 {time.time()-start:.1f}s")
            return data
        except Exception as exc:
            last_err = exc
            print(f"第 {attempt} 次下载失败: {exc}", file=sys.stderr)
            if attempt < retries:
                wait = 5 * attempt
                print(f"{wait}s 后重试...", file=sys.stderr)
                time.sleep(wait)
    raise RuntimeError(f"下载失败 (已重试 {retries} 次): {last_err}")


def parse_timepoint(record):
    raw = record.get("TimePoint") or ""
    if raw.startswith("/Date(") and raw.endswith(")/"):
        try:
            ms = int(raw[6:-2])
            return datetime.fromtimestamp(ms / 1000, tz=timezone(timedelta(hours=8)))
        except ValueError:
            return None
    return None


def normalize(records):
    rows = []
    for rec in records:
        row = {}
        for key, label in COLUMNS:
            val = rec.get(key)
            if val is None or val == "NA" or val == "":
                val = ""
            row[label] = val
        dt = parse_timepoint(rec)
        row["时间(ISO)"] = dt.isoformat() if dt else ""
        rows.append(row)
    return rows


def write_csv(rows, path):
    fields = [label for _, label in COLUMNS] + ["时间(ISO)"]
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_xlsx(rows, path):
    try:
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError("导出 Excel 需要 pandas: pip install pandas openpyxl") from exc
    pd.DataFrame(rows).to_excel(path, index=False)
    try:
        import openpyxl  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("导出 Excel 需要 openpyxl: pip install openpyxl") from exc


def summarize(records, rows):
    if not records:
        print("未获取到数据")
        return
    tp = records[0].get("TimePointStr", "")
    cities = {(r.get("Area") or "") for r in records}
    published = sum(1 for r in records if r.get("IsPublish"))
    print(f"数据时间 : {tp}")
    print(f"点位总数 : {len(records)}")
    print(f"覆盖城市 : {len(cities)}")
    print(f"已发布   : {published}")
    print("\n前 5 条:")
    for row in rows[:5]:
        print(
            f"  {row['城市/地区']:<10} {row['点位名称']:<12} "
            f"AQI={row['AQI']:<4} {row['空气质量等级']}  首要污染物={row['首要污染物']}"
        )


def default_name(fmt):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"station_aqi_{stamp}.{fmt}"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="下载全国(点位)空气质量实时数据",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("-o", "--output", help="输出文件路径")
    parser.add_argument(
        "-f", "--format", choices=["csv", "xlsx", "json"], default="csv", help="输出格式"
    )
    parser.add_argument("--timeout", type=int, default=600, help="单次请求超时(秒)")
    parser.add_argument("--retries", type=int, default=3, help="失败重试次数")
    parser.add_argument("--no-save", action="store_true", help="不保存文件, 仅打印摘要")
    args = parser.parse_args(argv)

    try:
        records = fetch_raw(timeout=args.timeout, retries=args.retries)
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1

    records.sort(key=lambda r: (r.get("Area") or "", r.get("PositionName") or ""))
    rows = normalize(records)
    summarize(records, rows)

    if args.no_save:
        return 0

    out = args.output or default_name(args.format)
    if args.format == "csv":
        write_csv(rows, out)
    elif args.format == "xlsx":
        write_xlsx(rows, out)
    else:
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(records, fh, ensure_ascii=False, indent=2)
    print(f"\n已保存: {os.path.abspath(out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
