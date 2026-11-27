import unittest
from datetime import date
from unittest.mock import patch
from src import monitor

class OfficialFallbackTests(unittest.TestCase):
    day = date(2026, 11, 27)
    payload = {"stat":"OK","date":"20261127","tables":[{"fields":["證券代號","成交股數","收盤價"],"data":[["0050","1,000","92.40"]]}]}
    def test_dates(self):
        for raw in ("1151127","115/11/27","20261127","2026-11-27"):
            self.assertEqual(monitor.parse_twse_date(raw), self.day)
    def test_stale_fallback(self):
        with patch.object(monitor,"fetch_twse_all_today",return_value={"Date":"1151126","ClosingPrice":"92.85","TradeVolume":"2000"}), patch.object(monitor,"get_json",return_value=self.payload):
            self.assertEqual(monitor.official_close_check("0050",self.day,92.4,1000)[::2],(True,"MI_INDEX"))
    def test_same_day_conflict(self):
        with patch.object(monitor,"fetch_twse_all_today",return_value={"Date":"1151127","ClosingPrice":"92.85","TradeVolume":"1000"}), patch.object(monitor,"get_json") as fallback:
            self.assertFalse(monitor.official_close_check("0050",self.day,92.4,1000)[0])
            fallback.assert_not_called()
    def test_wrong_fallback_date(self):
        with patch.object(monitor,"fetch_twse_all_today",return_value=None), patch.object(monitor,"get_json",return_value={**self.payload,"date":"20261126"}):
            self.assertFalse(monitor.official_close_check("0050",self.day,92.4,1000)[0])
