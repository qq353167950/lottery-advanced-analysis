#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按当天开奖日生成推荐，并通过 PushPlus 推送。"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
PUSHPLUS_URL = os.environ.get("PUSHPLUS_URL", "https://www.pushplus.plus/send/")
TZ = ZoneInfo(os.environ.get("LOTTERY_TIMEZONE", "Asia/Shanghai"))

# Python weekday(): Monday=0 ... Sunday=6
DRAW_DAYS = {
    "DLT": {0, 2, 5},  # 大乐透：周一、周三、周六
    "SSQ": {1, 3, 6},  # 双色球：周二、周四、周日
}


def run(cmd: list[str]) -> None:
    print("[run] " + " ".join(cmd))
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if proc.stdout:
        print(proc.stdout.rstrip())
    if proc.returncode:
        if proc.stderr:
            print(proc.stderr.rstrip(), file=sys.stderr)
        raise RuntimeError("command failed: " + " ".join(cmd))


def resolve_games(spec: str) -> list[str]:
    if spec.lower() == "auto":
        weekday = datetime.now(TZ).weekday()
        return [game for game, days in DRAW_DAYS.items() if weekday in days]

    games = [item.strip().upper() for item in spec.split(",") if item.strip()]
    for game in games:
        if game not in DRAW_DAYS:
            raise ValueError(f"不支持的彩种：{game}")
    return games


def draw_text(draw: dict) -> str:
    main = " ".join(draw.get("主区", []))
    sub = " ".join(draw.get("副区", []))
    return f"{main} + {sub}" if sub else main


def fetch_data(game: str) -> Path:
    data_path = ROOT / "data" / f"{game.lower()}_history.csv"
    run([
        PY,
        "scripts/fetch_data.py",
        "--game", game,
        "--periods", "200",
        "--out", str(data_path),
    ])
    return data_path


def analyze_one(game: str, data_path: Path, mode: str, bets: int, mc: int, multi_spec: str | None = None) -> dict:
    out = Path(tempfile.gettempdir()) / f"daily_{game.lower()}_{mode}_{multi_spec or bets}.json"
    cmd = [
        PY,
        "scripts/analyze.py",
        "--game", game,
        "--data", str(data_path),
        "--mc", str(mc),
        "--out", str(out),
        "--no-ledger",
    ]
    if mode == "single":
        cmd += ["--bets", str(bets)]
    elif mode == "multi":
        if not multi_spec:
            raise ValueError("multi_spec is required for multi mode")
        cmd += ["--multi", multi_spec, "--bets", str(bets)]
    else:
        raise ValueError(f"未知分析模式：{mode}")
    run(cmd)
    return json.loads(out.read_text(encoding="utf-8"))


def single_items(result: dict, count: int) -> list[dict]:
    items = []
    for rec in result.get("推荐组合", [])[:count]:
        items.append({
            "类型": "单式",
            "号码": draw_text(rec),
            "金额": 2,
        })
    return items


def dlt_5_plus_3(result: dict) -> dict:
    rec = result["推荐组合"][0]
    back = result["副区Top池"][:3]
    return {
        "类型": "5+3复式",
        "号码": " ".join(rec["主区"]) + " + " + " ".join(back),
        "金额": 6,
        "说明": "前区固定5个，后区3选2，共3注",
    }


def ssq_6_plus_2(result: dict) -> dict:
    rec = result["推荐组合"][0]
    blues = result["副区Top池"][:2]
    return {
        "类型": "6+2复式",
        "号码": " ".join(rec["主区"]) + " + " + " ".join(blues),
        "金额": 4,
        "说明": "红球固定6个，蓝球2选1，共2注",
    }


def build_recommendation_package(game: str, single_bets: int, multi_budget: int, mc: int) -> tuple[list[dict], list[dict], int, int, str]:
    data_path = fetch_data(game)

    single_result = analyze_one(game, data_path, "single", single_bets, mc)
    singles = single_items(single_result, single_bets)
    single_total = len(singles) * 2

    if game == "DLT":
        multi_6_2 = analyze_one(game, data_path, "multi", 1, mc, "6+2")
        multi_items = [
            {
                "类型": "6+2复式",
                "号码": multi_6_2["号码文本"]["复式"],
                "金额": multi_6_2["复式推荐"][0]["投注金额(2元/注)"],
            },
            dlt_5_plus_3(single_result),
        ]
        note = "大乐透复式组合：6+2 + 5+3"
    else:
        multi_7_1 = analyze_one(game, data_path, "multi", 1, mc, "7+1")
        multi_items = [
            {
                "类型": "7+1复式",
                "号码": multi_7_1["号码文本"]["复式"],
                "金额": multi_7_1["复式推荐"][0]["投注金额(2元/注)"],
            },
            ssq_6_plus_2(single_result),
        ]
        note = "双色球复式组合：7+1 + 6+2"

    multi_total = sum(item["金额"] for item in multi_items)
    if multi_total > multi_budget:
        raise RuntimeError(f"{game} 复式金额 {multi_total} 元超过限制 {multi_budget} 元")
    return singles, multi_items, single_total, multi_total, note


def render_section(lines: list[str], title: str, items: list[dict], total: int, suffix: str) -> None:
    lines.append(f"<h3>{title}（{total}元{suffix}）</h3>")
    for item in items:
        desc = f"<br>{item['说明']}" if item.get("说明") else ""
        lines.append(f"<p><b>{item['类型']}</b>：{item['号码']}<br>金额：{item['金额']}元{desc}</p>")


def build_html(game: str, singles: list[dict], multi_items: list[dict], single_total: int, multi_total: int, multi_budget: int, note: str) -> str:
    title = "大乐透" if game == "DLT" else "双色球"
    lines = [
        "<html><body>",
        f"<h2>{title} 今日推荐</h2>",
        f"<p>北京时间：{datetime.now(TZ):%Y-%m-%d %H:%M}<br>当天开奖彩种：{title}</p>",
    ]
    render_section(lines, "默认正常5注", singles, single_total, "，不计入复式预算")
    render_section(lines, "2组复式", multi_items, multi_total, f" / 限制{multi_budget}元")
    lines.append(f"<p>{note}</p>")
    lines.append("<p>彩票为独立随机事件，本推荐仅作历史形态筛选参考。</p>")
    lines.append("</body></html>")
    return "\n".join(lines)


def send_pushplus(token: str, title: str, content: str) -> dict:
    payload = {
        "token": token,
        "title": title,
        "content": content,
        "template": os.environ.get("PUSHPLUS_TEMPLATE", "html"),
    }
    for key, env in (("topic", "PUSHPLUS_TOPIC"), ("channel", "PUSHPLUS_CHANNEL")):
        value = os.environ.get(env)
        if value:
            payload[key] = value
    req = urllib.request.Request(
        PUSHPLUS_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}


def main() -> int:
    parser = argparse.ArgumentParser(description="按当天开奖彩种生成推荐并推送 PushPlus")
    parser.add_argument("--games", default="auto", help="auto 或 DLT/SSQ/DLT,SSQ")
    parser.add_argument("--single-bets", type=int, default=5, help="单式注数，默认5")
    parser.add_argument("--multi-budget", type=int, default=20, help="2组复式总预算上限，默认20元")
    parser.add_argument("--mc", type=int, default=100000, help="蒙特卡洛模拟次数")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    token = os.environ.get("PUSHPLUS_TOKEN", "").strip()
    if not token and not args.dry_run:
        print("[error] 请配置 PUSHPLUS_TOKEN（GitHub Secret 或环境变量）", file=sys.stderr)
        return 2

    games = resolve_games(args.games)
    if not games:
        print(f"[skip] 北京时间 {datetime.now(TZ):%Y-%m-%d} 无大乐透/双色球开奖，今天不推送")
        return 0

    for game in games:
        print(f"[info] processing {game}")
        singles, multi_items, single_total, multi_total, note = build_recommendation_package(
            game, args.single_bets, args.multi_budget, args.mc
        )
        content = build_html(game, singles, multi_items, single_total, multi_total, args.multi_budget, note)
        if args.dry_run:
            print(content)
            continue
        title = "大乐透 今日推荐" if game == "DLT" else "双色球 今日推荐"
        response = send_pushplus(token, title, content)
        print(json.dumps(response, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
