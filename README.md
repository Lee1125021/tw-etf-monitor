# tw-etf-monitor

TWSE 0050 / 0052 ETF monitoring and drawdown validation.

## What this repository does

- Pulls official monthly daily-trading history from TWSE `STOCK_DAY`.
- Keeps at least 15 months of 0050 / 0052 daily data.
- Applies verified ETF split adjustments from TWSE announcements.
- Calculates MA60 from the latest 60 completed trading-day closes.
- Calculates the 52-week official closing high from the rolling 364-day calendar window.
- Calculates drawdown and -10% / -15% / -20% trigger prices.
- Cross-checks the latest official close with a second market source before publishing.
- Uses the official TWSE MIS quote as the intraday value and Yahoo Finance only as a validation layer.
- Writes machine-readable results to `public/close.json`, `public/intraday.json`, and `public/health.json`.
- Fails closed: when required validation fails, output status is `尚無法驗證` instead of inventing a value.

## Corporate actions

Configured from official TWSE ETF announcements:

- 0050: 4:1 split, new units listed 2025-06-18.
- 0052: 7:1 split, new units listed 2025-11-26.

The source URLs are stored in `config/corporate_actions.json`.

## Scheduled runs

GitHub Actions starts early enough to absorb observed hosted-runner delays and retries unavailable sources.
Runs are scheduled Monday-Friday at Taiwan time:

- 08:31 and 08:47 — intraday jobs; an on-time job waits until 10:50 before requesting a quote
- 14:07 and 14:27 — close-data jobs; an on-time job waits until 15:45, then retries while official or validation data is pending

The 11:00 ChatGPT report reads the current-day `public/intraday.json`; the 16:30 report reads the
current-day `public/close.json`. Both require `public/health.json` to report `healthy: true`.

## Output formulas

- `MA60 = arithmetic mean of the latest 60 completed adjusted closes`
- `High52 = max(adjusted close from as_of - 364 days through as_of)`
- `Drawdown = (1 - Close / High52) * 100%`
- `-10% = High52 * 0.90`
- `-15% = High52 * 0.85`
- `-20% = High52 * 0.80`

## Data sources

Primary historical and close data: Taiwan Stock Exchange (TWSE) `STOCK_DAY`.

Primary intraday quote: official TWSE MIS real-time quote.

Second-source cross-check: Yahoo Finance daily history or a date-stamped Yahoo market snapshot. Yahoo
is used only as a verification layer; it never supplies the published price, MA60, or High52. Those
values are calculated from or read from official TWSE data.
