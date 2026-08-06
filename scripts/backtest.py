#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
滚动回测：验证评分模型是否优于随机基线，量化硬过滤对真实开奖的通过率。

方法：
  用前 window 期数据预测下一期 → 统计"评分TopK池"对当期开奖号的覆盖数，
  与"随机选K个号"的理论期望对比。逐期滚动，输出均值与提升幅度。

意义：
  彩票开奖独立随机，理论上任何模型的长期覆盖数应收敛于随机期望。
  回测结果若无显著提升，说明模型仅是形态筛选工具而非预测工具——
  这正是本 skill 的诚实性检验。

用法：
  python backtest.py --game DLT --data ../data/dlt_history.csv --window 100
"""

import argparse
import math
import sys
from pathlib import Path

# 复用分析引擎的全部计算逻辑
sys.path.insert(0, str(Path(__file__).parent))
from analyze import (PARAMS, load_data, base_indicators, bayes_scores,
                     calibrate_params, filter_pass_rate, hard_filter_main)

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def fused_pool(rows, p, game, pool_size):
    """用与 analyze.py 一致的融合逻辑（去掉蒙特卡洛以加速）生成主区Top池。"""
    base = base_indicators(rows, p, game)
    bayes_main, _ = bayes_scores(rows, p)

    def norm(d):
        vals = list(d.values())
        lo, hi = min(vals), max(vals)
        r = hi - lo if hi > lo else 1.0
        return {k: (v - lo) / r for k, v in d.items()}

    nb = norm(base["main_scores"])
    np_ = norm(bayes_main)
    fused = {k: 0.73 * nb[k] + 0.27 * np_[k] for k in nb}
    ranked = sorted(fused.items(), key=lambda kv: -kv[1])
    return [n for n, _ in ranked[:pool_size]], base


def main():
    ap = argparse.ArgumentParser(description="评分模型滚动回测")
    ap.add_argument("--game", required=True, choices=["DLT", "SSQ"])
    ap.add_argument("--data", required=True, help="历史数据CSV")
    ap.add_argument("--window", type=int, default=100, help="训练窗口期数（默认100）")
    ap.add_argument("--pool", type=int, default=None,
                    help="Top池大小（默认取彩种 main_pool 参数）")
    args = ap.parse_args()

    p = PARAMS[args.game]
    pool_size = args.pool or p["main_pool"]
    rows = load_data(args.data, p)
    if len(rows) < args.window + 20:
        print(f"[错误] 数据 {len(rows)} 期不足以回测（需 ≥ window+20）", file=sys.stderr)
        sys.exit(1)

    mhi = p["main_range"][1] - p["main_range"][0] + 1
    # 随机基线：从 mhi 个号里随机取 pool_size 个，覆盖开奖 main_pick 个号的期望
    random_expect = p["main_pick"] * pool_size / mhi

    hits, kills_fixed, kills_cal = [], 0, 0
    n_tests = len(rows) - args.window
    for i in range(n_tests):
        train = rows[i:i + args.window]
        actual = rows[i + args.window][1]
        pool, base = fused_pool(train, p, args.game, pool_size)
        hits.append(len(set(actual) & set(pool)))
        # 当期开奖是否会被两种过滤模式拦截（校准阈值用训练窗口内数据，无未来信息泄漏）
        if not hard_filter_main(actual, train[-1][1], p):
            kills_fixed += 1
        p_cal = calibrate_params(train, p)
        if not hard_filter_main(actual, train[-1][1], p_cal):
            kills_cal += 1

    avg_hit = sum(hits) / len(hits)
    # 全中（开奖号全在池内）比例
    full_cover = sum(1 for h in hits if h == p["main_pick"]) / len(hits)
    lift = (avg_hit - random_expect) / random_expect * 100

    # 简单显著性：覆盖数近似二项，标准误
    se = math.sqrt(sum((h - avg_hit) ** 2 for h in hits) / len(hits)) / math.sqrt(len(hits))
    z = (avg_hit - random_expect) / se if se > 0 else 0.0

    fp = filter_pass_rate(rows, p)

    print(f"=== {args.game} 滚动回测报告 ===")
    print(f"数据：{rows[0][0]} ~ {rows[-1][0]}；训练窗口 {args.window} 期；"
          f"回测 {n_tests} 期")
    print()
    print(f"[评分模型 Top{pool_size} 池覆盖能力]")
    print(f"  实际平均覆盖：{avg_hit:.3f} / {p['main_pick']} 个开奖号")
    print(f"  随机基线期望：{random_expect:.3f}")
    print(f"  提升幅度：{lift:+.1f}%（z={z:.2f}；|z|<2 视为无显著差异）")
    print(f"  开奖号全部落入池内的比例：{full_cover:.1%}")
    print()
    print(f"[硬过滤对真实开奖的拦截]")
    print(f"  固定阈值模式：拦截 {kills_fixed}/{n_tests}"
          f"（{kills_fixed / n_tests:.1%}）——仅 --fixed-filter 时使用")
    print(f"  校准模式（默认）：拦截 {kills_cal}/{n_tests}"
          f"（{kills_cal / n_tests:.1%}）——滚动窗口校准，无未来信息泄漏")
    print(f"  全量历史固定阈值通过率：{fp['历史通过率']:.1%}")
    print(f"  被滤原因分布（固定阈值）：{fp['被滤原因分布']}")
    print()
    if abs(z) < 2:
        print("[结论] 评分模型覆盖能力与随机选号无显著差异——符合彩票独立随机的数学本质。")
        print("       本工具的价值在于形态筛选与资金结构管理（复式/胆拖），而非预测。")
    else:
        print("[结论] 覆盖能力与随机基线存在统计差异，建议扩大回测样本复核（谨防过拟合）。")


if __name__ == "__main__":
    main()
