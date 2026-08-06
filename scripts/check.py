#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
对奖工具：核对投注号码与指定期（默认最新期）开奖结果，输出中奖等级与奖金。

投注文件格式（每行一注，与 analyze.py 号码文本一致）：
  DLT: 03 08 18 22 29 + 02 05
  SSQ: 02 10 13 19 24 28 + 01

奖级（浮动奖按常见参考值估算，以官方实际派奖为准）：
  DLT：一等(5+2) 二等(5+1) 三等(5+0) 四等(4+2) 五等(4+1) 六等(3+2)
       七等(4+0) 八等(3+1/2+2) 九等(3+0/1+2/2+1/0+2)
  SSQ：一等(6+1) 二等(6+0) 三等(5+1) 四等(5+0/4+1) 五等(4+0/3+1) 六等(2+1/1+1/0+1)

用法：
  python check.py --game DLT --tickets my_tickets.txt                # 对最新期
  python check.py --game DLT --tickets my_tickets.txt --period 26082 # 对指定期
  python check.py --game SSQ --tickets "02 10 13 19 24 28 + 01"      # 直接传号码
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from analyze import PARAMS, load_data
from ledger import PRIZE  # 奖级表唯一定义在 ledger.py，避免双份维护

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def parse_ticket(line, p):
    """解析一注号码：'主区... + 副区...' 或纯空格分隔。"""
    line = line.strip()
    if not line:
        return None
    if "+" in line:
        left, right = line.split("+", 1)
        main = [int(x) for x in left.split()]
        sub = [int(x) for x in right.split()]
    else:
        parts = [int(x) for x in line.split()]
        main, sub = parts[:p["main_pick"]], parts[p["main_pick"]:]
    mlo, mhi = p["main_range"]
    slo, shi = p["sub_range"]
    if (len(main) != p["main_pick"] or len(set(main)) != p["main_pick"]
            or not all(mlo <= x <= mhi for x in main)
            or len(sub) != p["sub_pick"] or len(set(sub)) != p["sub_pick"]
            or not all(slo <= x <= shi for x in sub)):
        raise ValueError(f"号码非法：{line}")
    return sorted(main), sorted(sub)


def main():
    ap = argparse.ArgumentParser(description="彩票对奖工具")
    ap.add_argument("--game", required=True, choices=["DLT", "SSQ"])
    ap.add_argument("--tickets", required=True,
                    help="投注文件路径，或直接传单注号码字符串")
    ap.add_argument("--data", default=None,
                    help="历史数据CSV（默认 skill 的 data/ 目录）")
    ap.add_argument("--period", default=None, help="开奖期号（默认最新一期）")
    args = ap.parse_args()

    p = PARAMS[args.game]
    data_path = args.data or str(Path(__file__).parent.parent / "data"
                                 / ("dlt_history.csv" if args.game == "DLT"
                                    else "ssq_history.csv"))
    rows = load_data(data_path, p)
    if not rows:
        print("[错误] 无法读取开奖数据，请先运行 fetch_data.py", file=sys.stderr)
        sys.exit(1)

    if args.period:
        match = [r for r in rows if r[0] == args.period]
        if not match:
            print(f"[错误] 数据中未找到第 {args.period} 期；"
                  f"现有范围 {rows[0][0]} ~ {rows[-1][0]}。"
                  "若为新开期号请先运行 fetch_data.py 更新", file=sys.stderr)
            sys.exit(1)
        draw = match[0]
    else:
        draw = rows[-1]
    period, win_main, win_sub = draw

    tp = Path(args.tickets)
    lines = (tp.read_text(encoding="utf-8").splitlines()
             if tp.is_file() else [args.tickets])

    print(f"=== {args.game} 第 {period} 期对奖结果 ===")
    print(f"开奖号码：{' '.join(f'{x:02d}' for x in win_main)}"
          f" + {' '.join(f'{x:02d}' for x in win_sub)}")
    print()

    total_prize, winners = 0, 0
    for i, line in enumerate(lines, 1):
        try:
            parsed = parse_ticket(line, p)
        except ValueError as exc:
            print(f"注{i}: [跳过] {exc}")
            continue
        if parsed is None:
            continue
        main, sub = parsed
        mh = len(set(main) & set(win_main))
        sh = len(set(sub) & set(win_sub))
        tier = PRIZE[args.game].get((mh, sh))
        nums = (" ".join(f"{x:02d}" for x in main) + " + "
                + " ".join(f"{x:02d}" for x in sub))
        if tier:
            name, amount = tier
            winners += 1
            amt_str = "浮动奖(见官方公告)" if amount is None else f"约{amount}元"
            if amount:
                total_prize += amount
            print(f"注{i}: {nums}  → 中{mh}+{sh}【{name} {amt_str}】")
        else:
            print(f"注{i}: {nums}  → 中{mh}+{sh}，未中奖")

    print()
    print(f"合计：{winners} 注中奖；固定奖参考总额约 {total_prize} 元"
          "（浮动奖另计，以官方派奖为准）")


if __name__ == "__main__":
    main()
