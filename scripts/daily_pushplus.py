#!/usr/bin/env python3
# -*- coding: utf-8 -*-
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

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
PUSHPLUS_URL = os.environ.get("PUSHPLUS_URL", "https://www.pushplus.plus/send/")
DEFAULT_GAMES = os.environ.get("GAMES", "DLT,SSQ")
DEFAULT_TEMPLATE = os.environ.get("PUSHPLUS_TEMPLATE", "html")


def run(cmd: list[str]) -> None:
    print("[run] " + " ".join(cmd))
    p = subprocess.run(cmd, cwd=str(ROOT), text=True, encoding="utf-8", errors="replace", capture_output=True)
    if p.stdout:
        print(p.stdout.rstrip())
    if p.returncode:
        if p.stderr:
            print(p.stderr.rstrip(), file=sys.stderr)
        raise RuntimeError("command failed")


def games_list(text: str) -> list[str]:
    games = [x.strip().upper() for x in text.split(",") if x.strip()]
    for g in games:
        if g not in {"DLT", "SSQ"}:
            raise ValueError(f"unsupported game: {g}")
    if not games:
        raise ValueError("no games specified")
    return games


def draw_text(draw: dict) -> str:
    main = " ".join(draw.get("主区", []))
    sub = " ".join(draw.get("副区", []))
    return f"{main} + {sub}" if sub else main


def analyze_one(game: str, mode: str, bets: int, mc: int, multi_spec: str | None, dantuo_spec: str | None) -> dict:
    data_path = ROOT / "data" / f"{game.lower()}_history.csv"
    out = Path(tempfile.gettempdir()) / f"daily_{game.lower()}_result.json"
    run([PY, "scripts/fetch_data.py", "--game", game, "--periods", "200", "--out", str(data_path)])
    cmd = [PY, "scripts/analyze.py", "--game", game, "--data", str(data_path), "--mc", str(mc), "--out", str(out)]
    if mode == "single":
        cmd += ["--bets", str(bets)]
    elif mode == "multi":
        cmd += ["--multi", multi_spec or ("8+3" if game == "DLT" else "9+2"), "--bets", str(bets)]
    else:
        cmd += ["--dantuo", dantuo_spec or "2*6"]
    run(cmd)
    return json.loads(out.read_text(encoding="utf-8"))


def pick_summary(game: str, result: dict) -> tuple[str, list[str]]:
    if result.get("推荐组合"):
        rec = result["推荐组合"][0]
        return draw_text(rec), [f"综合分 {rec.get('综合分')}", f"和值 {rec.get('和值')}", f"跨度 {rec.get('跨度')}", f"AC {rec.get('AC')}"]
    if result.get("复式推荐"):
        rec = result["复式推荐"][0]
        return draw_text(rec), [f"结构 {rec.get('复式结构')}", f"等效注数 {rec.get('等效注数')}", f"金额 {rec.get('投注金额(2元/注)')} 元"]
    if result.get("胆拖推荐"):
        rec = result["胆拖推荐"]
        return f"胆 {' '.join(rec.get('胆码', []))} 拖 {' '.join(rec.get('拖码', []))} + {' '.join(rec.get('副区', []))}", [f"等效注数 {rec.get('等效注数')}", f"金额 {rec.get('投注金额(2元/注)')} 元"]
    return "", []


def html_report(items: list[tuple[str, dict]]) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    body = ["<html><body><h2>彩票每日推荐</h2>", f"<p>生成时间：{now}</p>"]
    for game, result in items:
        name = "大乐透" if game == "DLT" else "双色球"
        ticket, bits = pick_summary(game, result)
        hard = result.get("硬过滤", {})
        body.append(f"<h3>{name}</h3>")
        body.append(
            "<p>"
            f"数据范围：{result.get('数据范围', 'N/A')}<br>"
            f"上期开奖：{draw_text(result.get('上期开奖', {}))}<br>"
            f"推荐：<b>{ticket}</b><br>"
            f"{'；'.join(bits)}<br>"
            f"硬过滤通过率：{hard.get('对历史开奖通过率', 'N/A')}；"
            f"固定阈值对照：{hard.get('固定阈值通过率(对照)', 'N/A')}"
            "</p>"
        )
    body.append("</body></html>")
    return "\n".join(body)


def pushplus(token: str, title: str, content: str) -> dict:
    payload = {"token": token, "title": title, "content": content, "template": DEFAULT_TEMPLATE}
    for key, env in [("topic", "PUSHPLUS_TOPIC"), ("channel", "PUSHPLUS_CHANNEL")]:
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
    ap = argparse.ArgumentParser(description="Daily lottery recommendation via PushPlus")
    ap.add_argument("--games", default=DEFAULT_GAMES)
    ap.add_argument("--mode", choices=["single", "multi", "dantuo"], default="single")
    ap.add_argument("--bets", type=int, default=1)
    ap.add_argument("--mc", type=int, default=100000)
    ap.add_argument("--multi-spec", default=None)
    ap.add_argument("--dantuo-spec", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    token = os.environ.get("PUSHPLUS_TOKEN", "").strip()
    if not token and not args.dry_run:
        print("[error] PUSHPLUS_TOKEN is required unless --dry-run is set", file=sys.stderr)
        return 2

    items: list[tuple[str, dict]] = []
    for game in games_list(args.games):
        print(f"[info] processing {game}")
        try:
            items.append((game, analyze_one(game, args.mode, args.bets, args.mc, args.multi_spec, args.dantuo_spec)))
        except Exception as exc:
            print(f"[error] {game}: {exc}", file=sys.stderr)
            items.append((game, {"数据范围": "失败", "上期开奖": {}, "错误": str(exc)}))

    content = html_report(items)
    if args.dry_run:
        print(content)
        return 0

    resp = pushplus(token, "彩票每日推荐", content)
    print(json.dumps(resp, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
