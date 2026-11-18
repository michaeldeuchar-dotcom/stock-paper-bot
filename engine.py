"""Paper account engine. Bar-driven: the same code path runs live and in replay.
Fills are simulated against 1-minute bars (entry at the just-closed bar's close, stops/targets checked on each
later bar's high/low, stop assumed to hit first if both are touched). Slippage 0.02%/side, commission $0.
There is no broker connection of any kind."""
import csv
import json
import math
import os

import pandas as pd

import config as C
from market_cal import PT, session
from strategies import STRATEGIES, EXIT_HOOKS, Ctx

TRADE_COLS = ["bot", "symbol", "strategy", "side", "shares", "date", "entry_ts", "entry_px", "exit_ts", "exit_px",
              "pnl_usd", "pnl_pct", "won", "exit_reason", "entry_reason", "hold_min", "equity_after"]


def new_bot(v):
    return dict(name=v["name"], symbol=v["symbol"], strategy=v["strategy"], realized=0.0, pos=None, last_ts=None,
                day=None, trades_today=0, pnl_today=0.0, last_loss_ts=None, n_signals=0)


def _slip(px, side, opening):
    """Adverse slippage: buying (open long / close short) pays more, selling receives less."""
    buy = (side == "long") == opening
    return px * (1 + C.SLIPPAGE) if buy else px * (1 - C.SLIPPAGE)


class Trader:
    def __init__(self, variant, state, out_dir, log=print):
        self.v, self.s, self.out, self.log = variant, state, out_dir, log
        self.p = variant["params"]
        self.fn = STRATEGIES[variant["strategy"]]
        self.hook = EXIT_HOOKS.get(variant["strategy"])

    # ------------------------------------------------------------------ trade log
    def _write_trade(self, row):
        path = os.path.join(self.out, f"trades_{self.v['name']}.csv")
        new = not os.path.exists(path)
        with open(path, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=TRADE_COLS)
            if new:
                w.writeheader()
            w.writerow(row)

    def _close(self, ts, raw_px, reason):
        s, pos = self.s, self.s["pos"]
        px = _slip(raw_px, pos["side"], opening=False)
        sign = 1 if pos["side"] == "long" else -1
        pnl = sign * (px - pos["entry_px"]) * pos["shares"] - C.COMMISSION
        s["realized"] += pnl
        s["pnl_today"] += pnl
        if pnl < 0:
            s["last_loss_ts"] = ts.isoformat()
        hold = (ts - pd.Timestamp(pos["entry_ts"])).total_seconds() / 60
        row = dict(bot=s["name"], symbol=s["symbol"], strategy=s["strategy"], side=pos["side"], shares=pos["shares"],
                   date=pd.Timestamp(pos["entry_ts"]).date().isoformat(), entry_ts=pos["entry_ts"][:19],
                   entry_px=round(pos["entry_px"], 4), exit_ts=ts.isoformat()[:19], exit_px=round(px, 4),
                   pnl_usd=round(pnl, 2), pnl_pct=round(pnl / (pos["entry_px"] * pos["shares"]) * 100, 3),
                   won=int(pnl > 0), exit_reason=reason, entry_reason=pos["reason"], hold_min=round(hold, 1),
                   equity_after=round(C.START_CASH + s["realized"], 2))
        self._write_trade(row)
        self.log(f"EXIT  {s['name']} {pos['side']} {pos['shares']} {s['symbol']} @ {px:.2f} ({reason}) pnl ${pnl:+.2f}")
        s["pos"] = None

    def _open(self, t_close, ctx, sig):
        s = self.s
        side = sig["side"]
        px = _slip(ctx.px, side, opening=True)
        shares = int(C.POSITION_USD // px)
        if shares < 1:
            return
        sgn = 1 if side == "long" else -1
        s["pos"] = dict(side=side, shares=shares, entry_px=px, entry_ts=t_close.isoformat(),
                        stop_px=px * (1 - sgn * sig["stop_pct"]), tp_px=px * (1 + sgn * sig["tp_pct"]),
                        stop_pct=sig["stop_pct"], tp_pct=sig["tp_pct"], reason=sig["reason"],
                        hold_until=(t_close + pd.Timedelta(minutes=sig["max_hold"])).isoformat() if sig.get("max_hold") else None)
        s["trades_today"] += 1
        self.log(f"ENTER {s['name']} {side} {shares} {s['symbol']} @ {px:.2f} stop {s['pos']['stop_px']:.2f} "
                 f"tp {s['pos']['tp_px']:.2f} | {sig['reason']}")

    # ------------------------------------------------------------------ one completed 1-min bar
    def on_bar(self, df, spy, i, now):
        """df: this symbol's 1-min bars; i: integer position of the just-completed bar; now: wall/sim clock."""
        s = self.s
        t = df.index[i]
        sess = session(t.date())
        if sess is None:
            return
        o, cl = sess
        if t < o or t >= cl:
            return
        t_close = t + pd.Timedelta(minutes=1)
        d = t.date().isoformat()
        if s["day"] != d:
            s.update(day=d, trades_today=0, pnl_today=0.0, last_loss_ts=None)
        bar = df.iloc[i]
        pos = s["pos"]
        flat_at = cl - pd.Timedelta(minutes=C.FLAT_BEFORE_CLOSE_MIN)
        ctx = None
        if pos is not None and pd.Timestamp(pos["entry_ts"]) < t_close:
            if pd.Timestamp(pos["entry_ts"]) <= t:       # exits only on bars after the entry bar
                sgn = 1 if pos["side"] == "long" else -1
                stop, tp = pos["stop_px"], pos["tp_px"]
                hit = None
                if sgn == 1:
                    if bar["open"] <= stop: hit = (bar["open"], "stop (gap)")
                    elif bar["open"] >= tp: hit = (bar["open"], "target (gap)")
                    elif bar["low"] <= stop: hit = (stop, "stop")
                    elif bar["high"] >= tp: hit = (tp, "target")
                else:
                    if bar["open"] >= stop: hit = (bar["open"], "stop (gap)")
                    elif bar["open"] <= tp: hit = (bar["open"], "target (gap)")
                    elif bar["high"] >= stop: hit = (stop, "stop")
                    elif bar["low"] <= tp: hit = (tp, "target")
                if hit:
                    self._close(t_close, hit[0], hit[1])
                elif t_close >= flat_at:
                    self._close(t_close, bar["close"], "flat by close")
                elif pos["hold_until"] and t_close >= pd.Timestamp(pos["hold_until"]):
                    self._close(t_close, bar["close"], "time stop")
                elif self.hook:
                    ctx = Ctx(df.iloc[max(0, i - 1500):i + 1], spy, t, o, cl)
                    why = self.hook(ctx, self.p, pos)
                    if why:
                        self._close(t_close, bar["close"], why)
        # ---- entry
        if s["pos"] is not None:
            return
        if (now - t_close).total_seconds() > C.MAX_ENTRY_STALENESS_SEC:
            return        # catching up on old bars: manage exits only, never open on stale data
        if s["trades_today"] >= self.v["max_trades"] or s["pnl_today"] <= -0.01 * C.START_CASH:
            return
        if (cl - t_close).total_seconds() / 60 < C.NO_ENTRY_LAST_MIN:
            return
        if s["last_loss_ts"] and (t_close - pd.Timestamp(s["last_loss_ts"])).total_seconds() / 60 < self.v["cooldown_min"]:
            return
        ctx = ctx or Ctx(df.iloc[max(0, i - 1500):i + 1], spy, t, o, cl)
        sig = self.fn(ctx, self.p)
        if sig:
            s["n_signals"] += 1
            self._open(t_close, ctx, sig)

    def process(self, df, spy, now, start_after=None):
        """Handle every completed bar newer than last_ts. Returns the number of bars processed."""
        s = self.s
        done = pd.Timestamp(s["last_ts"]) if s["last_ts"] else None
        if done is None and start_after is not None:
            done = start_after
        idx = df.index
        n = 0
        start = int(idx.searchsorted(done, side="right")) if done is not None else 0
        for i in range(start, len(df)):
            t = idx[i]
            if t + pd.Timedelta(minutes=1) > now:      # still forming
                break
            self.on_bar(df, spy, i, now)
            s["last_ts"] = t.isoformat()
            n += 1
        return n

    def force_flat(self, ts, px, reason):
        if self.s["pos"] is not None:
            self._close(ts, px, reason)


def load_state(path):
    try:
        with open(path) as f:
            st = json.load(f)
    except (OSError, ValueError):
        st = {}
    st.setdefault("bots", {})
    for v in C.VARIANTS:
        st["bots"].setdefault(v["name"], new_bot(v))
    return st


def save_state(path, st):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=1)
    os.replace(tmp, path)
