"""Free, keyless market data: Yahoo Finance chart endpoint (1-minute bars). No account, no API key."""
import threading
import time

import pandas as pd
import requests

from market_cal import PT

# Yahoo throttles some User-Agent strings (429) but not a bare "Mozilla/5.0"; fall back through alternatives.
UAS = ("Mozilla/5.0", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15")
HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
_local = threading.local()


class RateLimited(Exception):
    pass


def _sess():
    s = getattr(_local, "s", None)
    if s is None:
        s = requests.Session()
        s.headers.update({"Accept": "application/json"})
        _local.s = s
    return s


def fetch_bars(symbol: str, rng: str = "5d", interval: str = "1m") -> pd.DataFrame:
    """1-minute OHLCV bars (regular hours only), index = bar START time in Pacific time."""
    last_err = None
    for host, ua in zip(HOSTS + (HOSTS[0],), UAS):
        try:
            r = _sess().get(f"https://{host}/v8/finance/chart/{symbol}",
                            params={"range": rng, "interval": interval, "includePrePost": "false"},
                            headers={"User-Agent": ua}, timeout=(4, 10))
            if r.status_code == 429:
                raise RateLimited(f"429 from {host}")
            r.raise_for_status()
            res = r.json()["chart"]["result"][0]
            ts = res.get("timestamp") or []
            q = res["indicators"]["quote"][0]
            df = pd.DataFrame({"open": q["open"], "high": q["high"], "low": q["low"],
                               "close": q["close"], "volume": q["volume"]},
                              index=pd.to_datetime(ts, unit="s", utc=True).tz_convert(PT))
            df = df.dropna(subset=["open", "high", "low", "close"])
            df["volume"] = df["volume"].fillna(0)
            return df[~df.index.duplicated(keep="last")].sort_index()
        except RateLimited as e:
            last_err = e
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise last_err


def fetch_many(symbols, rng="5d", spacing=0.6, budget=45.0):
    """Sequential, polite fetch (Yahoo returns 429 to bursts). Returns ({sym: df}, {sym: error string}).
    Backs off and retries on 429 but gives up on the whole cycle after `budget` seconds."""
    ok, err = {}, {}
    t0 = time.time()
    for s in symbols:
        for attempt in range(3):
            if time.time() - t0 > budget:
                err[s] = "cycle budget exhausted"
                break
            try:
                ok[s] = fetch_bars(s, rng)
                break
            except RateLimited as e:
                err[s] = f"RateLimited: {e}"
                time.sleep(4 * (3 ** attempt))
            except Exception as e:  # noqa: BLE001
                err[s] = f"{type(e).__name__}: {str(e)[:120]}"
                break
        else:
            continue
        if s in ok:
            err.pop(s, None)
        time.sleep(spacing)
    return ok, err


def merge(old: pd.DataFrame | None, new: pd.DataFrame) -> pd.DataFrame:
    if old is None or old.empty:
        return new
    df = pd.concat([old[old.index < new.index[0]], new]) if len(new) else old
    return df[~df.index.duplicated(keep="last")].sort_index().iloc[-2600:]
