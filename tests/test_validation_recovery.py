import math
import unittest
from datetime import date
from src.monitor import num, payload_ready, SYMBOLS

class RecoveryTests(unittest.TestCase):
    def test_missing_numeric(self):
        for value in ("-", "--", None, "N/A"):
            self.assertTrue(math.isnan(num(value)))
        self.assertEqual(num("1,234.5"), 1234.5)

    def test_close_rejects_official_mismatch(self):
        day = date(2026, 11, 27)
        validation = dict(twse_monthly_history=True, twse_openapi_same_day_match=False,
                          corporate_action_configured=True, second_source_match=True, publishable=True)
        payload = dict(mode="close", official_data_date=str(day), is_today_official_data_available=True,
                       etfs={s: dict(status="verified", as_of=str(day), validation=dict(validation)) for s in SYMBOLS})
        self.assertFalse(payload_ready("close", payload, day))
        for item in payload["etfs"].values():
            item["validation"]["twse_openapi_same_day_match"] = True
        self.assertTrue(payload_ready("close", payload, day))

    def test_intraday_rejects_today_history(self):
        day = date(2026, 11, 27)
        self.assertFalse(payload_ready("intraday", dict(mode="intraday", historical_cutoff=str(day),
            etfs={s: {} for s in SYMBOLS}), day))
