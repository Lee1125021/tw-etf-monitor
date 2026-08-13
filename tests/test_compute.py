from datetime import date, timedelta

import pandas as pd

from src.monitor import adjusted_close


def test_split_adjustment_only_before_effective_date():
    df = pd.DataFrame({
        "date": [date(2025, 11, 18), date(2025, 11, 26), date(2025, 11, 27)],
        "close": [245.0, 35.5, 36.0],
    })
    actions = {"0052": [{"type": "split", "effective_date": "2025-11-26", "ratio": 7.0}]}
    out = adjusted_close(df, "0052", actions).tolist()
    assert out == [35.0, 35.5, 36.0]


def test_no_adjustment_after_0050_split_window():
    df = pd.DataFrame({
        "date": [date(2025, 8, 13), date(2026, 8, 13)],
        "close": [50.0, 100.0],
    })
    actions = {"0050": [{"type": "split", "effective_date": "2025-6-18", "ratio": 4.0}]}
    # ISO needs zero-padded date in production config; this test uses valid format below.
    actions["0050"][0]["effective_date"] = "2025-06-18"
    out = adjusted_close(df, "0050", actions).tolist()
    assert out == [50.0, 100.0]


def test_52_week_calendar_window_definition():
    as_of = date(2026, 8, 13)
    assert as_of - timedelta(days=364) == date(2025, 8, 14)
