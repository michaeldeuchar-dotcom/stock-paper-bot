#!/usr/bin/env python3
"""Per-bot results for the paper stock fleet: trades, wins, losses, win rate, net fake P&L, today vs since start.
  python3 summarize.py                 # reads ./data (copy it from the `data` branch)
  python3 summarize.py --bot orb_aapl  # one bot, with its recent trades
  python3 summarize.py --json          # machine-readable
"""
import argparse
import csv
import glob
import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

PT = ZoneInfo("America/Los_Angeles")
ROOT = os.path.dirname(os.path.abspath(__file__))
START_CASH = 10_000.0


def money(x):
    return ("-" if x < 0 else "+") + f"${abs(x):,.2f}"


def stats(rows):
    pnl = [float(r["pnl_usd"]) for r in rows]
    wins = sum(1 for p in pnl if p > 0)
    n = len(rows)
    return dict(trades=n, wins=wins, losses=n - wins, win_rate=(wins / n if n else None), pnl=round(sum(pnl), 2),
                avg_win=round(sum(p for p in pnl if p > 0) / wins, 2) if wins else 0.0,
                avg_loss=round(sum(p for p in pnl if p <= 0) / (n - wins), 2) if n - wins else 0.0)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=os.path.join(ROOT, "data"))
    ap.add_argument("--bot")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-open", action="store_true", help="hide open-position details")
    a = ap.parse_args(argv)
    d = a.dir
    for cand in (d, os.path.join(d, "week1")):
        if os.path.exists(os.path.join(cand, "state.json")) or glob.glob(os.path.join(cand, "trades_*.csv")):
            d = cand
            break
    try:
        state = json.load(open(os.path.join(d, "state.json")))["bots"]
    except (OSError, ValueError, KeyError):
        state = {}
    names = sorted(set(state) | {os.path.basename(f)[7:-4] for f in glob.glob(os.path.join(d, "trades_*.csv"))})
    if a.bot:
        names = [n for n in names if n == a.bot]
    today = datetime.now(PT).date().isoformat()
    out, tot_all, tot_today = [], 0.0, 0.0
    for n in names:
        rows = []
        p = os.path.join(d, f"trades_{n}.csv")
        if os.path.exists(p):
            rows = list(csv.DictReader(open(p)))
        all_s = stats(rows)
        td_s = stats([r for r in rows if r["exit_ts"][:10] == today])
        st = state.get(n, {})
        out.append(dict(bot=n, symbol=st.get("symbol") or (rows[0]["symbol"] if rows else ""),
                        strategy=st.get("strategy", ""), since_start=all_s, today=td_s,
                        equity=round(START_CASH + all_s["pnl"], 2), open_pos=st.get("pos"), rows=rows))
        tot_all += all_s["pnl"]
        tot_today += td_s["pnl"]
    if a.json:
        for o in out:
            o.pop("rows")
        print(json.dumps(dict(today=today, total_pnl_since_start=round(tot_all, 2), total_pnl_today=round(tot_today, 2),
                              bots=out), indent=1, default=str))
        return
    print(f"PAPER STOCK FLEET (fake money, $10,000 per bot) - {datetime.now(PT):%a %b %d %H:%M} PT   data dir: {d}")
    print(f"{'bot':<22}{'sym':<5}| {'since start':^41} | {'today (' + today[5:] + ')':^28} | open")
    print(f"{'':<27}| {'trades':>6} {'W':>3} {'L':>3} {'win%':>5} {'net P&L':>11} {'avg W/L':>13} | {'trd':>3} {'W':>2} {'L':>2} {'win%':>5} {'P&L':>10} |")
    for o in out:
        a_, t_ = o["since_start"], o["today"]
        wr = lambda s: f"{s['win_rate']:.0%}" if s["win_rate"] is not None else "-"
        pos = o["open_pos"]
        opn = f"{pos['side']} {pos['shares']} @ {pos['entry_px']:.2f}" if pos else ""
        print(f"{o['bot']:<22}{o['symbol']:<5}| {a_['trades']:>6} {a_['wins']:>3} {a_['losses']:>3} {wr(a_):>5} {money(a_['pnl']):>11} "
              f"{a_['avg_win']:>6.2f}/{a_['avg_loss']:<6.2f} | {t_['trades']:>3} {t_['wins']:>2} {t_['losses']:>2} {wr(t_):>5} {money(t_['pnl']):>10} | {opn}")
    n_tr = sum(o["since_start"]["trades"] for o in out)
    n_w = sum(o["since_start"]["wins"] for o in out)
    print(f"{'FLEET TOTAL':<27}| {n_tr:>6} {n_w:>3} {n_tr - n_w:>3} {(f'{n_w / n_tr:.0%}' if n_tr else '-'):>5} {money(tot_all):>11} "
          f"{'':>13} | {sum(o['today']['trades'] for o in out):>3} {'':>2} {'':>2} {'':>5} {money(tot_today):>10} |")
    if a.bot and out:
        o = out[0]
        print(f"\nlast trades for {a.bot}:")
        for r in o["rows"][-10:]:
            print(f"  {r['entry_ts'][5:16]} {r['side']:<5} {r['shares']}@{r['entry_px']} -> {r['exit_px']} {r['exit_reason']:<14} "
                  f"{money(float(r['pnl_usd'])):>9} | {r['entry_reason']}")
    elif not a.no_open:
        for o in out:
            if o["open_pos"]:
                print(f"OPEN {o['bot']}: {o['open_pos']['side']} since {o['open_pos']['entry_ts'][11:16]} - {o['open_pos']['reason']}")


if __name__ == "__main__":
    main()
