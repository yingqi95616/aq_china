#!/usr/bin/env python3
"""下载 air.cnemc.cn 近期历史(回溯)的点位空气质量数据。

说明: 该平台的"历史"能力有限, 只保留最近约 24 小时的整点快照
(接口 GetAQIHistoryByConditionHis), 无法获取多年前的数据。本程序按小时
回溯下载这些快照, 合并成一个 CSV, 并可选地并入 national_data 数据库。

用法示例:
    python history_download.py                        # 最近 24 小时
    python history_download.py --hours 6              # 最近 6 小时
    python history_download.py --end "2026-10-11 11:00:00" --hours 3
    python history_download.py --update-db            # 下载后更新数据库
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta

import requests
import urllib3

import aq_download as aq

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HIS_URL = aq.BASE + "/HourChangesPublish/GetAQIHistoryByConditionHis"


def fetch_hour(date_str, timeout=600, retries=3):
    session = requests.Session()
    session.headers.update(aq.HEADERS)
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            resp = session.post(
                HIS_URL,
                data={"date": date_str},
                timeout=(30, timeout),
                verify=False,
            )
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else []
        except Exception as exc:
            last_err = exc
            if attempt < retries:
                time.sleep(5 * attempt)
    print(f"    请求失败({date_str}): {last_err}", file=sys.stderr)
    return None


def hour_key(dt):
    return dt.strftime("%Y%m%d%H")


def load_station_city_map(data_dir):
    path = os.path.join(data_dir, "province_city_stations_info.txt")
    mapping = {}
    if not os.path.exists(path):
        return mapping
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    if not lines:
        return mapping
    header = lines[0].split("\t")
    try:
        si = header.index("StationCode")
        ci = header.index("CityCode")
    except ValueError:
        return mapping
    for line in lines[1:]:
        parts = line.split("\t")
        if len(parts) > max(si, ci):
            mapping[parts[si]] = parts[ci]
    return mapping


def enrich_city_codes(records, data_dir, no_live_map, timeout, retries):
    mapping = load_station_city_map(data_dir)
    missing = {
        r.get("StationCode")
        for r in records
        if r.get("StationCode") and r.get("StationCode") not in mapping
    }
    if missing and not no_live_map:
        print(f"  从实时接口补充 {len(missing)} 个点位的城市代码...")
        try:
            live = aq.fetch_raw(timeout=timeout, retries=retries)
            for rec in live:
                mapping.setdefault(rec.get("StationCode"), rec.get("CityCode"))
        except Exception as exc:
            print(f"  实时接口补充失败: {exc}", file=sys.stderr)
    filled = 0
    still = 0
    for rec in records:
        code = mapping.get(rec.get("StationCode"))
        if code:
            rec["CityCode"] = code
            filled += 1
        else:
            still += 1
    print(f"  城市代码: 已补齐 {filled}, 仍缺失 {still}")


def keep_matching(records, target):
    key = hour_key(target)
    out = []
    for rec in records:
        dt = aq.parse_timepoint(rec)
        if dt and hour_key(dt) == key:
            out.append(rec)
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="下载近期历史点位空气质量数据 (平台仅保留最近约 24 小时)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--hours", type=int, default=24, help="回溯小时数")
    parser.add_argument(
        "--end",
        help="结束时刻 (YYYY-MM-DD HH:MM:SS), 默认当前整点",
    )
    parser.add_argument("-o", "--output", help="合并输出的 CSV 路径")
    parser.add_argument("--timeout", type=int, default=600, help="单次请求超时(秒)")
    parser.add_argument("--retries", type=int, default=3, help="失败重试次数")
    parser.add_argument("--raw-dir", help="额外保存每小时原始 JSON 的目录")
    parser.add_argument(
        "--update-db", action="store_true", help="下载后并入 national_data 数据库"
    )
    parser.add_argument(
        "-d",
        "--data-dir",
        default="./national_data",
        help="数据库根目录 (含点位信息文件)",
    )
    parser.add_argument(
        "--no-live-map",
        action="store_true",
        help="不通过实时接口补充点位城市代码, 只用本地点位信息文件",
    )
    args = parser.parse_args(argv)

    data_dir = args.data_dir

    if args.end:
        try:
            end = datetime.fromisoformat(args.end)
        except ValueError:
            print(f"无法解析 --end: {args.end}", file=sys.stderr)
            return 1
    else:
        end = datetime.now()
    end = end.replace(minute=0, second=0, microsecond=0)

    if args.hours < 1:
        print("--hours 必须 >= 1", file=sys.stderr)
        return 1

    if args.raw_dir:
        os.makedirs(args.raw_dir, exist_ok=True)

    print(f"回溯范围: 截至 {end:%Y-%m-%d %H:%M} 的最近 {args.hours} 小时")
    all_records = []
    got = 0
    empty = 0
    start = time.time()

    for i in range(args.hours):
        target = end - timedelta(hours=i)
        stamp = target.strftime("%Y-%m-%d %H:%M:%S")
        records = fetch_hour(stamp, timeout=args.timeout, retries=args.retries)
        if records is None:
            empty += 1
            continue
        records = keep_matching(records, target)
        if not records:
            print(f"  {stamp}  无数据")
            empty += 1
            continue
        all_records.extend(records)
        got += 1
        print(f"  {stamp}  {len(records)} 个点位")
        if args.raw_dir:
            with open(
                os.path.join(args.raw_dir, f"his_{hour_key(target)}.json"),
                "w",
                encoding="utf-8",
            ) as fh:
                json.dump(records, fh, ensure_ascii=False)

    if not all_records:
        print("未获取到任何历史数据", file=sys.stderr)
        return 1

    enrich_city_codes(
        all_records, data_dir, args.no_live_map, args.timeout, args.retries
    )

    rows = aq.normalize(all_records)
    out = args.output or f"station_history_{end:%Y%m%d%H}_{args.hours}h.csv"
    aq.write_csv(rows, out)
    aq.summarize(all_records, rows)
    print(f"\n小时数: {got} (无数据 {empty}), 记录数: {len(all_records)}")
    print(f"已保存: {os.path.abspath(out)}  (用时 {time.time()-start:.0f}s)")

    if args.update_db:
        import update_database

        print(f"\n并入数据库: {data_dir}")
        rc = update_database.main(["-i", out, "-d", data_dir])
        if rc != 0:
            return rc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
