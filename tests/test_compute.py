import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch

import pandas as pd

from src import monitor
from src.monitor import adjusted_close, parse_twse_intraday_payload, payload_ready


class MonitorTests(unittest.TestCase):
    def test_split_adjustment_only_before_effective_date(self):
        df = pd.DataFrame(
            {
                "date": [date(2025, 11, 18), date(2025, 11, 26), date(2025, 11, 27)],
                "close": [245.0, 35.5, 36.0],
            }
        )
        actions = {"0052": [{"type": "split", "effective_date": "2025-11-26", "ratio": 7.0}]}
        self.assertEqual(adjusted_close(df, "0052", actions).tolist(), [35.0, 35.5, 36.0])

    def test_no_adjustment_after_0050_split_window(self):
        df = pd.DataFrame(
            {
                "date": [date(2025, 8, 13), date(2026, 8, 13)],
                "close": [50.0, 100.0],
            }
        )
        actions = {"0050": [{"type": "split", "effective_date": "2025-06-18", "ratio": 4.0}]}
        self.assertEqual(adjusted_close(df, "0050", actions).tolist(), [50.0, 100.0])

    def test_52_week_calendar_window_definition(self):
        as_of = date(2026, 8, 13)
        self.assertEqual(as_of - timedelta(days=364), date(2025, 8, 14))

    def test_parse_twse_intraday_payload_uses_official_quote(self):
        payload = {
            "rtcode": "0000",
            "msgArray": [
                {"c": "0050", "z": "103.8000", "y": "103.1000", "d": "20260820", "t": "11:00:00"}
            ],
        }
        quote = parse_twse_intraday_payload(payload, "0050")
        self.assertEqual(quote["price"], 103.8)
        self.assertEqual(quote["previous_close"], 103.1)
        self.assertEqual(quote["date"], date(2026, 8, 20))
        self.assertEqual(quote["timestamp"].isoformat(), "2026-08-20T11:00:00+08:00")

    def test_yahoo_previous_close_can_validate_missing_daily_row(self):
        snapshot = {
            "price": 104.2,
            "timestamp": int(datetime(2026, 8, 21, 11, 0, tzinfo=monitor.TZ).timestamp()),
            "date": date(2026, 8, 21),
            "previous_close": 103.8,
        }
        with (
            patch.object(monitor, "fetch_yahoo_daily", return_value=pd.DataFrame()),
            patch.object(monitor, "fetch_yahoo_snapshot", return_value=snapshot),
        ):
            value, matched, note = monitor.compare_yahoo_close("0050", date(2026, 8, 20), 103.8)
        self.assertEqual(value, 103.8)
        self.assertTrue(matched)
        self.assertEqual(note, "matched via Yahoo previous close")

    def test_close_payload_requires_current_date_and_second_source(self):
        item = {
            "status": "verified",
            "as_of": "2026-08-20",
            "validation": {"second_source_match": True, "publishable": True},
        }
        payload = {
            "mode": "close",
            "official_data_date": "2026-08-20",
            "is_today_official_data_available": True,
            "etfs": {"0050": item, "0052": {**item, "validation": dict(item["validation"])}},
        }
        self.assertTrue(payload_ready("close", payload, date(2026, 8, 20)))
        payload["etfs"]["0052"]["validation"] = {"second_source_match": False, "publishable": False}
        self.assertFalse(payload_ready("close", payload, date(2026, 8, 20)))

    def test_intraday_payload_requires_current_official_quote_and_second_source(self):
        item = {
            "status": "盤中",
            "quote_date": "2026-08-20",
            "validation": {"second_source_match": True, "publishable": True},
        }
        payload = {
            "mode": "intraday",
            "etfs": {"0050": item, "0052": {**item, "validation": dict(item["validation"])}},
        }
        self.assertTrue(payload_ready("intraday", payload, date(2026, 8, 20)))
        payload["etfs"]["0052"]["quote_date"] = "2026-08-19"
        self.assertFalse(payload_ready("intraday", payload, date(2026, 8, 20)))

    def test_intraday_publishes_twse_price_only_after_yahoo_match(self):
        now = datetime(2026, 8, 20, 10, 50, tzinfo=monitor.TZ)
        base = {
            "status": "verified",
            "as_of": "2026-08-19",
            "ma60": 99.0,
            "high52_close": 110.0,
            "thresholds": {"minus_10": 99.0, "minus_15": 93.5, "minus_20": 88.0},
        }
        close_payload = {
            "official_data_date": "2026-08-19",
            "etfs": {"0050": base, "0052": dict(base)},
        }

        def official_quote(symbol):
            return {
                "price": 103.8 if symbol == "0050" else 60.45,
                "previous_close": 103.1 if symbol == "0050" else 60.0,
                "date": now.date(),
                "timestamp": now,
            }

        def yahoo_match(symbol, official):
            return official["price"], True, "matched", now

        with (
            patch.object(monitor, "run_close", return_value=close_payload),
            patch.object(monitor, "fetch_twse_intraday_quote", side_effect=official_quote),
            patch.object(monitor, "compare_yahoo_intraday", side_effect=yahoo_match),
        ):
            payload = monitor.run_intraday(now)

        self.assertEqual(payload["etfs"]["0050"]["price"], 103.8)
        self.assertEqual(payload["etfs"]["0050"]["quote_source"], "TWSE MIS real-time quote")
        self.assertTrue(payload_ready("intraday", payload, now.date()))

    def test_intraday_fails_closed_when_yahoo_does_not_match(self):
        now = datetime(2026, 8, 20, 10, 50, tzinfo=monitor.TZ)
        base = {
            "status": "verified",
            "as_of": "2026-08-19",
            "ma60": 99.0,
            "high52_close": 110.0,
            "thresholds": {},
        }
        close_payload = {"official_data_date": "2026-08-19", "etfs": {"0050": base, "0052": dict(base)}}
        quote = {"price": 103.8, "previous_close": 103.1, "date": now.date(), "timestamp": now}
        mismatch = (103.7, False, "Yahoo intraday price mismatch", now)
        with (
            patch.object(monitor, "run_close", return_value=close_payload),
            patch.object(monitor, "fetch_twse_intraday_quote", return_value=quote),
            patch.object(monitor, "compare_yahoo_intraday", return_value=mismatch),
        ):
            payload = monitor.run_intraday(now)

        self.assertEqual(payload["etfs"]["0050"]["status"], "尚無法驗證")
        self.assertNotIn("price", payload["etfs"]["0050"])
        self.assertFalse(payload_ready("intraday", payload, now.date()))


if __name__ == "__main__":
    unittest.main()
