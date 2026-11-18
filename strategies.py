"""12 paper-trading strategies. Each takes a Ctx (bars up to and including the just-closed 1-min bar) and
returns None or a dict(side='long'|'short', stop_pct, tp_pct, reason, [max_hold])."""
import numpy as np
import pandas as pd


class Ctx:
    def __init__(self, df, spy, t, sess_open, sess_close):
        self.df, self.spy, self.t = df, spy, t              # t = START time of the just-closed 1-min bar
        self.open, self.close = sess_open, sess_close
        self.now = t + pd.Timedelta(minutes=1)              # bar close time
        self.m = (self.now - sess_open).total_seconds() / 60    # minutes since the open
        self.left = (sess_close - self.now).total_seconds() / 60  # minutes to the close
        self._cache = {}
        d = df.index.date
        self.day = df[d == t.date()]
        prev = df[d < t.date()]
        self.prev_close = float(prev["close"].iloc[-1]) if len(prev) else None
        self.px = float(df["close"].iloc[-1])
        self.on5 = (self.now.minute % 5 == 0)               # a 5-min bar just completed

    def cached(self, key, fn):
        if key not in self._cache:
            self._cache[key] = fn()
        return self._cache[key]

    @property
    def vwap_series(self):
        def f():
            d = self.day
            tp = (d["high"] + d["low"] + d["close"]) / 3
            return (tp * d["volume"]).cumsum() / d["volume"].cumsum().replace(0, np.nan)
        return self.cached("vwap", f)

    @property
    def vwap(self):
        v = self.vwap_series.dropna()
        return float(v.iloc[-1]) if len(v) else None

    @property
    def m5(self):
        """Completed 5-min bars (today + prior days), only valid right after a 5-min bar closes."""
        def f():
            g = self.df.resample("5min", closed="left", label="left").agg(
                {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
            return g.dropna(subset=["close"])
        return self.cached("m5", f)


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


# 1 ---------------------------------------------------------------- opening-range breakout
def orb(c, p):
    if c.m < p["or_min"] + 1 or c.m > p["last_entry_m"]:
        return None
    rng_bars = c.day[c.day.index < c.open + pd.Timedelta(minutes=p["or_min"])]
    if len(rng_bars) < p["or_min"] * 0.8:
        return None
    hi, lo = float(rng_bars["high"].max()), float(rng_bars["low"].min())
    mid = (hi + lo) / 2
    rng = (hi - lo) / mid
    if not (p["min_rng"] <= rng <= p["max_rng"]):
        return None
    bar = c.day.iloc[-1]
    avg_v = rng_bars["volume"].mean()
    if avg_v and bar["volume"] < p["vol_mult"] * avg_v * 0.5:    # at least some participation on the breakout bar
        return None
    stop = _clamp(rng / 2, p["min_stop"], p["max_stop"])
    px = c.px
    if px > hi * (1 + p["buf"]):
        return dict(side="long", stop_pct=stop, tp_pct=stop * p["rr"],
                    reason=f"ORB up: close {px:.2f} > 15-min high {hi:.2f} (range {rng:.2%})")
    if px < lo * (1 - p["buf"]):
        return dict(side="short", stop_pct=stop, tp_pct=stop * p["rr"],
                    reason=f"ORB down: close {px:.2f} < 15-min low {lo:.2f} (range {rng:.2%})")
    return None


# 2 ---------------------------------------------------------------- VWAP pullback in trend
def vwap_pullback(c, p):
    if c.m < p["min_m"] or c.m > p["last_entry_m"] or len(c.day) < 40:
        return None
    vw = c.vwap_series.dropna()
    if len(vw) < 35:
        return None
    v, v30 = float(vw.iloc[-1]), float(vw.iloc[-31])
    day_ret = c.px / float(c.day["open"].iloc[0]) - 1
    last6 = c.day.iloc[-6:]
    bar = c.day.iloc[-1]
    if day_ret >= p["trend_ret"] and v > v30 and c.px > v and float(last6["low"].min()) <= v * (1 + p["touch"]) \
            and bar["close"] > bar["open"]:
        return dict(side="long", stop_pct=p["stop"], tp_pct=p["tp"],
                    reason=f"uptrend (day {day_ret:+.2%}, VWAP rising) pulled back to VWAP {v:.2f}, green bar")
    if day_ret <= -p["trend_ret"] and v < v30 and c.px < v and float(last6["high"].max()) >= v * (1 - p["touch"]) \
            and bar["close"] < bar["open"]:
        return dict(side="short", stop_pct=p["stop"], tp_pct=p["tp"],
                    reason=f"downtrend (day {day_ret:+.2%}, VWAP falling) bounced to VWAP {v:.2f}, red bar")
    return None


# 3 ---------------------------------------------------------------- 1h momentum + HH/HL + volume
def momentum(c, p):
    if not c.on5 or c.m < p["min_m"] or c.m > p["last_entry_m"] or len(c.day) < 60:
        return None
    w = c.day.iloc[-60:]
    net = float(w["close"].iloc[-1] / w["open"].iloc[0] - 1)
    path = float(w["close"].diff().abs().sum())
    eff = abs(float(w["close"].iloc[-1] - w["open"].iloc[0])) / path if path else 0
    if abs(net) < p["min_net"] or eff < p["min_eff"]:
        return None
    ch = [w.iloc[i * 15:(i + 1) * 15] for i in range(4)]
    hh = sum(ch[i + 1]["high"].max() > ch[i]["high"].max() for i in range(3))
    hl = sum(ch[i + 1]["low"].min() > ch[i]["low"].min() for i in range(3))
    ll = sum(ch[i + 1]["low"].min() < ch[i]["low"].min() for i in range(3))
    lh = sum(ch[i + 1]["high"].max() < ch[i]["high"].max() for i in range(3))
    upv = float(w.loc[w["close"] > w["open"], "volume"].sum())
    dnv = float(w.loc[w["close"] < w["open"], "volume"].sum())
    info = f"1h {net:+.2%}, eff {eff:.0%}"
    if net > 0 and hh >= 2 and hl >= 2 and upv >= p["vol_ratio"] * max(dnv, 1):
        return dict(side="long", stop_pct=p["stop"], tp_pct=p["tp"], max_hold=p["max_hold"],
                    reason=f"{info}, HH {hh}/3 HL {hl}/3, up/down vol {upv / max(dnv, 1):.2f}x")
    if net < 0 and ll >= 2 and lh >= 2 and dnv >= p["vol_ratio"] * max(upv, 1):
        return dict(side="short", stop_pct=p["stop"], tp_pct=p["tp"], max_hold=p["max_hold"],
                    reason=f"{info}, LL {ll}/3 LH {lh}/3, down/up vol {dnv / max(upv, 1):.2f}x")
    return None


# 4 ---------------------------------------------------------------- RSI extreme, revert to VWAP
def rsi_revert(c, p):
    if not c.on5 or c.m < p["min_m"] or c.m > p["last_entry_m"]:
        return None
    v = c.vwap
    m5 = c.m5
    if v is None or len(m5) < 20:
        return None
    r = float(rsi(m5["close"]).iloc[-1])
    dist = (c.px - v) / v
    if r <= p["rsi_lo"] and -p["max_dist"] <= dist <= -p["min_dist"]:
        return dict(side="long", stop_pct=p["stop"], tp_pct=abs(dist) * 0.9, max_hold=p["max_hold"],
                    reason=f"RSI {r:.0f} oversold, {dist:+.2%} vs VWAP {v:.2f}; target back toward VWAP")
    if r >= p["rsi_hi"] and p["min_dist"] <= dist <= p["max_dist"]:
        return dict(side="short", stop_pct=p["stop"], tp_pct=abs(dist) * 0.9, max_hold=p["max_hold"],
                    reason=f"RSI {r:.0f} overbought, {dist:+.2%} vs VWAP {v:.2f}; target back toward VWAP")
    return None


# 5 ---------------------------------------------------------------- 9/21 EMA crossover on 5m
def _ema_state(c, p):
    m5 = c.m5["close"]
    if len(m5) < p["slow"] + 3:
        return None
    f, s = ema(m5, p["fast"]), ema(m5, p["slow"])
    return float(f.iloc[-2]), float(s.iloc[-2]), float(f.iloc[-1]), float(s.iloc[-1])


def ema_cross(c, p):
    if not c.on5 or c.m < p["min_m"] or c.m > p["last_entry_m"]:
        return None
    st = _ema_state(c, p)
    if not st:
        return None
    f0, s0, f1, s1 = st
    sep = abs(f1 - s1) / c.px
    if f0 <= s0 and f1 > s1 and sep >= p["min_sep"]:
        return dict(side="long", stop_pct=p["stop"], tp_pct=p["tp"],
                    reason=f"EMA{p['fast']} crossed above EMA{p['slow']} (5m), sep {sep:.3%}")
    if f0 >= s0 and f1 < s1 and sep >= p["min_sep"]:
        return dict(side="short", stop_pct=p["stop"], tp_pct=p["tp"],
                    reason=f"EMA{p['fast']} crossed below EMA{p['slow']} (5m), sep {sep:.3%}")
    return None


def ema_cross_exit(c, p, pos):
    if not c.on5:
        return None
    st = _ema_state(c, p)
    if not st:
        return None
    f0, s0, f1, s1 = st
    if pos["side"] == "long" and f1 < s1 and f0 >= s0:
        return "EMAs crossed back down"
    if pos["side"] == "short" and f1 > s1 and f0 <= s0:
        return "EMAs crossed back up"
    return None


# 6 ---------------------------------------------------------------- volume-spike breakout
def vol_spike(c, p):
    n = p["lookback"]
    if c.m < p["min_m"] or c.m > p["last_entry_m"] or len(c.day) < n + 1:
        return None
    prev, bar = c.day.iloc[-n - 1:-1], c.day.iloc[-1]
    avg_v = float(prev["volume"].mean())
    if avg_v <= 0 or bar["volume"] < p["vol_mult"] * avg_v:
        return None
    if (bar["high"] - bar["low"]) / bar["close"] < p["min_range"]:
        return None
    mult = bar["volume"] / avg_v
    if bar["close"] > prev["high"].max() and bar["close"] > bar["open"]:
        return dict(side="long", stop_pct=p["stop"], tp_pct=p["tp"], max_hold=p["max_hold"],
                    reason=f"volume {mult:.1f}x avg, close {bar['close']:.2f} > {n}-min high {prev['high'].max():.2f}")
    if bar["close"] < prev["low"].min() and bar["close"] < bar["open"]:
        return dict(side="short", stop_pct=p["stop"], tp_pct=p["tp"], max_hold=p["max_hold"],
                    reason=f"volume {mult:.1f}x avg, close {bar['close']:.2f} < {n}-min low {prev['low'].min():.2f}")
    return None


# 7 ---------------------------------------------------------------- gap and go
def _gap(c):
    if c.prev_close is None or c.day.empty:
        return None
    return float(c.day["open"].iloc[0]) / c.prev_close - 1


def gap_go(c, p):
    g = _gap(c)
    if g is None or abs(g) < p["min_gap"] or c.m < p["or_min"] + 1 or c.m > p["last_entry_m"]:
        return None
    ob = c.day[c.day.index < c.open + pd.Timedelta(minutes=p["or_min"])]
    hi, lo = float(ob["high"].max()), float(ob["low"].min())
    v = c.vwap
    if v is None:
        return None
    if g > 0 and c.px > hi and c.px > v:
        stop = _clamp((c.px - lo) / c.px, p["min_stop"], p["max_stop"])
        return dict(side="long", stop_pct=stop, tp_pct=stop * p["rr"],
                    reason=f"gap up {g:+.2%}, broke 15-min high {hi:.2f}, above VWAP {v:.2f}")
    if g < 0 and c.px < lo and c.px < v:
        stop = _clamp((hi - c.px) / c.px, p["min_stop"], p["max_stop"])
        return dict(side="short", stop_pct=stop, tp_pct=stop * p["rr"],
                    reason=f"gap down {g:+.2%}, broke 15-min low {lo:.2f}, below VWAP {v:.2f}")
    return None


# 8 ---------------------------------------------------------------- gap fade
def gap_fade(c, p):
    g = _gap(c)
    if g is None or abs(g) < p["min_gap"] or c.m < p["first_m"] or c.m > p["last_entry_m"]:
        return None
    o, v = float(c.day["open"].iloc[0]), c.vwap
    if v is None:
        return None
    to_fill = abs(c.px - c.prev_close) / c.px
    tp = _clamp(to_fill * 0.8, p["min_tp"], p["max_tp"])
    if g > 0 and c.px < o * (1 - p["roll"]) and c.px < v and c.px > c.prev_close * 1.001 and to_fill >= p["min_tp"]:
        return dict(side="short", stop_pct=p["stop"], tp_pct=tp, max_hold=p["max_hold"],
                    reason=f"gap up {g:+.2%} failing (below open {o:.2f} & VWAP {v:.2f}), fade toward {c.prev_close:.2f}")
    if g < 0 and c.px > o * (1 + p["roll"]) and c.px > v and c.px < c.prev_close * 0.999 and to_fill >= p["min_tp"]:
        return dict(side="long", stop_pct=p["stop"], tp_pct=tp, max_hold=p["max_hold"],
                    reason=f"gap down {g:+.2%} failing (above open {o:.2f} & VWAP {v:.2f}), fade toward {c.prev_close:.2f}")
    return None


# 9 ---------------------------------------------------------------- Bollinger squeeze breakout
def bb_squeeze(c, p):
    if not c.on5 or c.m < p["min_m"] or c.m > p["last_entry_m"]:
        return None
    m5 = c.m5
    n = p["bb_n"]
    if len(m5) < n + 30:
        return None
    cl = m5["close"]
    mid, sd = cl.rolling(n).mean(), cl.rolling(n).std()
    up, lo = mid + p["bb_k"] * sd, mid - p["bb_k"] * sd
    bw = ((up - lo) / mid).dropna()
    hist = bw.iloc[-p["hist"] - 1:-1]
    if len(hist) < 40:
        return None
    thresh = float(np.percentile(hist, p["pct"]))
    squeezed = float(bw.iloc[-4:-1].min()) <= thresh
    avg_v = float(m5["volume"].iloc[-n - 1:-1].mean())
    bar = m5.iloc[-1]
    if not squeezed or avg_v <= 0 or bar["volume"] < p["vol_mult"] * avg_v:
        return None
    if bar["close"] > up.iloc[-1]:
        return dict(side="long", stop_pct=p["stop"], tp_pct=p["tp"], max_hold=p["max_hold"],
                    reason=f"squeeze (bw<={thresh:.2%}) then 5m close {bar['close']:.2f} above upper band {up.iloc[-1]:.2f}, vol {bar['volume'] / avg_v:.1f}x")
    if bar["close"] < lo.iloc[-1]:
        return dict(side="short", stop_pct=p["stop"], tp_pct=p["tp"], max_hold=p["max_hold"],
                    reason=f"squeeze (bw<={thresh:.2%}) then 5m close {bar['close']:.2f} below lower band {lo.iloc[-1]:.2f}, vol {bar['volume'] / avg_v:.1f}x")
    return None


# 10 --------------------------------------------------------------- relative strength vs SPY
def rel_strength(c, p):
    if not c.on5 or c.m < p["min_m"] or c.m > p["last_entry_m"] or c.spy is None:
        return None
    lb = p["lookback"]
    if len(c.day) < lb + 1:
        return None
    s_now = c.spy[c.spy.index <= c.t]
    s_ago_idx = c.day.index[-lb - 1]
    s_old = c.spy[c.spy.index <= s_ago_idx]
    if len(s_now) == 0 or len(s_old) == 0 or s_now.index[-1] < c.t - pd.Timedelta(minutes=3):
        return None
    spy_ret = float(s_now["close"].iloc[-1] / s_old["close"].iloc[-1] - 1)
    st_ret = float(c.px / c.day["close"].iloc[-lb - 1] - 1)
    rel = st_ret - spy_ret
    v = c.vwap
    if v is None:
        return None
    if rel >= p["min_rel"] and c.px > v and spy_ret > -p["spy_tol"]:
        return dict(side="long", stop_pct=p["stop"], tp_pct=p["tp"], max_hold=p["max_hold"],
                    reason=f"1h stock {st_ret:+.2%} vs SPY {spy_ret:+.2%} (rel {rel:+.2%}), above VWAP {v:.2f}")
    if rel <= -p["min_rel"] and c.px < v and spy_ret < p["spy_tol"]:
        return dict(side="short", stop_pct=p["stop"], tp_pct=p["tp"], max_hold=p["max_hold"],
                    reason=f"1h stock {st_ret:+.2%} vs SPY {spy_ret:+.2%} (rel {rel:+.2%}), below VWAP {v:.2f}")
    return None


# 11 --------------------------------------------------------------- end-of-day momentum
def eod_momentum(c, p):
    if not (p["window_end_left"] <= c.left <= p["window_start_left"]) or len(c.day) < 60:
        return None
    o = float(c.day["open"].iloc[0])
    day = c.px / o - 1
    last = c.px / float(c.day["close"].iloc[-46]) - 1
    v = c.vwap
    hi, lo = float(c.day["high"].max()), float(c.day["low"].min())
    if v is None:
        return None
    if day >= p["min_day"] and last >= p["min_last"] and c.px > v and c.px >= hi * (1 - p["near_extreme"]):
        return dict(side="long", stop_pct=p["stop"], tp_pct=p["tp"],
                    reason=f"EOD momentum up: day {day:+.2%}, last 45m {last:+.2%}, near high {hi:.2f}, above VWAP")
    if day <= -p["min_day"] and last <= -p["min_last"] and c.px < v and c.px <= lo * (1 + p["near_extreme"]):
        return dict(side="short", stop_pct=p["stop"], tp_pct=p["tp"],
                    reason=f"EOD momentum down: day {day:+.2%}, last 45m {last:+.2%}, near low {lo:.2f}, below VWAP")
    return None


# 12 --------------------------------------------------------------- cautious trend + pullback
def trend_pullback(c, p):
    if not c.on5 or c.m < p["min_m"] or c.m > p["last_entry_m"]:
        return None
    m5 = c.m5
    if len(m5) < p["slow"] + 10:
        return None
    cl = m5["close"]
    f, s = ema(cl, p["fast"]), ema(cl, p["slow"])
    bar = m5.iloc[-1]
    f1, s1, s6 = float(f.iloc[-1]), float(s.iloc[-1]), float(s.iloc[-7])
    gap = (f1 - s1) / s1
    if gap >= p["min_gap"] and s1 > s6 and bar["close"] > f1 and bar["close"] > s1 and bar["low"] <= f1 * (1 + p["touch"]) \
            and bar["close"] > bar["open"]:
        return dict(side="long", stop_pct=p["stop"], tp_pct=p["tp"],
                    reason=f"5m uptrend (EMA{p['fast']}>EMA{p['slow']} by {gap:.2%}), dipped to EMA{p['fast']} {f1:.2f}, green close")
    if gap <= -p["min_gap"] and s1 < s6 and bar["close"] < f1 and bar["close"] < s1 and bar["high"] >= f1 * (1 - p["touch"]) \
            and bar["close"] < bar["open"]:
        return dict(side="short", stop_pct=p["stop"], tp_pct=p["tp"],
                    reason=f"5m downtrend (EMA{p['fast']}<EMA{p['slow']} by {gap:.2%}), bounced to EMA{p['fast']} {f1:.2f}, red close")
    return None


STRATEGIES = {"orb": orb, "vwap_pullback": vwap_pullback, "momentum": momentum, "rsi_revert": rsi_revert,
              "ema_cross": ema_cross, "vol_spike": vol_spike, "gap_go": gap_go, "gap_fade": gap_fade,
              "bb_squeeze": bb_squeeze, "rel_strength": rel_strength, "eod_momentum": eod_momentum,
              "trend_pullback": trend_pullback}
EXIT_HOOKS = {"ema_cross": ema_cross_exit}
