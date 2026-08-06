#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
skill 自检回归测试：覆盖数据加载、指标计算、过滤、复式/胆拖、结算数学、账本闭环。

不依赖网络（用本地 data/*.csv 或合成数据），全部通过输出 [PASS]，任一失败即退出码1。

用法：
  python selftest.py            # 完整自检（需 data/ 下有历史CSV）
  python selftest.py --offline  # 仅跑无需真实数据的合成用例
"""

import argparse
import json
import math
import random
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from analyze import (PARAMS, ac_value, odd_even, size_ratio, zone_ratio,
                     consecutive_groups, same_tail_count, wilson_bounds,
                     calibrate_params, hard_filter_main, load_data,
                     base_indicators, bayes_scores, parse_multi, parse_dantuo)
import ledger

PASSED = FAILED = 0


def check(name, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"[PASS] {name}")
    else:
        FAILED += 1
        print(f"[FAIL] {name} {detail}")


def synth_rows(game, n=150, seed=99):
    """合成 n 期合法开奖数据（仅用于离线测试计算路径，不代表真实分布）。"""
    rng = random.Random(seed)
    p = PARAMS[game]
    rows = []
    for i in range(n):
        main = sorted(rng.sample(range(p["main_range"][0],
                                       p["main_range"][1] + 1), p["main_pick"]))
        sub = sorted(rng.sample(range(p["sub_range"][0],
                                      p["sub_range"][1] + 1), p["sub_pick"]))
        rows.append((f"{2500 + i}", main, sub))
    return rows


def test_indicators():
    """形态指标的已知值用例。"""
    check("AC值 DLT", ac_value([3, 12, 18, 25, 31], 4) ==
          len({9, 15, 22, 28, 6, 13, 19, 7, 13, 6} | {abs(a - b) for a, b in
              [(3, 12), (3, 18), (3, 25), (3, 31), (12, 18), (12, 25),
               (12, 31), (18, 25), (18, 31), (25, 31)]}) - 4)
    check("奇偶比", odd_even([1, 2, 3, 4, 5]) == (3, 2))
    check("大小比 DLT split=17", size_ratio([1, 5, 18, 20, 35], 17) == (3, 2))
    check("三区比", zone_ratio([1, 13, 14, 25, 35],
                            ((1, 12), (13, 24), (25, 35))) == (1, 2, 2))
    check("连号组数", consecutive_groups([1, 2, 3, 10, 11]) == 2)
    check("同尾计数", same_tail_count([5, 15, 25, 8, 9]) == 3)
    lo, hi = wilson_bounds(30, 100)
    check("威尔逊区间含真值", lo < 0.3 < hi and 0 < lo < hi < 1)


def test_params_and_specs():
    """参数解析与校验。"""
    p = PARAMS["DLT"]
    check("复式解析 8+3", parse_multi("8+3", p) == (8, 3))
    for bad in ["5+2", "30+3", "abc"]:
        try:
            parse_multi(bad, p)
            check(f"复式非法值拒绝 {bad}", False)
        except ValueError:
            check(f"复式非法值拒绝 {bad}", True)
    check("胆拖解析 2*6", parse_dantuo("2*6", p) == (2, 6))
    for bad in ["5*3", "1*2", "xx"]:
        try:
            parse_dantuo(bad, p)
            check(f"胆拖非法值拒绝 {bad}", False)
        except ValueError:
            check(f"胆拖非法值拒绝 {bad}", True)


def test_settle_math():
    """结算组合数学：与手工核算值对照。"""
    win_m, win_s = [8, 16, 18, 24, 34], [9, 12]
    # 复式8+3全中：一等1注 + 二等C(2,1)C(1,1)=2注 → 浮动3
    _, fixed, floating = ledger._settle_combo(
        "DLT", 5, 2, [8, 11, 16, 18, 24, 29, 33, 34], [5, 9, 12], win_m, win_s)
    check("复式全中浮动奖3注", floating == 3, f"got {floating}")
    # 四等(4+2): C(5,4)C(3,1)C(2,2)C(1,0)=15注×3000=45000 包含在fixed中
    check("复式固定奖含四等15注", fixed >= 15 * 3000, f"got {fixed}")
    # 胆拖：2胆全中+拖中3
    _, f2, fl2 = ledger._settle_dantuo(
        "DLT", 5, 2, [8, 16], [18, 24, 34, 11, 29, 33], [9, 12, 5], win_m, win_s)
    check("胆拖全中浮动奖3注", fl2 == 3, f"got {fl2}")
    # 单式一等
    d, f3, fl3 = ledger._settle_single(
        "DLT", [{"main": win_m, "sub": win_s}], win_m, win_s)
    check("单式一等奖判定", d[0]["奖级"] == "一等奖" and fl3 == 1)
    # 随机期望为正且小于2元（彩票负期望的体现）
    exp = ledger.random_expectation("DLT", 5, 35, 2, 12)
    check("DLT随机固定奖期望∈(0,2)", 0 < exp < 2, f"got {exp}")
    exp2 = ledger.random_expectation("SSQ", 6, 33, 1, 16)
    check("SSQ随机固定奖期望∈(0,2)", 0 < exp2 < 2, f"got {exp2}")


def test_ledger_lifecycle():
    """账本闭环：记录→去重→结算→幂等→过期，全部在临时目录进行。"""
    tmp = tempfile.mkdtemp(prefix="ledger_test_")
    try:
        rows = synth_rows("DLT", 120)
        # 用倒数第2期作为推荐基准 → 最后一期即目标期，可结算
        base_p = rows[-2][0]
        payload = {"tickets": [{"main": [1, 2, 3, 4, 5], "sub": [1, 2]}]}
        rid1, created1 = ledger.record_recommendation(
            "DLT", "单式", base_p, payload, 2, data_dir=tmp)
        check("账本记录创建", created1)
        _, created2 = ledger.record_recommendation(
            "DLT", "单式", base_p, payload, 2, data_dir=tmp)
        check("账本同内容去重", not created2)
        newly = ledger.settle_pending("DLT", rows, data_dir=tmp)
        check("账本结算1条", len(newly) == 1 and newly[0]["status"] == "settled")
        newly2 = ledger.settle_pending("DLT", rows, data_dir=tmp)
        check("账本结算幂等", len(newly2) == 0)
        # 过期：推荐基准期早于数据窗口
        ledger.record_recommendation("DLT", "单式", "1000", payload, 2, data_dir=tmp)
        ledger.settle_pending("DLT", rows, data_dir=tmp)
        recs = ledger.load_ledger("DLT", data_dir=tmp)
        check("窗口滚过标记expired",
              any(r["status"] == "expired" for r in recs))
        st = ledger.stats("DLT", data_dir=tmp)
        check("统计含过期计数", st.get("已过期次数") == 1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_calibration(rows_by_game):
    """校准过滤：通过率显著高于固定阈值，且阈值来自数据本身。"""
    for game, rows in rows_by_game.items():
        p = PARAMS[game]
        cal = calibrate_params(rows, p)
        from analyze import filter_pass_rate
        r_fixed = filter_pass_rate(rows, p)["历史通过率"]
        r_cal = filter_pass_rate(rows, cal)["历史通过率"]
        check(f"{game} 校准通过率>固定阈值", r_cal > r_fixed,
              f"cal={r_cal} fixed={r_fixed}")
        check(f"{game} 校准通过率≥50%", r_cal >= 0.5, f"got {r_cal}")


def test_real_data():
    """真实数据全链路（需 data/*.csv 存在）。"""
    data_dir = Path(__file__).parent.parent / "data"
    rows_by_game = {}
    for game, fname in [("DLT", "dlt_history.csv"), ("SSQ", "ssq_history.csv")]:
        path = data_dir / fname
        if not path.exists():
            print(f"[SKIP] {game} 真实数据不存在（先运行 fetch_data.py）")
            continue
        p = PARAMS[game]
        rows = load_data(str(path), p)
        check(f"{game} 数据≥100期", len(rows) >= 100, f"got {len(rows)}")
        rows_by_game[game] = rows
        base = base_indicators(rows, p, game)
        check(f"{game} 上期号码合法",
              len(base["last_main"]) == p["main_pick"])
        bm, bs = bayes_scores(rows, p)
        check(f"{game} 贝叶斯后验归一合理",
              abs(sum(bm.values()) - 1.0) < 0.05,
              f"sum={sum(bm.values()):.3f}")
    if rows_by_game:
        test_calibration(rows_by_game)


def main():
    ap = argparse.ArgumentParser(description="skill 自检回归测试")
    ap.add_argument("--offline", action="store_true",
                    help="仅跑合成数据用例（无需真实CSV）")
    args = ap.parse_args()

    print("=== 彩票分析 skill 自检 ===\n")
    test_indicators()
    test_params_and_specs()
    test_settle_math()
    test_ledger_lifecycle()
    if not args.offline:
        test_real_data()

    print(f"\n结果：{PASSED} 通过，{FAILED} 失败")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
