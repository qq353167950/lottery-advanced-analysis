#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按当天开奖日生成预算约束推荐，并通过 PushPlus 推送。"""
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
# 大乐透：周一/三/六；双色球：周二/四/日
DRAW_DAYS = {"DLT": {0, 2, 5}, "SSQ": {1, 3, 6}}


def run(cmd: list[str]) -> None:
    print("[run] " + " ".join(cmd))
    p = subprocess.run(cmd, cwd=str(ROOT), text=True, encoding="utf-8", errors="replace", capture_output=True)
    if p.stdout:
        print(p.stdout.rstrip())
    if p.returncode:
        if p.stderr:
            print(p.stderr.rstrip(), file=sys.stderr)
        raise RuntimeError("command failed")


def resolve_games(spec: str) -> list[str]:
    if spec.lower() != "auto":
        games = [x.strip().upper() for x in spec.split(",") if x.strip()]
    else:
        weekday = datetime.now(TZ).weekday()
        games = [g for g, days in DRAW_DAYS.items() if weekday in days]
    for game in games:
        if game not in DRAW_DAYS:
            raise ValueError(f"不支持的彩种：{game}")
    return games


def draw_text(draw: dict) -> str:
    main = " ".join(draw.get("主区", []))
    sub = " ".join(draw.get("副区", []))
    return f"{main} + {sub}" if sub else main


def analyze_one(game: str, mode: str, bets: int, mc: int, multi_spec: str | None = None) -> dict:
    data_path = ROOT / "data" / f"{game.lower()}_history.csv"
    out = Path(tempfile.gettempdir()) / f"daily_{game.lower()}_{mode}.json"
    run([PY, "scripts/fetch_data.py", "--game", game, "--periods", "200", "--out", str(data_path)])
    cmd = [PY, "scripts/analyze.py", "--game", game, "--data", str(data_path), "--mc", str(mc), "--out", str(out), "--no-ledger"]
    if mode == "single":
        cmd += ["--bets", str(bets)]
    elif mode == "multi":
        cmd += ["--multi", multi_spec or ("6+2" if game == "DLT" else "7+1"), "--bets", str(bets)]
    else:
        raise ValueError(f"未知分析模式：{mode}")
    run(cmd)
    return json.loads(out.read_text(encoding="utf-8"))


def single_items(result: dict) -> list[dict]:
    return [{"类型": "单式", "号码": " ".join(r["主区"]) + " + " + " ".join(r["副区"]), "金额": 2, "指标": r} for r in result.get("推荐组合", [])]


def dlt_5_plus_3(result: dict) -> dict:
    rec = result["推荐组合"][0]
    back = result["副区Top池"][:3]
    return {
        "类型": "5+3混合复式",
        "号码": " ".join(rec["主区"]) + " + " + " ".join(back),
        "金额": 6,
        "说明": "前区固定5个，后区3选2，共3注",
    }


def build_plan(game: str, mode: str, budget: int, bets: int, mc: int) -> tuple[list[dict], int, str]:
    if mode == "single":
        result = analyze_one(game, "single", bets or 5, mc)
        items = single_items(result)[:bets or 5]
        return items, len(items) * 2, "正常单式"

    if mode == "multi":
        if game == "DLT":
            r62 = analyze_one(game, "multi", 1, mc, "6+2")
            r5 = analyze_one(game, "single", 1, mc)
            items = [
                {"类型": "6+2复式", "号码": r62["号码文本"]["复式"], "金额": r62["复式推荐"][0]["投注金额(2元/注)"]},
                dlt_5_plus_3(r5),
            ]
            return items, sum(x["金额"] for x in items), "两组复式"
        # SSQ 的最小红球复式 7+1 就是14元，两组无法压到20元。
        r71 = analyze_one(game, "multi", 1, mc, "7+1")
        r3 = analyze_one(game, "single", 3, mc)
        items = [{"类型": "7+1复式", "号码": r71["号码文本"]["复式"], "金额": r71["复式推荐"][0]["投注金额(2元/注)"]}]
        items += single_items(r3)[:3]
        return items, sum(x["金额"] for x in items), "预算安全回退：1组复式+3注单式"

    if mode == "mixed":
        if game == "DLT":
            r62 = analyze_one(game, "multi", 1, mc, "6+2")
            r3 = analyze_one(game, "single", 3, mc)
            items = [{"类型": "6+2复式", "号码": r62["号码文本"]["复式"], "金额": r62["复式推荐"][0]["投注金额(2元/注)"]}]
            items += single_items(r3)[:3]
        else:
            r71 = analyze_one(game, "multi", 1, mc, "7+1")
            r3 = analyze_one(game, "single", 3, mc)
            items = [{"类型": "7+1复式", "号码": r71["号码文本"]["复式"], "金额": r71["复式推荐"][0]["投注金额(2元/注)"]}]
            items += single_items(r3)[:3]
        return items, sum(x["金额"] for x in items), "混合模式"

    raise ValueError(f"未知推荐模式：{mode}")


def render_section(lines: list[str], title: str, items: list[dict], total: int, budget: int, note: str) -> None:
    lines.append(f"<h3>{title}（{note}，{total}/{budget}元）</h3>")
    for item in items:
        lines.append(f"<p><b>{item['类型']}</b>：{item['号码']}<br>金额：{item['金额']} 元{('<br>' + item['说明']) if item.get('说明') else ''}</p>")


def build_html(game: str, budget: int, sections: list[tuple[str, list[dict], int, str]]) -> str:
    title = "大乐透" if game == "DLT" else "双色球"
    lines = [
        "<html><body>",
        f"<h2>{title} 今日推荐</h2>",
        f"<p>北京时间：{datetime.now(TZ):%Y-%m-%d %H:%M}<br>当天开奖彩种：{title}</p>",
    ]
    for section_title, items, total, note in sections:
        render_section(lines, section_title, items, total, budget, note)
    lines.append("<p>彩票为独立随机事件，本推荐仅作历史形态筛选参考。所有预算均为对应模块合计金额。</p>")
    lines.append("</body></html>")
    return "\n".join(lines)

def send_pushplus(token: str, title: str, content: str) -> dict:
    payload = {"token": token, "title": title, "content": content, "template": os.environ.get("PUSHPLUS_TEMPLATE", "html")}
    for key, env in (("topic", "PUSHPLUS_TOPIC"), ("channel", "PUSHPLUS_CHANNEL")):
        if os.environ.get(env):
            payload[key] = os.environ[env]
    req = urllib.request.Request(PUSHPLUS_URL, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def main() -> int:
    ap = argparse.ArgumentParser(description="按当天开奖彩种生成预算约束推荐并推送 PushPlus")
    ap.add_argument("--games", default="auto", help="auto 或 DLT,SSQ")
    ap.add_argument("--mode", choices=["package", "single", "multi", "mixed"], default="package")
    ap.add_argument("--bets", type=int, default=5)
    ap.add_argument("--budget", type=int, default=20)
    ap.add_argument("--mc", type=int, default=100000)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    token = os.environ.get("PUSHPLUS_TOKEN", "").strip()
    if not token and not args.dry_run:
        print("[error] 请在环境变量 PUSHPLUS_TOKEN 或 GitHub Secret 中配置 PushPlus token", file=sys.stderr)
        return 2

    games = resolve_games(args.games)
    if not games:
        print(f"[skip] 北京时间 {datetime.now(TZ):%Y-%m-%d} 无大乐透/双色球开奖，今天不推送")
        return 0

    for game in games:
        print(f"[info] processing {game}")
        if args.mode == "package":
            sections = []
            for section_title, plan_mode, section_bets in [
                ("默认正常5注", "single", args.bets or 5),
                ("2组复式预算", "multi", args.bets),
                ("混合预算", "mixed", args.bets),
            ]:
                items, total, note = build_plan(game, plan_mode, args.budget, section_bets, args.mc)
                if total > args.budget:
                    raise RuntimeError(f"{game} {section_title} 金额 {total} 元超过预算 {args.budget} 元")
                sections.append((section_title, items, total, note))
        else:
            items, total, note = build_plan(game, args.mode, args.budget, args.bets, args.mc)
            if total > args.budget:
                raise RuntimeError(f"{game} 推荐金额 {total} 元超过预算 {args.budget} 元")
            sections = [("推荐", items, total, note)]

        content = build_html(game, args.budget, sections)
        if args.dry_run:
            print(content)
            continue
        response = send_pushplus(token, f"{('大乐透' if game == 'DLT' else '双色球')} 今日推荐", content)
        print(json.dumps(response, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
