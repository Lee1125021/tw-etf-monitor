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
- Writes machine-readable results to `public/close.json`, `public/intraday.json`, and `public/health.json`.
- Fails closed: when required validation fails, output status is `尚無法驗證` instead of inventing a value.

## Corporate actions

Configured from official TWSE ETF announcements:

- 0050: 4:1 split, new units listed 2025-06-18.
- 0052: 7:1 split, new units listed 2025-11-26.

The source URLs are stored in `config/corporate_actions.json`.

## Scheduled runs

GitHub Actions runs Monday-Friday at Taiwan time:

- 10:55 — intraday monitor preparation
- 16:10 — first close-data update
- 16:22 — second close-data confirmation

The 16:30 ChatGPT report can read `public/close.json` after the final update.

## Output formulas

- `MA60 = arithmetic mean of the latest 60 completed adjusted closes`
- `High52 = max(adjusted close from as_of - 364 days through as_of)`
- `Drawdown = (1 - Close / High52) * 100%`
- `-10% = High52 * 0.90`
- `-15% = High52 * 0.85`
- `-20% = High52 * 0.80`

## Data sources

Primary: Taiwan Stock Exchange (TWSE).

Second-source close cross-check: Yahoo Finance daily history. This second source is used only as a verification layer; MA60 and High52 are calculated from the official TWSE history, not from third-party technical indicators.
