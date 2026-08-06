#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
彩票高级综合分析引擎：14维基础指标 + 11项高级数学模型 + 硬过滤 + 组合优选。

输入：CSV 历史数据（期号升序或降序均可，自动排序），格式：
  DLT: 期号,前1,前2,前3,前4,前5,后1,后2
  SSQ: 期号,红1,红2,红3,红4,红5,红6,蓝

输出：JSON 分析结果（stdout 或 --out 文件），包含基础指标、各模型结果、推荐组合。

用法：
  python analyze.py --game DLT --data dlt_history.csv --bets 8
  python analyze.py --game SSQ --data ssq_history.csv --bets 8 --mc 200000
"""

import argparse
import itertools
import json
import math
import random
import re
import sys
from collections import Counter, defaultdict

# Windows 控制台默认 GBK，强制 UTF-8 避免中文输出乱码
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# ---------------------------------------------------------------- 彩种参数

PARAMS = {
    "DLT": {
        "main_range": (1, 35), "main_pick": 5,
        "sub_range": (1, 12), "sub_pick": 2,
        "ac_offset": 4, "ac_best": (6, 9), "ac_near": (5, 10),
        "sum_range": (85, 115), "span_range": (18, 28),
        "odd_even_ok": {(3, 2), (2, 3)},
        "size_split": 17, "size_ok": {(3, 2), (2, 3)},
        "zones": ((1, 12), (13, 24), (25, 35)),
        "zone_ok": {(2, 2, 1), (1, 2, 2), (2, 1, 2)},
        "same_tail_max": 2, "consec_max": 2, "repeat_ok": (0, 2),
        "main_pool": 20, "sub_pool": 6,
    },
    "SSQ": {
        "main_range": (1, 33), "main_pick": 6,
        "sub_range": (1, 16), "sub_pick": 1,
        "ac_offset": 5, "ac_best": (7, 10), "ac_near": (6, 11),
        "sum_range": (80, 130), "span_range": (16, 30),
        "odd_even_ok": {(3, 3), (4, 2), (2, 4)},
        "size_split": 16, "size_ok": {(3, 3), (4, 2), (2, 4)},
        "zones": ((1, 11), (12, 22), (23, 33)),
        "zone_ok": {(2, 2, 2), (3, 2, 1), (2, 3, 1), (1, 2, 3), (2, 1, 3)},
        "same_tail_max": 3, "consec_max": 2, "repeat_ok": (0, 2),
        "main_pool": 24, "sub_pool": 8,
    },
}

# ---------------------------------------------------------------- 数据加载


def load_data(path, p):
    """读取CSV，校验号码合法性，按期号升序返回 [(期号, 主区列表, 副区列表)]。"""
    mp, sp = p["main_pick"], p["sub_pick"]
    mlo, mhi = p["main_range"]
    slo, shi = p["sub_range"]
    rows = []
    with open(path, encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            line = line.strip().replace("+", " ").replace(",", " ")
            if not line:
                continue
            parts = line.split()
            if len(parts) != 1 + mp + sp:
                print(f"[警告] 第{ln}行字段数不符，跳过：{line}", file=sys.stderr)
                continue
            period = parts[0]
            main = sorted(int(x) for x in parts[1:1 + mp])
            sub = sorted(int(x) for x in parts[1 + mp:])
            if (len(set(main)) != mp or not all(mlo <= x <= mhi for x in main)
                    or len(set(sub)) != sp or not all(slo <= x <= shi for x in sub)):
                print(f"[警告] 第{ln}行号码非法，跳过：{line}", file=sys.stderr)
                continue
            rows.append((period, main, sub))
    uniq = {r[0]: r for r in rows}
    rows = sorted(uniq.values(), key=lambda r: int("".join(c for c in r[0] if c.isdigit())))
    return rows


# ---------------------------------------------------------------- 形态工具


def ac_value(nums, offset):
    diffs = {abs(a - b) for a, b in itertools.combinations(nums, 2)}
    return len(diffs) - offset


def odd_even(nums):
    odd = sum(1 for x in nums if x % 2)
    return (odd, len(nums) - odd)


def size_ratio(nums, split):
    small = sum(1 for x in nums if x <= split)
    return (len(nums) - small, small)  # (大, 小)


def zone_ratio(nums, zones):
    return tuple(sum(1 for x in nums if lo <= x <= hi) for lo, hi in zones)


def consecutive_groups(nums):
    s = sorted(nums)
    groups = 0
    i = 0
    while i < len(s) - 1:
        if s[i + 1] - s[i] == 1:
            groups += 1
            while i < len(s) - 1 and s[i + 1] - s[i] == 1:
                i += 1
        else:
            i += 1
    return groups


def same_tail_count(nums):
    c = Counter(x % 10 for x in nums)
    return max(c.values())


def wilson_bounds(count, n, z=1.96):
    """威尔逊置信区间：小样本下比正态近似(μ±σ)更稳健的频率区间估计。"""
    if n == 0:
        return 0.0, 1.0
    phat = count / n
    denom = 1 + z * z / n
    center = phat + z * z / (2 * n)
    half = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n))
    return (center - half) / denom, (center + half) / denom


# ---------------------------------------------------------------- 遗漏统计


def omission_stats(history_sets, lo, hi):
    """返回每个号码的 (当前遗漏O, 最大遗漏O_max, 平均遗漏O_avg, 出现次数)。"""
    n = len(history_sets)
    stats = {}
    for num in range(lo, hi + 1):
        cur, mx, gap, cnt = 0, 0, 0, 0
        for s in history_sets:
            if num in s:
                cnt += 1
                mx = max(mx, gap)
                gap = 0
            else:
                gap += 1
        mx = max(mx, gap)
        cur = gap
        avg = n / cnt if cnt else float(n)
        stats[num] = {"cur": cur, "max": mx, "avg": avg, "count": cnt}
    return stats


# ---------------------------------------------------------------- 14维基础指标


def base_indicators(rows, p, game):
    n = len(rows)
    main_sets = [set(m) for _, m, _ in rows]
    sub_sets = [set(s) for _, _, s in rows]
    mlo, mhi = p["main_range"]
    slo, shi = p["sub_range"]
    mu = p["main_pick"] / (mhi - mlo + 1)
    sigma = math.sqrt(mu * (1 - mu) / n)

    m_om = omission_stats(main_sets, mlo, mhi)
    s_om = omission_stats(sub_sets, slo, shi)

    main_scores = {}
    hot, cold = [], []
    for num in range(mlo, mhi + 1):
        st = m_om[num]
        score = 0.0
        # 1 频率热冷（8）——威尔逊区间判定：下界>μ为热（显著偏高），上界<μ为冷
        wlo, whi = wilson_bounds(st["count"], n)
        if wlo > mu:
            score += 8.0
            hot.append(num)
        elif whi < mu:
            score += 3.0
            cold.append(num)
        else:
            score += 6.0
        # 2 当前遗漏（10）
        o = st["cur"]
        score += 10.0 if o <= 8 else (4.0 if o > 15 else 7.0)
        # 3 最大遗漏回补（6）
        score += 6.0 if st["max"] > 0 and o >= 0.7 * st["max"] else 3.0
        # 4 平均遗漏（6）
        score += 6.0 if o > 1.5 * st["avg"] else 4.0
        main_scores[num] = score

    # 5 重号加分：上期号码 +5
    last_main = main_sets[-1]
    for num in last_main:
        main_scores[num] += 5.0

    # 副区单号得分（第14维：冷热+遗漏）
    sub_scores = {}
    smu = (p["sub_pick"] / (shi - slo + 1))
    ssigma = math.sqrt(smu * (1 - smu) / n)
    sub_hot, sub_cold = [], []
    for num in range(slo, shi + 1):
        st = s_om[num]
        score = 0.0
        # 威尔逊区间判定副区冷热（同主区逻辑）
        wlo, whi = wilson_bounds(st["count"], n)
        if wlo > smu:
            score += 5.0
            sub_hot.append(num)
        elif whi < smu:
            score += 1.5
            sub_cold.append(num)
        else:
            score += 3.5
        o = st["cur"]
        score += 5.0 if o <= 2 * st["avg"] else 2.0
        if st["max"] > 0 and o >= 0.7 * st["max"]:
            score += 2.0  # 回补加成
        sub_scores[num] = score

    # 近期形态统计（指标5-13，作为组合层参考）
    recent = rows[-30:]
    patterns = {
        "重号分布": Counter(len(set(m) & set(rows[i - 1][1]))
                        for i, (_, m, _) in enumerate(rows) if i > 0),
        "和值近10期": [sum(m) for _, m, _ in rows[-10:]],
        "跨度近10期": [max(m) - min(m) for _, m, _ in rows[-10:]],
        "奇偶近10期": [f"{o}:{e}" for o, e in
                    (odd_even(m) for _, m, _ in recent[-10:])],
        "三区近10期": [":".join(map(str, zone_ratio(m, p["zones"])))
                    for _, m, _ in recent[-10:]],
    }

    return {
        "n": n, "mu": mu, "sigma": sigma,
        "main_scores": main_scores, "sub_scores": sub_scores,
        "main_omission": m_om, "sub_omission": s_om,
        "hot": sorted(hot), "cold": sorted(cold),
        "sub_hot": sorted(sub_hot), "sub_cold": sorted(sub_cold),
        "last_main": sorted(last_main), "last_sub": sorted(sub_sets[-1]),
        "patterns": {k: (dict(v) if isinstance(v, Counter) else v)
                     for k, v in patterns.items()},
    }


# ---------------------------------------------------------------- 高级模型


def markov_zone(rows, p):
    """三区形态马尔可夫：状态=三区比元组，输出下期各形态概率。"""
    states = [zone_ratio(m, p["zones"]) for _, m, _ in rows]
    trans = defaultdict(Counter)
    for a, b in zip(states, states[1:]):
        trans[a][b] += 1
    cur = states[-1]
    row = trans.get(cur, Counter())
    total = sum(row.values())
    if not total:
        return {"当前形态": ":".join(map(str, cur)), "预测": {}}
    pred = {":".join(map(str, k)): round(v / total, 4)
            for k, v in row.most_common(5)}
    return {"当前形态": ":".join(map(str, cur)), "预测": pred}


def markov_sub(rows, p, half_life=30):
    """副区号码级马尔可夫（拉普拉斯平滑 + 指数时间衰减：近期转移权重更高）。"""
    slo, shi = p["sub_range"]
    m = shi - slo + 1
    trans = defaultdict(lambda: defaultdict(float))
    subs = [s for _, _, s in rows]
    n_trans = len(subs) - 1
    decay = math.log(2) / half_life  # 半衰期期数
    for i, (a, b) in enumerate(zip(subs, subs[1:])):
        w = math.exp(-decay * (n_trans - 1 - i))  # 越新权重越接近1
        for x in a:
            for y in b:
                trans[x][y] += w
    cur = subs[-1]
    k = 1  # 拉普拉斯 K
    probs = {}
    for num in range(slo, shi + 1):
        psum = 0.0
        for x in cur:
            row = trans[x]
            total = sum(row.values())
            psum += (row[num] + k) / (total + k * m)
        probs[num] = psum / len(cur)
    top = sorted(probs.items(), key=lambda kv: -kv[1])
    return {f"{num:02d}": round(pr, 4) for num, pr in top[:8]}


def bayes_scores(rows, p, alpha=1.0):
    """贝叶斯后验（狄利克雷平滑），返回主区、副区后验字典。"""
    n = len(rows)
    mlo, mhi = p["main_range"]
    slo, shi = p["sub_range"]
    mcnt = Counter(x for _, m, _ in rows for x in m)
    scnt = Counter(x for _, _, s in rows for x in s)
    mm, sm = mhi - mlo + 1, shi - slo + 1
    main = {i: (mcnt[i] + alpha) / (n * p["main_pick"] + alpha * mm)
            for i in range(mlo, mhi + 1)}
    sub = {i: (scnt[i] + alpha) / (n * p["sub_pick"] + alpha * sm)
           for i in range(slo, shi + 1)}
    return main, sub


def omission_signals(base, p):
    """遗漏模型：S_i>1.5 超冷回补；遗漏≥0.7×O_max 强信号。"""
    sig = {"超冷回补": [], "强信号": []}
    for num, st in base["main_omission"].items():
        o, om, oa = st["cur"], st["max"], st["avg"]
        s_i = (o / oa) * (1 - math.exp(-o / om)) if oa > 0 and om > 0 else 0.0
        if s_i > 1.5:
            sig["超冷回补"].append(f"{num:02d}(S={s_i:.2f})")
        if om > 0 and o >= 0.7 * om:
            sig["强信号"].append(f"{num:02d}(O={o}/max{om})")
    return sig


def monte_carlo(base, bayes_main, bayes_sub, p, n_sim, seed=None):
    """蒙特卡洛：按 后验×遗漏强度 加权采样，统计主区/副区出现频率。"""
    rng = random.Random(seed)
    mlo, mhi = p["main_range"]
    slo, shi = p["sub_range"]

    def weight(num, om, post):
        st = om[num]
        o, omax, oavg = st["cur"], st["max"], st["avg"]
        s_i = (o / oavg) * (1 - math.exp(-o / omax)) if oavg > 0 and omax > 0 else 0.0
        return post[num] * (1.0 + min(s_i, 2.0) * 0.3)

    mnums = list(range(mlo, mhi + 1))
    mw = [weight(x, base["main_omission"], bayes_main) for x in mnums]
    snums = list(range(slo, shi + 1))
    sw = [weight(x, base["sub_omission"], bayes_sub) for x in snums]

    mfreq, sfreq = Counter(), Counter()
    for _ in range(n_sim):
        picked = set()
        while len(picked) < p["main_pick"]:
            picked.add(rng.choices(mnums, weights=mw)[0])
        mfreq.update(picked)
        spicked = set()
        while len(spicked) < p["sub_pick"]:
            spicked.add(rng.choices(snums, weights=sw)[0])
        sfreq.update(spicked)
    return mfreq, sfreq


def mc_confidence(freq, n_sim, pick, top_n=10):
    """蒙特卡洛频率的95%威尔逊置信区间：呈现估计不确定性而非裸频次。"""
    out = {}
    for num, cnt in freq.most_common(top_n):
        lo, hi = wilson_bounds(cnt, n_sim)
        out[f"{num:02d}"] = {
            "入选率": round(cnt / n_sim, 4),
            "95%置信区间": [round(lo, 4), round(hi, 4)],
        }
    return out


def calibrate_params(rows, p):
    """数据驱动校准硬过滤阈值：用历史真实开奖分布替代固定阈值。

    原文档固定阈值对真实开奖的通过率仅约7%（DLT），会系统性滤掉开奖形态。
    校准规则：
      和值/跨度 → 取历史5%~95%分位；
      AC → 覆盖≥90%期数的最小连续区间；
      奇偶/大小/三区 → 按频次累计覆盖≥85%的形态集合。
    连号/同尾/重号规则通过率本就高，维持原值。
    返回新参数字典（不修改原 p）。
    """
    mains = [m for _, m, _ in rows]
    q = lambda lst, f: sorted(lst)[min(int(f * len(lst)), len(lst) - 1)]

    sums = [sum(m) for m in mains]
    spans = [max(m) - min(m) for m in mains]

    acs = Counter(ac_value(m, p["ac_offset"]) for m in mains)
    total = len(mains)
    # AC最小连续区间：从众数向两侧扩展直到覆盖90%
    best = acs.most_common(1)[0][0]
    lo = hi = best
    while sum(acs[v] for v in range(lo, hi + 1)) < 0.9 * total:
        left = acs.get(lo - 1, 0)
        right = acs.get(hi + 1, 0)
        if left >= right and left > 0:
            lo -= 1
        elif right > 0:
            hi += 1
        elif left > 0:
            lo -= 1
        else:
            break

    def top_cover(counter, threshold=0.85):
        picked, acc = set(), 0
        for k, v in counter.most_common():
            picked.add(k)
            acc += v
            if acc >= threshold * total:
                break
        return picked

    cal = dict(p)
    cal["sum_range"] = (q(sums, 0.05), q(sums, 0.95))
    cal["span_range"] = (q(spans, 0.05), q(spans, 0.95))
    cal["ac_best"] = (lo, hi)
    cal["odd_even_ok"] = top_cover(Counter(odd_even(m) for m in mains))
    cal["size_ok"] = top_cover(Counter(size_ratio(m, p["size_split"]) for m in mains))
    cal["zone_ok"] = top_cover(Counter(zone_ratio(m, p["zones"]) for m in mains))
    return cal


def filter_pass_rate(rows, p):
    """硬过滤对历史真实开奖的通过率：量化过滤器把开奖号滤掉的风险。

    通过率低说明过滤过严——推荐池系统性排除了大量真实开奖形态。
    """
    passed, detail = 0, Counter()
    checks = [
        ("AC", lambda m: p["ac_best"][0] <= ac_value(m, p["ac_offset"]) <= p["ac_best"][1]),
        ("和值", lambda m: p["sum_range"][0] <= sum(m) <= p["sum_range"][1]),
        ("跨度", lambda m: p["span_range"][0] <= max(m) - min(m) <= p["span_range"][1]),
        ("奇偶", lambda m: odd_even(m) in p["odd_even_ok"]),
        ("大小", lambda m: size_ratio(m, p["size_split"]) in p["size_ok"]),
        ("三区", lambda m: zone_ratio(m, p["zones"]) in p["zone_ok"]),
        ("连号", lambda m: consecutive_groups(m) <= p["consec_max"]),
        ("同尾", lambda m: same_tail_count(m) <= p["same_tail_max"]),
    ]
    for i in range(1, len(rows)):
        m = rows[i][1]
        ok = True
        for name, fn in checks:
            if not fn(m):
                detail[name] += 1
                ok = False
        rep = len(set(m) & set(rows[i - 1][1]))
        if not (p["repeat_ok"][0] <= rep <= p["repeat_ok"][1]):
            detail["重号"] += 1
            ok = False
        if ok:
            passed += 1
    n = len(rows) - 1
    return {
        "历史通过率": round(passed / n, 3),
        "样本期数": n,
        "被滤原因分布": dict(detail.most_common()),
        "提示": ("通过率偏低，说明约{:.0f}%的真实开奖会被硬过滤排除；"
               "推荐组合覆盖的是主流形态，冷门形态需靠逆向/爆冷信号补充"
               ).format((1 - passed / n) * 100),
    }


def sub_special(rows, base, p, game):
    """副区专项：DLT后区连号模型 / SSQ蓝球专项模型。"""
    if game == "DLT":
        subs = [s for _, _, s in rows]
        consec = sum(1 for s in subs if abs(s[0] - s[1]) == 1)
        rate = consec / len(subs)
        diffs = Counter(abs(s[1] - s[0]) for s in subs)
        gap = 0
        for s in reversed(subs):
            if abs(s[0] - s[1]) == 1:
                break
            gap += 1
        return {
            "历史连号占比": round(rate, 3),
            "连号当前遗漏": gap,
            "差值分布Top": dict(diffs.most_common(6)),
            "策略": "连号遗漏偏高，辅推连号" if rate > 0 and gap > 1 / max(rate, 1e-9) * 1.5
                   else "主推跨组差值≥2 + 1热1温",
        }
    # SSQ 蓝球
    blues = [s[0] for _, _, s in rows]
    amps = [abs(a - b) for a, b in zip(blues, blues[1:])]
    amp_c = Counter(amps)
    last = blues[-1]
    neighbor_hits = sum(1 for a, b in zip(blues, blues[1:]) if abs(b - a) <= 2)
    return {
        "上期蓝球": f"{last:02d}",
        "振幅分布Top": dict(amp_c.most_common(6)),
        "邻号(±2)历史命中率": round(neighbor_hits / max(len(amps), 1), 3),
        "近30期高频蓝": [f"{n:02d}" for n, _ in
                     Counter(blues[-30:]).most_common(5)],
    }


def reverse_and_burst(rows, base, p):
    """逆向形态捕捉 + 爆冷结构识别（简版信号输出）。"""
    signals = []
    # 零重号遗漏
    zero_rep_gap = 0
    for i in range(len(rows) - 1, 0, -1):
        if len(set(rows[i][1]) & set(rows[i - 1][1])) == 0:
            break
        zero_rep_gap += 1
    rep_dist = base["patterns"]["重号分布"]
    total = sum(rep_dist.values())
    zero_avg = total / max(rep_dist.get(0, 1), 1)
    if zero_rep_gap >= 1.8 * zero_avg:
        signals.append(f"零重号遗漏{zero_rep_gap}期，达触发线，可留1注零重号组合")
    # 爆冷
    cold_strength = sum(
        (st["cur"] / st["avg"]) for num, st in base["main_omission"].items()
        if st["avg"] > 0 and st["cur"] > 1.5 * st["avg"])
    burst = len(base["cold"]) >= 3 and cold_strength > 4.5
    if burst:
        signals.append(f"爆冷信号：冷号{len(base['cold'])}个，冷强度{cold_strength:.1f}，"
                       "可留1注冷号主导组合")
    return signals if signals else ["无显著逆向/爆冷信号"]


# ---------------------------------------------------------------- 硬过滤与组合


def hard_filter_main(main, last_main, p):
    if not (p["ac_best"][0] <= ac_value(main, p["ac_offset"]) <= p["ac_best"][1]):
        return False
    if not (p["sum_range"][0] <= sum(main) <= p["sum_range"][1]):
        return False
    if not (p["span_range"][0] <= max(main) - min(main) <= p["span_range"][1]):
        return False
    if odd_even(main) not in p["odd_even_ok"]:
        return False
    if size_ratio(main, p["size_split"]) not in p["size_ok"]:
        return False
    if zone_ratio(main, p["zones"]) not in p["zone_ok"]:
        return False
    if consecutive_groups(main) > p["consec_max"]:
        return False
    if same_tail_count(main) > p["same_tail_max"]:
        return False
    rep = len(set(main) & set(last_main))
    if not (p["repeat_ok"][0] <= rep <= p["repeat_ok"][1]):
        return False
    return True


def build_recommendations(base, bayes_main, bayes_sub, mfreq, sfreq, p, game,
                          n_bets, seed=None):
    """融合打分 → Top池 → 组合生成 → 硬过滤 → 排序去重。"""
    rng = random.Random(seed)

    def norm(d):
        vals = list(d.values())
        lo, hi = min(vals), max(vals)
        rng_ = hi - lo if hi > lo else 1.0
        return {k: (v - lo) / rng_ for k, v in d.items()}

    nb = norm(base["main_scores"])
    np_ = norm(bayes_main)
    nm = norm(dict(mfreq)) if mfreq else {k: 0 for k in nb}
    fused_main = {k: 0.73 * nb[k] + 0.27 * (0.6 * np_[k] + 0.4 * nm.get(k, 0))
                  for k in nb}

    sb = norm(base["sub_scores"])
    sp_ = norm(bayes_sub)
    sm = norm(dict(sfreq)) if sfreq else {k: 0 for k in sb}
    fused_sub = {k: 0.73 * sb[k] + 0.27 * (0.6 * sp_[k] + 0.4 * sm.get(k, 0))
                 for k in sb}

    main_pool = [n for n, _ in sorted(fused_main.items(),
                                      key=lambda kv: -kv[1])][:p["main_pool"]]
    sub_pool = [n for n, _ in sorted(fused_sub.items(),
                                     key=lambda kv: -kv[1])][:p["sub_pool"]]

    # 主区组合：池内穷举（池20选5=15504，可穷举）；过大时随机抽样
    all_main = list(itertools.combinations(sorted(main_pool), p["main_pick"]))
    if len(all_main) > 60000:
        all_main = rng.sample(all_main, 60000)
    passed = [c for c in all_main if hard_filter_main(list(c), base["last_main"], p)]

    scored = sorted(passed,
                    key=lambda c: -sum(fused_main[x] for x in c))

    # 副区组合
    if p["sub_pick"] == 2:
        sub_combos = sorted(itertools.combinations(sorted(sub_pool), 2),
                            key=lambda c: -(fused_sub[c[0]] + fused_sub[c[1]]))
        # DLT后区硬过滤：至少1个热/温号（非冷）
        sub_combos = [c for c in sub_combos
                      if not all(x in base["sub_cold"] for x in c)] or sub_combos
    else:
        sub_combos = [(x,) for x in sub_pool]

    recs, used = [], []
    si = 0
    for c in scored:
        if len(recs) >= n_bets:
            break
        # 注间去重：与已选组合主区重叠 ≤ main_pick-2
        if any(len(set(c) & set(u)) > p["main_pick"] - 2 for u in used):
            continue
        sub = sub_combos[si % len(sub_combos)]
        si += 1
        raw = (sum(fused_main[x] for x in c) / p["main_pick"] * 70
               + sum(fused_sub[x] for x in sub) / p["sub_pick"] * 30)
        recs.append({
            "主区": [f"{x:02d}" for x in c],
            "副区": [f"{x:02d}" for x in sub],
            "综合分": round(raw, 1),
            "和值": sum(c), "跨度": max(c) - min(c),
            "AC": ac_value(c, p["ac_offset"]),
            "奇偶": ":".join(map(str, odd_even(c))),
            "大小": ":".join(map(str, size_ratio(c, p["size_split"]))),
            "三区": ":".join(map(str, zone_ratio(c, p["zones"]))),
            "重号": len(set(c) & set(base["last_main"])),
        })
        used.append(c)

    return {
        "主区Top池": [f"{x:02d}" for x in main_pool],
        "副区Top池": [f"{x:02d}" for x in sub_pool],
        "硬过滤通过组合数": len(passed),
        "推荐组合": recs,
        "_fused_main": fused_main,
        "_fused_sub": fused_sub,
    }


def build_multi(fused_main, fused_sub, base, p, m_count, s_count):
    """复式推荐：主区选 m_count 个、副区选 s_count 个。

    选号策略：按融合分排序取Top，但主区强制三区均衡（每区至少1个）、
    奇偶不极端（奇或偶占比不超过75%），保证复式覆盖面。
    """
    mlo, mhi = p["main_range"]
    ranked = [n for n, _ in sorted(fused_main.items(), key=lambda kv: -kv[1])]

    picked = []
    zone_of = {n: next(i for i, (lo, hi) in enumerate(p["zones"]) if lo <= n <= hi)
               for n in range(mlo, mhi + 1)}
    # 先保证每个三区至少1个
    for zi in range(len(p["zones"])):
        for n in ranked:
            if zone_of[n] == zi and n not in picked:
                picked.append(n)
                break
    # 再按分数补足，同时控制奇偶极端
    limit = max(1, int(m_count * 0.75))
    for n in ranked:
        if len(picked) >= m_count:
            break
        if n in picked:
            continue
        odd = sum(1 for x in picked if x % 2) + (n % 2)
        even = len(picked) + 1 - odd
        if odd > limit or even > limit:
            continue
        picked.append(n)
    # 奇偶约束导致不满时放开补齐
    for n in ranked:
        if len(picked) >= m_count:
            break
        if n not in picked:
            picked.append(n)

    sub_ranked = [n for n, _ in sorted(fused_sub.items(), key=lambda kv: -kv[1])]
    sub_picked = sub_ranked[:s_count]

    n_combo = (math.comb(m_count, p["main_pick"])
               * math.comb(s_count, p["sub_pick"]))
    picked.sort()
    sub_picked.sort()
    return {
        "复式结构": f"{m_count}+{s_count}",
        "主区": [f"{x:02d}" for x in picked],
        "副区": [f"{x:02d}" for x in sub_picked],
        "等效注数": n_combo,
        "投注金额(2元/注)": n_combo * 2,
        "主区三区分布": ":".join(map(str, zone_ratio(picked, p["zones"]))),
        "主区奇偶": ":".join(map(str, odd_even(picked))),
        "含上期重号": len(set(picked) & set(base["last_main"])),
    }


def build_multi_groups(fused_main, fused_sub, base, p, m_count, s_count, n_groups):
    """生成 n_groups 组复式。后续组对已用号码施加分数惩罚，保证组间差异化。"""
    groups = []
    m_used, s_used = Counter(), Counter()
    for _ in range(n_groups):
        adj_main = {k: v - 0.35 * m_used[k] for k, v in fused_main.items()}
        adj_sub = {k: v - 0.35 * s_used[k] for k, v in fused_sub.items()}
        g = build_multi(adj_main, adj_sub, base, p, m_count, s_count)
        groups.append(g)
        for x in g["主区"]:
            m_used[int(x)] += 1
        for x in g["副区"]:
            s_used[int(x)] += 1
    return groups


def parse_dantuo(spec, p):
    """解析胆拖参数 '胆*拖'（如 DLT '2*6' 表示主区2胆6拖）。

    约束：胆码数 < main_pick；胆+拖 > main_pick（否则无意义）。
    副区默认全包 sub_pool 前2~3个（DLT）或 Top蓝（SSQ单式副区）。
    """
    m = re.match(r"^(\d+)[*x×](\d+)$", spec.strip())
    if not m:
        raise ValueError(f"胆拖格式应为 胆*拖，如 2*6，收到：{spec}")
    dan, tuo = int(m.group(1)), int(m.group(2))
    if not (1 <= dan < p["main_pick"]):
        raise ValueError(f"胆码数需在 1~{p['main_pick'] - 1} 之间，收到：{dan}")
    if dan + tuo <= p["main_pick"]:
        raise ValueError(f"胆+拖需大于 {p['main_pick']}（否则不构成胆拖），"
                         f"收到：{dan}+{tuo}")
    if dan + tuo > p["main_pool"]:
        raise ValueError(f"胆+拖合计不能超过 {p['main_pool']}，收到：{dan + tuo}")
    return dan, tuo


def build_dantuo(fused_main, fused_sub, base, p, dan_n, tuo_n):
    """胆拖推荐：胆码=评分Top且尽量跨区；拖码=次高分差异化覆盖。

    等效注数 = C(拖, main_pick-胆) × 副区组合数。
    """
    mlo, mhi = p["main_range"]
    ranked = [n for n, _ in sorted(fused_main.items(), key=lambda kv: -kv[1])]
    zone_of = {n: next(i for i, (lo, hi) in enumerate(p["zones"]) if lo <= n <= hi)
               for n in range(mlo, mhi + 1)}

    # 胆码：评分最高，且尽量分布在不同三区（降低整组覆灭风险）
    dan = []
    used_zones = set()
    for n in ranked:
        if len(dan) >= dan_n:
            break
        if zone_of[n] not in used_zones or len(used_zones) >= len(p["zones"]):
            dan.append(n)
            used_zones.add(zone_of[n])
    for n in ranked:  # 区约束不满时补齐
        if len(dan) >= dan_n:
            break
        if n not in dan:
            dan.append(n)

    # 拖码：剩余高分号，保证每个三区至少1个
    remain = [n for n in ranked if n not in dan]
    tuo = []
    for zi in range(len(p["zones"])):
        if any(zone_of[n] == zi for n in dan):
            continue
        for n in remain:
            if zone_of[n] == zi and n not in tuo:
                tuo.append(n)
                break
    for n in remain:
        if len(tuo) >= tuo_n:
            break
        if n not in tuo:
            tuo.append(n)
    tuo = tuo[:tuo_n]

    # 副区：DLT 取Top3全包；SSQ 取Top2蓝
    sub_ranked = [n for n, _ in sorted(fused_sub.items(), key=lambda kv: -kv[1])]
    sub_n = 3 if p["sub_pick"] == 2 else 2
    sub_picked = sorted(sub_ranked[:sub_n])

    need = p["main_pick"] - dan_n
    n_combo = math.comb(tuo_n, need) * math.comb(sub_n, p["sub_pick"])
    dan.sort()
    tuo.sort()
    return {
        "胆拖结构": f"主区{dan_n}胆{tuo_n}拖 + 副区{sub_n}",
        "胆码": [f"{x:02d}" for x in dan],
        "拖码": [f"{x:02d}" for x in tuo],
        "副区": [f"{x:02d}" for x in sub_picked],
        "等效注数": n_combo,
        "投注金额(2元/注)": n_combo * 2,
        "胆码三区分布": ":".join(map(str, zone_ratio(dan, p["zones"]))),
        "胆码含上期重号": len(set(dan) & set(base["last_main"])),
        "说明": f"胆码必出，从拖码中任选{need}个组成一注",
    }


# ---------------------------------------------------------------- 主流程


def parse_multi(spec, p):
    """解析复式参数 '主+副'（如 DLT '8+3'、SSQ '9+2'），校验合法范围。"""
    m = re.match(r"^(\d+)\+(\d+)$", spec.strip())
    if not m:
        raise ValueError(f"复式格式应为 主+副，如 8+3，收到：{spec}")
    mc, sc = int(m.group(1)), int(m.group(2))
    mhi = p["main_range"][1] - p["main_range"][0] + 1
    shi = p["sub_range"][1] - p["sub_range"][0] + 1
    if not (p["main_pick"] < mc <= mhi and mc <= p["main_pool"]):
        raise ValueError(
            f"主区复式个数需在 {p['main_pick'] + 1}~{p['main_pool']} 之间，收到：{mc}")
    if not (p["sub_pick"] <= sc <= min(shi, p["sub_pool"])):
        raise ValueError(
            f"副区复式个数需在 {p['sub_pick']}~{min(shi, p['sub_pool'])} 之间，收到：{sc}")
    if mc == p["main_pick"] and sc == p["sub_pick"]:
        raise ValueError("主副均为单式个数，无需复式，请去掉 --multi")
    return mc, sc


def format_tickets(result, game):
    """生成纯号码文本块（可直接复制投注）。"""
    sep = " + "
    lines = []
    for rec in result.get("推荐组合", []):
        lines.append(" ".join(rec["主区"]) + sep + " ".join(rec["副区"]))
    ticket_text = "\n".join(lines)
    multi_lines = [" ".join(g["主区"]) + sep + " ".join(g["副区"])
                   for g in result.get("复式推荐", [])]
    dt = result.get("胆拖推荐")
    dt_text = ""
    if dt:
        dt_text = ("胆 " + " ".join(dt["胆码"]) + " 拖 " + " ".join(dt["拖码"])
                   + sep + " ".join(dt["副区"]))
    return ticket_text, "\n".join(multi_lines), dt_text


def main():
    ap = argparse.ArgumentParser(description="彩票高级综合分析引擎")
    ap.add_argument("--game", required=True, choices=["DLT", "SSQ"])
    ap.add_argument("--data", required=True, help="历史数据CSV路径")
    ap.add_argument("--bets", type=int, default=None,
                    help="推荐数量：单式模式=注数（默认5）；复式模式=组数（默认1）")
    ap.add_argument("--multi", default=None,
                    help="复式结构 主+副，如 DLT 8+3 / SSQ 9+2。"
                         "指定后只输出复式，不输出单式")
    ap.add_argument("--dantuo", default=None,
                    help="胆拖结构 胆*拖，如 2*6（主区2胆6拖）。"
                         "指定后只输出胆拖，不输出单式")
    ap.add_argument("--mc", type=int, default=100000, help="蒙特卡洛模拟次数")
    ap.add_argument("--seed", type=int, default=None, help="随机种子（可复现）")
    ap.add_argument("--fixed-filter", action="store_true",
                    help="使用文档固定阈值做硬过滤（默认用数据驱动校准阈值，"
                         "对真实开奖历史通过率约85%%，固定阈值仅约7%%）")
    ap.add_argument("--no-ledger", action="store_true",
                    help="跳过账本记录与自动结算（默认开启账本闭环）")
    ap.add_argument("--out", default=None, help="输出JSON路径（默认stdout）")
    args = ap.parse_args()

    p = PARAMS[args.game]
    if args.multi and args.dantuo:
        print("[错误] --multi 与 --dantuo 不能同时指定", file=sys.stderr)
        sys.exit(1)
    multi_spec = dantuo_spec = None
    try:
        if args.multi:
            multi_spec = parse_multi(args.multi, p)
        if args.dantuo:
            dantuo_spec = parse_dantuo(args.dantuo, p)
    except ValueError as exc:
        print(f"[错误] 参数非法：{exc}", file=sys.stderr)
        sys.exit(1)
    # 单式默认5注；复式默认1组；胆拖固定1组
    n_bets = args.bets if args.bets is not None else (1 if multi_spec else 5)

    rows = load_data(args.data, p)
    if len(rows) < 100:
        print(f"[错误] 有效数据仅 {len(rows)} 期，少于最低要求100期", file=sys.stderr)
        sys.exit(1)

    # 硬过滤阈值：默认数据驱动校准；--fixed-filter 回退文档固定值
    fp_orig = filter_pass_rate(rows, p)
    if not args.fixed_filter:
        p = calibrate_params(rows, p)
    fp_used = fp_orig if args.fixed_filter else filter_pass_rate(rows, p)

    base = base_indicators(rows, p, args.game)
    bayes_main, bayes_sub = bayes_scores(rows, p)
    mfreq, sfreq = monte_carlo(base, bayes_main, bayes_sub, p, args.mc, args.seed)

    result = {
        "彩种": args.game,
        "数据范围": f"{rows[0][0]} ~ {rows[-1][0]}（共{len(rows)}期）",
        "上期开奖": {"主区": [f"{x:02d}" for x in base["last_main"]],
                  "副区": [f"{x:02d}" for x in base["last_sub"]]},
        "基础指标": {
            "热号": [f"{x:02d}" for x in base["hot"]],
            "冷号": [f"{x:02d}" for x in base["cold"]],
            "副区热号": [f"{x:02d}" for x in base["sub_hot"]],
            "副区冷号": [f"{x:02d}" for x in base["sub_cold"]],
            "近期形态": base["patterns"],
        },
        "高级模型": {
            "三区马尔可夫": markov_zone(rows, p),
            "副区马尔可夫Top": markov_sub(rows, p),
            "贝叶斯后验Top10": {f"{n:02d}": round(v, 4) for n, v in
                            sorted(bayes_main.items(), key=lambda kv: -kv[1])[:10]},
            "遗漏信号": omission_signals(base, p),
            "蒙特卡洛主区Top10": mc_confidence(mfreq, args.mc, p["main_pick"], 10),
            "蒙特卡洛副区Top5": mc_confidence(sfreq, args.mc, p["sub_pick"], 5),
            "逆向与爆冷": reverse_and_burst(rows, base, p),
            "副区专项": sub_special(rows, base, p, args.game),
        },
        "硬过滤": {
            "模式": "固定阈值" if args.fixed_filter else "数据驱动校准",
            "生效阈值": {
                "和值": list(p["sum_range"]), "跨度": list(p["span_range"]),
                "AC": list(p["ac_best"]),
                "奇偶": sorted(f"{a}:{b}" for a, b in p["odd_even_ok"]),
                "大小": sorted(f"{a}:{b}" for a, b in p["size_ok"]),
                "三区": sorted(":".join(map(str, z)) for z in p["zone_ok"]),
            },
            "对历史开奖通过率": fp_used["历史通过率"],
            "固定阈值通过率(对照)": fp_orig["历史通过率"],
        },
    }
    rec_result = build_recommendations(base, bayes_main, bayes_sub,
                                       mfreq, sfreq, p, args.game,
                                       0 if (multi_spec or dantuo_spec) else n_bets,
                                       args.seed)
    fused_main = rec_result.pop("_fused_main")
    fused_sub = rec_result.pop("_fused_sub")
    if multi_spec:
        # 复式模式：不输出单式推荐，仅保留池信息供报告引用
        rec_result.pop("推荐组合", None)
        result.update(rec_result)
        result["复式推荐"] = build_multi_groups(fused_main, fused_sub, base, p,
                                            multi_spec[0], multi_spec[1], n_bets)
    elif dantuo_spec:
        # 胆拖模式：不输出单式推荐
        rec_result.pop("推荐组合", None)
        result.update(rec_result)
        result["胆拖推荐"] = build_dantuo(fused_main, fused_sub, base, p,
                                      dantuo_spec[0], dantuo_spec[1])
    else:
        result.update(rec_result)

    ticket_text, multi_text, dt_text = format_tickets(result, args.game)
    result["号码文本"] = {}
    if ticket_text:
        result["号码文本"]["单式"] = ticket_text
    if multi_text:
        result["号码文本"]["复式"] = multi_text
    if dt_text:
        result["号码文本"]["胆拖"] = dt_text

    # ---- 账本闭环：先结算历史待开奖记录，再落盘本次推荐 ----
    if not args.no_ledger:
        try:
            import ledger as _ledger
            latest_period = rows[-1][0]
            # 1) 自动结算：pending 记录的目标期已开奖则结算（幂等，settled不重复）
            newly = _ledger.settle_pending(args.game, rows)
            if newly:
                result["上次推荐结算"] = [{
                    "记录ID": r["id"], "模式": r["mode"],
                    "开奖期": r["settle"]["target_period"],
                    "开奖号码": r["settle"]["开奖"],
                    "命中明细": r["settle"]["命中明细"],
                    "固定奖金额": r["settle"]["固定奖金额"],
                    "浮动奖注数": r["settle"]["浮动奖注数"],
                } for r in newly]
            # 2) 落盘本次推荐（同期同内容不重复记录）
            if multi_spec:
                mode, payload = "复式", {"groups": [
                    {"main": [int(x) for x in g["主区"]],
                     "sub": [int(x) for x in g["副区"]]}
                    for g in result["复式推荐"]]}
                cost = sum(g["投注金额(2元/注)"] for g in result["复式推荐"])
            elif dantuo_spec:
                dt = result["胆拖推荐"]
                mode, payload = "胆拖", {
                    "dan": [int(x) for x in dt["胆码"]],
                    "tuo": [int(x) for x in dt["拖码"]],
                    "sub": [int(x) for x in dt["副区"]]}
                cost = dt["投注金额(2元/注)"]
            else:
                mode, payload = "单式", {"tickets": [
                    {"main": [int(x) for x in r["主区"]],
                     "sub": [int(x) for x in r["副区"]]}
                    for r in result["推荐组合"]]}
                cost = len(result["推荐组合"]) * 2
            rid, created = _ledger.record_recommendation(
                args.game, mode, latest_period, payload, cost)
            result["账本"] = {
                "本次记录": rid if created else f"{rid}（同内容已存在，未重复记录）",
                "累计统计": _ledger.stats(args.game),
            }
        except Exception as exc:  # 账本故障不阻断分析主流程
            result["账本"] = {"错误": f"账本读写失败：{exc}"}

    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"[完成] 分析结果已写入 {args.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
