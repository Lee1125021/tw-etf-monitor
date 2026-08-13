from __future__ import annotations

import argparse
import json
import math
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from dateutil.relativedelta import relativedelta

TZ = ZoneInfo("Asia/Taipei")
ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "public"
DATA = ROOT / "data"
CONFIG = ROOT / "config" / "corporate_actions.json"
SYMBOLS = ("0050", "0052")
TWSE_MONTH = "https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY"
TWSE_ALL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}.TW"
HEADERS = {"User-Agent": "Mozilla/5.0 tw-etf-monitor/1.0"}


class ValidationError(RuntimeError):
    pass


@dataclass
class Result:
    symbol: str
    as_of: date
    close: float
    volume_shares: int
    ma60: float
    ma60_start: date
    ma60_end: date
    high52: float
    high52_date: date
    drawdown_pct: float
    thresholds: dict[str, float]
    daily_return_pct: float | None
    triggers: dict[str, bool]
    yahoo_close: float | None
    validation: dict[str, Any]


def get_json(url: str, params: dict[str, Any] | None = None, retries: int = 3) -> Any:
    last: Exception | None = None
    for i in range(retries):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=25)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # pragma: no cover - network retry
            last = e
            time.sleep(1.5 * (i + 1))
    raise ValidationError(f"HTTP failure: {url}: {last}")


def parse_twse_date(s: str) -> date:
    # TWSE STOCK_DAY typically returns ROC dates such as 115/08/13.
    y, m, d = [int(x) for x in s.strip().split("/")]
    if y < 1911:
        y += 1911
    return date(y, m, d)


def num(s: Any) -> float:
    if s is None:
        return math.nan
    t = str(s).replace(",", "").strip()
    if t in {"", "--", "---", "X0.00"}:
        return math.nan
    t = t.replace("X", "")
    return float(t)


def fetch_twse_month(symbol: str, month: date) -> pd.DataFrame:
    payload = get_json(
        TWSE_MONTH,
        params={"date": month.strftime("%Y%m01"), "stockNo": symbol, "response": "json"},
    )
    if payload.get("stat") != "OK":
        raise ValidationError(f"TWSE STOCK_DAY {symbol} {month:%Y-%m}: {payload.get('stat')}")
    fields = payload.get("fields", [])
    data = payload.get("data", [])
    if not data:
        return pd.DataFrame()
    wanted = {
        "日期": "date",
        "成交股數": "volume",
        "開盤價": "open",
        "最高價": "high",
        "最低價": "low",
        "收盤價": "close",
    }
    idx: dict[str, int] = {}
    for zh, out in wanted.items():
        if zh not in fields:
            raise ValidationError(f"Missing TWSE field {zh} for {symbol}")
        idx[out] = fields.index(zh)
    rows = []
    for r in data:
        rows.append(
            {
                "date": parse_twse_date(r[idx["date"]]),
                "volume": int(num(r[idx["volume"]])),
                "open": num(r[idx["open"]]),
                "high": num(r[idx["high"]]),
                "low": num(r[idx["low"]]),
                "close": num(r[idx["close"]]),
            }
        )
    return pd.DataFrame(rows)


def fetch_history(symbol: str, end: date, months: int = 15) -> pd.DataFrame:
    start_month = (end.replace(day=1) - relativedelta(months=months - 1))
    frames = []
    m = start_month
    while m <= end.replace(day=1):
        f = fetch_twse_month(symbol, m)
        if not f.empty:
            frames.append(f)
        m += relativedelta(months=1)
    if not frames:
        raise ValidationError(f"No TWSE history for {symbol}")
    df = pd.concat(frames, ignore_index=True).drop_duplicates("date").sort_values("date")
    df = df[df["date"] <= end].reset_index(drop=True)
    if len(df) < 60:
        raise ValidationError(f"Insufficient history for {symbol}: only {len(df)} rows")
    return df


def load_actions() -> dict[str, list[dict[str, Any]]]:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def adjusted_close(df: pd.DataFrame, symbol: str, actions: dict[str, list[dict[str, Any]]]) -> pd.Series:
    out = df["close"].astype(float).copy()
    for action in sorted(actions.get(symbol, []), key=lambda x: x["effective_date"]):
        if action.get("type") != "split":
            raise ValidationError(f"Unsupported corporate action for {symbol}: {action}")
        eff = date.fromisoformat(action["effective_date"])
        ratio = float(action["ratio"])
        if ratio <= 0:
            raise ValidationError(f"Invalid split ratio for {symbol}: {ratio}")
        out.loc[df["date"] < eff] = out.loc[df["date"] < eff] / ratio
    return out


def fetch_twse_all_today(symbol: str) -> dict[str, Any] | None:
    rows = get_json(TWSE_ALL)
    for r in rows:
        if str(r.get("Code", "")) == symbol:
            return r
    return None


def fetch_yahoo_daily(symbol: str, start: date, end: date) -> pd.DataFrame:
    p1 = int(datetime.combine(start, datetime.min.time(), tzinfo=TZ).timestamp())
    # Yahoo period2 is exclusive; add 2 days to safely include end.
    p2 = int(datetime.combine(end + timedelta(days=2), datetime.min.time(), tzinfo=TZ).timestamp())
    payload = get_json(YAHOO_CHART.format(symbol=symbol), params={"period1": p1, "period2": p2, "interval": "1d", "events": "history"})
    result = payload.get("chart", {}).get("result")
    if not result:
        return pd.DataFrame()
    r = result[0]
    ts = r.get("timestamp", [])
    q = (r.get("indicators", {}).get("quote") or [{}])[0]
    closes = q.get("close", [])
    rows = []
    for t, c in zip(ts, closes):
        if c is None:
            continue
        d = datetime.fromtimestamp(t, TZ).date()
        rows.append({"date": d, "close": float(c)})
    return pd.DataFrame(rows)


def compare_yahoo_close(symbol: str, as_of: date, official_close: float) -> tuple[float | None, bool, str]:
    try:
        yd = fetch_yahoo_daily(symbol, as_of - timedelta(days=4), as_of)
        hit = yd[yd["date"] == as_of]
        if hit.empty:
            return None, False, "Yahoo daily row unavailable"
        yc = float(hit.iloc[-1]["close"])
        ok = abs(yc - official_close) <= 0.011
        return yc, ok, "matched" if ok else f"official={official_close}, yahoo={yc}"
    except Exception as e:
        return None, False, f"Yahoo validation error: {e}"


def calculate(symbol: str, history: pd.DataFrame, actions: dict[str, list[dict[str, Any]]], require_second_source: bool = True) -> Result:
    df = history.copy()
    df["adj_close"] = adjusted_close(df, symbol, actions)
    as_of = df.iloc[-1]["date"]
    close = float(df.iloc[-1]["close"])
    volume = int(df.iloc[-1]["volume"])

    last60 = df.tail(60)
    if len(last60) != 60:
        raise ValidationError(f"MA60 requires exactly 60 completed sessions for {symbol}")
    ma60 = float(last60["adj_close"].mean())

    window_start = as_of - timedelta(days=364)
    w = df[(df["date"] >= window_start) & (df["date"] <= as_of)]
    if w.empty:
        raise ValidationError(f"No 52-week window data for {symbol}")
    high_idx = w["adj_close"].idxmax()
    high52 = float(df.loc[high_idx, "adj_close"])
    high52_date = df.loc[high_idx, "date"]
    drawdown = (1 - close / high52) * 100.0
    thresholds = {
        "minus_10": high52 * 0.90,
        "minus_15": high52 * 0.85,
        "minus_20": high52 * 0.80,
    }
    daily_return = None
    if len(df) >= 2:
        prev = float(df.iloc[-2]["close"])
        daily_return = (close / prev - 1) * 100.0

    # Cross-check current official row against TWSE OpenAPI STOCK_DAY_ALL when available.
    twse_all_ok = False
    twse_all_note = "unavailable"
    try:
        row = fetch_twse_all_today(symbol)
        if row:
            oc = num(row.get("ClosingPrice"))
            ov = num(row.get("TradeVolume"))
            close_ok = not math.isnan(oc) and abs(oc - close) <= 0.001
            volume_ok = math.isnan(ov) or int(ov) == volume
            twse_all_ok = close_ok and volume_ok
            twse_all_note = f"close={oc}, volume={None if math.isnan(ov) else int(ov)}"
    except Exception as e:
        twse_all_note = f"error: {e}"

    yahoo_close, yahoo_ok, yahoo_note = compare_yahoo_close(symbol, as_of, close)
    second_ok = yahoo_ok if require_second_source else True

    # We permit historical computation only if the official monthly series is complete enough;
    # publication flag requires second-source match as requested by the user.
    publishable = bool(second_ok)
    validation = {
        "twse_monthly_history": True,
        "twse_openapi_same_day_match": twse_all_ok,
        "twse_openapi_note": twse_all_note,
        "second_source": "Yahoo Finance daily",
        "second_source_match": yahoo_ok,
        "second_source_note": yahoo_note,
        "corporate_action_configured": True,
        "history_rows": int(len(df)),
        "publishable": publishable,
    }

    triggers = {
        "close_below_ma60": close < ma60,
        "below_ma60_and_drawdown_ge_10": close < ma60 and drawdown >= 10,
        "drawdown_ge_15": drawdown >= 15,
        "drawdown_ge_20": drawdown >= 20,
        "daily_return_le_minus_3": daily_return is not None and daily_return <= -3,
    }

    return Result(
        symbol=symbol,
        as_of=as_of,
        close=close,
        volume_shares=volume,
        ma60=ma60,
        ma60_start=last60.iloc[0]["date"],
        ma60_end=last60.iloc[-1]["date"],
        high52=high52,
        high52_date=high52_date,
        drawdown_pct=drawdown,
        thresholds=thresholds,
        daily_return_pct=daily_return,
        triggers=triggers,
        yahoo_close=yahoo_close,
        validation=validation,
    )


def result_json(r: Result) -> dict[str, Any]:
    if not r.validation["publishable"]:
        return {
            "symbol": r.symbol,
            "as_of": r.as_of.isoformat(),
            "status": "尚無法驗證",
            "validation": r.validation,
        }
    return {
        "symbol": r.symbol,
        "as_of": r.as_of.isoformat(),
        "status": "verified",
        "official_close": round(r.close, 4),
        "volume_shares": r.volume_shares,
        "ma60": round(r.ma60, 4),
        "ma60_period": {"start": r.ma60_start.isoformat(), "end": r.ma60_end.isoformat(), "sessions": 60},
        "high52_close": round(r.high52, 4),
        "high52_date": r.high52_date.isoformat(),
        "high52_window": {"start": (r.as_of - timedelta(days=364)).isoformat(), "end": r.as_of.isoformat()},
        "drawdown_pct": round(r.drawdown_pct, 4),
        "thresholds": {k: round(v, 4) for k, v in r.thresholds.items()},
        "daily_return_pct": None if r.daily_return_pct is None else round(r.daily_return_pct, 4),
        "triggers": r.triggers,
        "validation": r.validation,
        "formulas": {
            "ma60": "sum(last 60 completed adjusted closes) / 60",
            "high52": "max(adjusted close in [as_of-364d, as_of])",
            "drawdown_pct": "(1 - close / high52) * 100",
        },
    }


def save_history(symbol: str, df: pd.DataFrame, actions: dict[str, list[dict[str, Any]]]) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    out = df.copy()
    out["adjusted_close"] = adjusted_close(out, symbol, actions)
    out.to_csv(DATA / f"{symbol}_history.csv", index=False)


def latest_completed_date(symbol: str, today: date) -> tuple[date, pd.DataFrame]:
    hist = fetch_history(symbol, today, months=15)
    if hist.empty:
        raise ValidationError(f"No history for {symbol}")
    return hist.iloc[-1]["date"], hist


def run_close(now: datetime) -> dict[str, Any]:
    actions = load_actions()
    output: dict[str, Any] = {
        "generated_at": now.isoformat(),
        "mode": "close",
        "source_priority": ["TWSE STOCK_DAY", "TWSE STOCK_DAY_ALL", "Yahoo Finance daily cross-check"],
        "etfs": {},
    }
    dates = []
    for symbol in SYMBOLS:
        try:
            as_of, hist = latest_completed_date(symbol, now.date())
            save_history(symbol, hist, actions)
            r = calculate(symbol, hist, actions)
            output["etfs"][symbol] = result_json(r)
            dates.append(as_of)
        except Exception as e:
            output["etfs"][symbol] = {"symbol": symbol, "status": "尚無法驗證", "error": str(e)}
    output["official_data_date"] = max(dates).isoformat() if dates else None
    output["is_today_official_data_available"] = bool(dates and max(dates) == now.date())
    return output


def run_intraday(now: datetime) -> dict[str, Any]:
    # Historical indicators use the latest completed TWSE session only. Intraday quote is Yahoo and is never
    # inserted into MA60/High52.
    close_output = run_close(now)
    out: dict[str, Any] = {
        "generated_at": now.isoformat(),
        "mode": "intraday",
        "historical_cutoff": close_output.get("official_data_date"),
        "etfs": {},
    }
    for symbol in SYMBOLS:
        base = close_output["etfs"].get(symbol, {})
        if base.get("status") != "verified":
            out["etfs"][symbol] = {"status": "尚無法驗證", "historical_validation": base}
            continue
        try:
            yd = fetch_yahoo_daily(symbol, now.date() - timedelta(days=3), now.date())
            today_row = yd[yd["date"] == now.date()]
            if today_row.empty:
                raise ValidationError("No intraday/daily Yahoo quote for today")
            # Daily endpoint may be delayed; timestamp-free values are labelled as quote snapshot only.
            price = float(today_row.iloc[-1]["close"])
            high52 = float(base["high52_close"])
            ma60 = float(base["ma60"])
            dd = (1 - price / high52) * 100
            out["etfs"][symbol] = {
                "status": "盤中",
                "quote_source": "Yahoo Finance chart snapshot",
                "quote_time": now.isoformat(),
                "price": round(price, 4),
                "historical_cutoff": base["as_of"],
                "ma60": ma60,
                "high52_close": high52,
                "intraday_drawdown_pct": round(dd, 4),
                "thresholds": base["thresholds"],
                "temporary_triggers": {
                    "price_below_previous_ma60": price < ma60,
                    "drawdown_ge_10": dd >= 10,
                    "drawdown_ge_15": dd >= 15,
                    "drawdown_ge_20": dd >= 20,
                },
                "formal_trigger": False,
                "note": "盤中資料不得作為正式收盤觸發；正式結果由收盤模式確認。",
            }
        except Exception as e:
            out["etfs"][symbol] = {"status": "尚無法驗證", "error": str(e), "historical": base}
    return out


def write_outputs(mode: str, payload: dict[str, Any]) -> None:
    PUBLIC.mkdir(parents=True, exist_ok=True)
    target = PUBLIC / ("intraday.json" if mode == "intraday" else "close.json")
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    health = {
        "generated_at": payload.get("generated_at"),
        "mode": mode,
        "healthy": all(v.get("status") in {"verified", "盤中"} for v in payload.get("etfs", {}).values()),
        "statuses": {k: v.get("status") for k, v in payload.get("etfs", {}).items()},
    }
    (PUBLIC / "health.json").write_text(json.dumps(health, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["auto", "close", "intraday"], default="auto")
    args = parser.parse_args()
    now = datetime.now(TZ)
    mode = args.mode
    if mode == "auto":
        mode = "intraday" if now.hour < 13 else "close"
    payload = run_intraday(now) if mode == "intraday" else run_close(now)
    write_outputs(mode, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
