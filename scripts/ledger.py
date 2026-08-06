#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
推荐账本：持久化记录每次推荐，开奖后自动结算命中与奖金，累计实盘统计。

设计定位（诚实性声明）：
  账本是"诚实的实盘账目"，用于长期对照模型表现与随机期望——
  它**不能**提高中奖概率（回测已证明覆盖能力与随机无显著差异），
  也**禁止**用命中记录反向调优评分权重（那是过拟合噪声）。
  合法的自我更新只有一种：硬过滤校准阈值随新数据自动刷新（analyze.py 已实现）。

存储：data/ledger_dlt.jsonl / data/ledger_ssq.jsonl，每行一条 JSON 记录。
增量结算：status=pending 的记录在目标期开奖数据可用时结算一次，
          已 settled 的记录永不重复处理（与 CSV 增量拉取同思路）。

CLI 用法：
  python ledger.py --game DLT --settle          # 结算所有待开奖记录
  python ledger.py --game DLT --stats           # 输出累计统计
"""

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# 固定奖参考值；浮动奖(None)单独计数不计金额
PRIZE = {
    "DLT": {
        (5, 2): ("一等奖", None), (5, 1): ("二等奖", None),
        (5, 0): ("三等奖", 10000), (4, 2): ("四等奖", 3000),
        (4, 1): ("五等奖", 300), (3, 2): ("六等奖", 200),
        (4, 0): ("七等奖", 100), (3, 1): ("八等奖", 15), (2, 2): ("八等奖", 15),
        (3, 0): ("九等奖", 5), (1, 2): ("九等奖", 5),
        (2, 1): ("九等奖", 5), (0, 2): ("九等奖", 5),
    },
    "SSQ": {
        (6, 1): ("一等奖", None), (6, 0): ("二等奖", None),
        (5, 1): ("三等奖", 3000), (5, 0): ("四等奖", 200), (4, 1): ("四等奖", 200),
        (4, 0): ("五等奖", 10), (3, 1): ("五等奖", 10),
        (2, 1): ("六等奖", 5), (1, 1): ("六等奖", 5), (0, 1): ("六等奖", 5),
    },
}

# 随机单注的固定奖期望（用于账本对照）：sum(P(命中i+j)*奖金)
def random_expectation(game, mp, mn, sp, sn):
    """随机一注的固定奖参考期望值（元）。mn/sn为主/副区号码总数。"""
    exp = 0.0
    for (i, j), (_, amount) in PRIZE[game].items():
        if amount is None:
            continue
        pm = (math.comb(mp, i) * math.comb(mn - mp, mp - i)) / math.comb(mn, mp)
        ps = (math.comb(sp, j) * math.comb(sn - sp, sp - j)) / math.comb(sn, sp)
        exp += pm * ps * amount
    return exp

GAME_DIMS = {"DLT": (5, 35, 2, 12), "SSQ": (6, 33, 1, 16)}


def ledger_path(game, data_dir=None):
    base = Path(data_dir) if data_dir else Path(__file__).parent.parent / "data"
    base.mkdir(parents=True, exist_ok=True)
    return base / f"ledger_{game.lower()}.jsonl"


def load_ledger(game, data_dir=None):
    path = ledger_path(game, data_dir)
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def save_ledger(records, game, data_dir=None):
    path = ledger_path(game, data_dir)
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def record_recommendation(game, mode, data_latest_period, payload, cost,
                          data_dir=None):
    """落盘一条推荐记录（status=pending，待目标期开奖后结算）。

    去重：同一 data_latest_period + mode + payload 完全相同的 pending 记录不重复写入。
    """
    records = load_ledger(game, data_dir)
    for r in records:
        if (r["status"] == "pending" and r["mode"] == mode
                and r["data_latest_period"] == data_latest_period
                and r["payload"] == payload):
            return r["id"], False  # 已存在，不重复记录
    rid = f"{game}-{data_latest_period}-{len(records) + 1}"
    records.append({
        "id": rid,
        "recorded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "game": game,
        "mode": mode,                      # 单式 / 复式 / 胆拖
        "data_latest_period": data_latest_period,  # 推荐时数据最新期，目标=其后一期
        "payload": payload,
        "cost": cost,
        "status": "pending",
    })
    save_ledger(records, game, data_dir)
    return rid, True


def _prize_lookup(game, mh, sh):
    return PRIZE[game].get((mh, sh))


def _settle_single(game, tickets, win_main, win_sub):
    """单式结算：逐注命中。"""
    details, fixed, floating = [], 0, 0
    for t in tickets:
        mh = len(set(t["main"]) & set(win_main))
        sh = len(set(t["sub"]) & set(win_sub))
        tier = _prize_lookup(game, mh, sh)
        if tier:
            name, amount = tier
            if amount is None:
                floating += 1
            else:
                fixed += amount
            details.append({"命中": f"{mh}+{sh}", "奖级": name})
        else:
            details.append({"命中": f"{mh}+{sh}", "奖级": None})
    return details, fixed, floating


def _settle_combo(game, mp, sp, m_nums, s_nums, win_main, win_sub):
    """复式结算（组合数学展开，不枚举）：
    奖金 = Σ C(主中,i)C(主未中,mp-i) × C(副中,j)C(副未中,sp-j) × 奖金(i,j)
    """
    mh = len(set(m_nums) & set(win_main))
    sh = len(set(s_nums) & set(win_sub))
    mn, sn = len(m_nums), len(s_nums)
    fixed, floating = 0, 0
    for (i, j), (_, amount) in PRIZE[game].items():
        if i > mh or mp - i > mn - mh or j > sh or sp - j > sn - sh:
            continue
        cnt = (math.comb(mh, i) * math.comb(mn - mh, mp - i)
               * math.comb(sh, j) * math.comb(sn - sh, sp - j))
        if cnt <= 0:
            continue
        if amount is None:
            floating += cnt
        else:
            fixed += cnt * amount
    return {"主区命中": mh, "副区命中": sh}, fixed, floating


def _settle_dantuo(game, mp, sp, dan, tuo, s_nums, win_main, win_sub):
    """胆拖结算：胆码必含，从拖码取 (mp-胆数) 个。"""
    dh = len(set(dan) & set(win_main))
    th = len(set(tuo) & set(win_main))
    sh = len(set(s_nums) & set(win_sub))
    need = mp - len(dan)
    tn, sn = len(tuo), len(s_nums)
    fixed, floating = 0, 0
    for (i, j), (_, amount) in PRIZE[game].items():
        k = i - dh  # 需要从拖码命中的个数
        if k < 0 or k > th or need - k > tn - th:
            continue
        if j > sh or sp - j > sn - sh:
            continue
        cnt = (math.comb(th, k) * math.comb(tn - th, need - k)
               * math.comb(sh, j) * math.comb(sn - sh, sp - j))
        if cnt <= 0:
            continue
        if amount is None:
            floating += cnt
        else:
            fixed += cnt * amount
    return {"胆码命中": dh, "拖码命中": th, "副区命中": sh}, fixed, floating


def settle_pending(game, rows, data_dir=None):
    """结算所有 pending 记录：目标期（data_latest_period 的下一期）已开奖则结算。

    rows: load_data 返回的升序开奖列表。幂等——settled/expired 记录不再处理。
    数据窗口已滚过推荐期号的记录标记为 expired（避免永久滞留 pending）。
    返回本次新结算的记录列表。
    """
    mp, _, sp, _ = GAME_DIMS[game]
    records = load_ledger(game, data_dir)
    period_index = {r[0]: i for i, r in enumerate(rows)}
    earliest = int("".join(c for c in rows[0][0] if c.isdigit())) if rows else 0
    newly, changed = [], False
    for rec in records:
        if rec["status"] != "pending":
            continue
        base_p = rec["data_latest_period"]
        if base_p not in period_index:
            base_num = int("".join(c for c in base_p if c.isdigit()))
            if base_num < earliest:
                # 数据窗口已滚过：无法自动结算，标记过期（可用 check.py 手动核对）
                rec["status"] = "expired"
                rec["expired_reason"] = (f"推荐基准期 {base_p} 已滚出当前数据窗口"
                                         f"（{rows[0][0]} ~ {rows[-1][0]}），"
                                         "如需核对请用 check.py --period 手动对奖")
                changed = True
            continue  # 期号异常或未来期，留待下次
        idx = period_index[base_p]
        if idx + 1 >= len(rows):
            continue  # 目标期未开奖
        target_period, win_main, win_sub = rows[idx + 1]

        pl = rec["payload"]
        if rec["mode"] == "单式":
            detail, fixed, floating = _settle_single(game, pl["tickets"],
                                                     win_main, win_sub)
        elif rec["mode"] == "复式":
            detail, fixed, floating = [], 0, 0
            for g in pl["groups"]:
                d, fx, fl = _settle_combo(game, mp, sp, g["main"], g["sub"],
                                          win_main, win_sub)
                detail.append(d)
                fixed += fx
                floating += fl
        else:  # 胆拖
            detail, fixed, floating = _settle_dantuo(
                game, mp, sp, pl["dan"], pl["tuo"], pl["sub"],
                win_main, win_sub)

        rec["status"] = "settled"
        rec["settle"] = {
            "settled_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "target_period": target_period,
            "开奖": {"主区": win_main, "副区": win_sub},
            "命中明细": detail,
            "固定奖金额": fixed,
            "浮动奖注数": floating,
        }
        newly.append(rec)
        changed = True
    if changed:
        save_ledger(records, game, data_dir)
    return newly


def stats(game, data_dir=None):
    """累计实盘统计：成本、固定奖回收、回报率，与随机期望对照。"""
    records = load_ledger(game, data_dir)
    settled = [r for r in records if r["status"] == "settled"]
    pending = [r for r in records if r["status"] == "pending"]
    expired = [r for r in records if r["status"] == "expired"]
    total_cost = sum(r["cost"] for r in settled)
    total_fixed = sum(r["settle"]["固定奖金额"] for r in settled)
    total_floating = sum(r["settle"]["浮动奖注数"] for r in settled)
    total_bets = sum(r["cost"] // 2 for r in settled)

    mp, mn, sp, sn = GAME_DIMS[game]
    rand_exp = random_expectation(game, mp, mn, sp, sn)

    out = {
        "已结算次数": len(settled),
        "待开奖次数": len(pending),
        "累计投入(元)": total_cost,
        "累计等效注数": total_bets,
        "固定奖回收(元)": total_fixed,
        "浮动奖注数": total_floating,
        "固定奖回报率": round(total_fixed / total_cost, 4) if total_cost else None,
        "随机单注固定奖期望(元/2元)": round(rand_exp, 3),
        "随机基线回报率": round(rand_exp / 2, 4),
        "说明": ("回报率长期应收敛于随机基线附近——账本用于诚实对照，"
               "不构成模型有效性证据，禁止据此调参"),
    }
    if expired:
        out["已过期次数"] = len(expired)
        out["过期说明"] = "数据窗口滚过未及结算的记录，可用 check.py --period 手动核对"
    return out


def main():
    ap = argparse.ArgumentParser(description="推荐账本：结算与统计")
    ap.add_argument("--game", required=True, choices=["DLT", "SSQ"])
    ap.add_argument("--settle", action="store_true", help="结算待开奖记录")
    ap.add_argument("--stats", action="store_true", help="输出累计统计")
    ap.add_argument("--data", default=None, help="历史数据CSV（默认 data/ 目录）")
    args = ap.parse_args()

    from analyze import PARAMS, load_data
    data_path = args.data or str(Path(__file__).parent.parent / "data"
                                 / ("dlt_history.csv" if args.game == "DLT"
                                    else "ssq_history.csv"))
    if args.settle:
        rows = load_data(data_path, PARAMS[args.game])
        newly = settle_pending(args.game, rows)
        if not newly:
            print("无可结算记录（全部已结算或目标期未开奖）")
        for r in newly:
            s = r["settle"]
            print(f"[结算] {r['id']}（{r['mode']}）→ 第{s['target_period']}期："
                  f"固定奖 {s['固定奖金额']} 元，浮动奖 {s['浮动奖注数']} 注")
    if args.stats or not args.settle:
        print(json.dumps(stats(args.game), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
