#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
拉取大乐透(DLT)/双色球(SSQ)最新历史开奖数据。

数据源：
  DLT 主源：中国体彩网官方接口 webapi.sporttery.cn
  SSQ 主源：中国福彩网官方接口 www.cwl.gov.cn
  双彩种备用源：datachart.500.com 历史数据页

输出：CSV（按期号升序），格式与 analyze.py 输入一致：
  DLT: 期号,前1,前2,前3,前4,前5,后1,后2
  SSQ: 期号,红1,红2,红3,红4,红5,红6,蓝

用法：
  python fetch_data.py --game DLT --periods 200 --out dlt_history.csv
  python fetch_data.py --game SSQ --periods 200 --out ssq_history.csv
"""

import argparse
import json
import re
import ssl
import sys
import urllib.request

# Windows 控制台默认 GBK，强制 UTF-8 避免中文输出乱码
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def http_get(url, referer=None, timeout=20):
    """带浏览器 UA 的 GET 请求，返回原始字节。"""
    headers = {"User-Agent": UA, "Accept": "*/*"}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.read()


def fetch_dlt_sporttery(periods):
    """体彩官方接口，固定每页100条翻页拉取（页大小必须恒定，否则分页错位）。
    返回 [(期号, [前区5], [后区2])]。"""
    rows = []
    page = 1
    while len(rows) < periods:
        url = ("https://webapi.sporttery.cn/gateway/lottery/getHistoryPageListV1.qry"
               f"?gameNo=85&provinceId=0&pageSize=100&isVerify=1&pageNo={page}")
        data = json.loads(http_get(url, referer="https://static.sporttery.cn/"))
        lst = data.get("value", {}).get("list", [])
        if not lst:
            break
        for item in lst:
            nums = item["lotteryDrawResult"].split()
            rows.append((item["lotteryDrawNum"],
                         [int(x) for x in nums[:5]],
                         [int(x) for x in nums[5:7]]))
        page += 1
    return rows[:periods]


def fetch_ssq_cwl(periods):
    """福彩官方接口。返回 [(期号, [红6], [蓝1])]。"""
    url = ("http://www.cwl.gov.cn/cwl_admin/front/cwlkj/search/kjxx/findDrawNotice"
           f"?name=ssq&issueCount={periods}")
    data = json.loads(http_get(url, referer="http://www.cwl.gov.cn/kjxx/ssq/kjgg/"))
    rows = []
    for item in data.get("result", []):
        rows.append((item["code"],
                     [int(x) for x in item["red"].split(",")],
                     [int(item["blue"])]))
    return rows


def fetch_500(game, periods):
    """500.com 备用源，HTML 表格解析。"""
    path = "dlt" if game == "DLT" else "ssq"
    url = (f"https://datachart.500.com/{path}/history/newinc/history.php"
           f"?limit={periods}&sort=0")
    html = http_get(url).decode("gb2312", errors="ignore")
    rows = []
    for tr in re.findall(r'<tr class="t_tr1">(.*?)</tr>', html, re.S):
        tds = [t.strip() for t in re.findall(r"<td[^>]*>([^<]*)</td>", tr)]
        if game == "DLT" and len(tds) >= 8:
            rows.append((tds[0], [int(x) for x in tds[1:6]],
                         [int(x) for x in tds[6:8]]))
        elif game == "SSQ" and len(tds) >= 8:
            rows.append((tds[0], [int(x) for x in tds[1:7]], [int(tds[7])]))
    return rows


def validate(rows, game):
    """号码范围与结构校验，过滤非法行。"""
    if game == "DLT":
        mlo, mhi, mp, slo, shi, sp = 1, 35, 5, 1, 12, 2
    else:
        mlo, mhi, mp, slo, shi, sp = 1, 33, 6, 1, 16, 1
    ok = []
    for period, main, sub in rows:
        if (len(main) == mp and len(set(main)) == mp
                and all(mlo <= x <= mhi for x in main)
                and len(sub) == sp and len(set(sub)) == sp
                and all(slo <= x <= shi for x in sub)):
            ok.append((period, sorted(main), sorted(sub)))
    return ok


def load_existing(path, game):
    """读取既有CSV缓存，用于增量更新。文件不存在或解析失败返回空列表。"""
    mp = 5 if game == "DLT" else 6
    rows = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split(",")
                if len(parts) != 1 + mp + (2 if game == "DLT" else 1):
                    continue
                rows.append((parts[0],
                             [int(x) for x in parts[1:1 + mp]],
                             [int(x) for x in parts[1 + mp:]]))
    except (FileNotFoundError, ValueError):
        return []
    return validate(rows, game)


def merge_rows(existing, new):
    """合并去重并按期号升序。新数据覆盖同期号旧数据。"""
    uniq = {p: (p, m, s) for p, m, s in existing}
    uniq.update({p: (p, m, s) for p, m, s in new})
    return sorted(uniq.values(),
                  key=lambda r: int(re.sub(r"\D", "", r[0])))


def main():
    ap = argparse.ArgumentParser(description="拉取彩票最新历史开奖数据（支持CSV增量更新）")
    ap.add_argument("--game", required=True, choices=["DLT", "SSQ"], help="彩种")
    ap.add_argument("--periods", type=int, default=200, help="保留期数（默认200，最少100）")
    ap.add_argument("--out", default=None, help="输出CSV路径；已存在时执行增量更新")
    ap.add_argument("--full", action="store_true", help="忽略既有缓存，强制全量重拉")
    args = ap.parse_args()

    periods = max(args.periods, 100)  # 强制最少100期

    existing = []
    if args.out and not args.full:
        existing = load_existing(args.out, args.game)

    # 增量模式：缓存足量时只拉最新一页（100期足以覆盖两次分析之间的开奖间隔）
    fetch_n = 100 if len(existing) >= periods else periods

    def run_fetch(n):
        fetchers = ([("体彩官方接口", lambda: fetch_dlt_sporttery(n))]
                    if args.game == "DLT"
                    else [("福彩官方接口", lambda: fetch_ssq_cwl(n))])
        fetchers.append(("500.com备用源", lambda: fetch_500(args.game, n)))
        for name, fn in fetchers:
            try:
                got = validate(fn(), args.game)
                if got:
                    return got, name
            except Exception as exc:
                print(f"[警告] {name} 拉取失败：{exc}，尝试下一数据源", file=sys.stderr)
        return [], ""

    new_rows, source = run_fetch(fetch_n)
    rows = merge_rows(existing, new_rows)

    # 增量后仍不足（缓存断档过大）→ 回退全量重拉
    if len(rows) < periods and fetch_n < periods:
        new_rows, source = run_fetch(periods)
        rows = merge_rows(existing, new_rows)

    if len(rows) < 100:
        print("[错误] 所有数据源均失败或数据不足100期，请手动提供历史数据", file=sys.stderr)
        sys.exit(1)

    rows = rows[-periods:]
    old_latest = existing[-1][0] if existing else None
    added = (sum(1 for p, _, _ in rows
                 if int(re.sub(r"\D", "", p)) > int(re.sub(r"\D", "", old_latest)))
             if old_latest else len(rows))
    mode = "增量更新" if existing and not args.full else "全量拉取"

    lines = [",".join([p] + [f"{x:02d}" for x in m] + [f"{x:02d}" for x in s])
             for p, m, s in rows]
    text = "\n".join(lines) + "\n"
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"[完成] {mode}；数据源：{source or '本地缓存(无新增)'}；新增 {added} 期；"
              f"共 {len(rows)} 期；范围 {rows[0][0]} ~ {rows[-1][0]}；已写入 {args.out}")
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
