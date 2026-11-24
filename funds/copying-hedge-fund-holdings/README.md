# Can copying hedge fund holdings beat the market?

The study ranked 49 managers by the past returns of their Form 13F holdings.
From August 2016 to November 2026, the long-only backtest returned 20.7% a
year after modeled trading fees, compared with 12.6% for SPY. SPY led over
the final eight and sixteen quarters.

This Python tool applies the study's long-only rule to Form 13F filings and
stock prices from the Tradytics API. It returns the latest holdings and
target weights. Run it from the command line or call it from Python.

## Run it

Use Python 3.11 or later and a [Tradytics API key](https://tradytics.com/developer/keys)
with the `market` and `research` scopes. From this directory:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
export TRADYTICS_API_KEY="$(python -c 'from getpass import getpass; print(getpass("Tradytics API key: "))')"
tradytics-13f > portfolio.json
```

The command writes JSON to standard output. This example shows the date and
holdings fields with sample values:

```json
{
  "as_of": "2026-11-23",
  "quarter_end": "2026-09-30",
  "rebalance_date": "2026-11-17",
  "holdings": [
    {"symbol": "AAPL", "weight": 0.60},
    {"symbol": "MSFT", "weight": 0.40}
  ]
}
```

A weight of `0.60` means 60% of the portfolio. The report also includes the
selected managers, their scores and weights, and the number of managers that
qualified. The holdings show the target weights at the rebalance. A change in
stock price between rebalances does not change these weights.

The default universe has 49 managers. A full run makes hundreds of API calls.
For a first run on a small quota, use one manager and a few positions. The tool
fetches each ticker's price history once per run. See the [API docs](https://tradytics.com/docs)
for current limits.

## Change the strategy

```sh
tradytics-13f --lookback 8 --top-funds 10 --top-holdings 15 --weighting equal
```

| Option | Default | What it controls |
| --- | --- | --- |
| `--as-of YYYY-MM-DD` | Today in New York | Use the latest available rebalance through this date. |
| `--lookback N` | 4 | Number of completed quarterly holding periods used to rank managers. Minimum 2. |
| `--top-funds N` | 5 | Maximum number of eligible managers to select. |
| `--top-holdings N` | 20 | Maximum positions per manager, ranked by reported value. |
| `--weighting score\|equal` | `score` | Weight selected funds by positive score or equally. |
| `--funds FILE` | [49 study managers](src/tradytics_13f_portfolio/funds.json) | Replace the manager list with a JSON file of SEC filer IDs. |
| `--api-key KEY` | `TRADYTICS_API_KEY` | Override the environment variable. Use the environment variable for everyday runs to keep the key out of the command line. |

Save this example as `my-funds.json` to try the tool with a single manager.
A manager that changed filers can have multiple CIKs in its array.

```json
[
  {"id": "berkshire-hathaway", "name": "Berkshire Hathaway", "ciks": [1067983]}
]
```

```sh
tradytics-13f --funds my-funds.json --top-funds 1 --top-holdings 5
```

## Use it from Python

```python
from datetime import date
from getpass import getpass

from tradytics_13f_portfolio import current_portfolio, load_funds

portfolio = current_portfolio(
    api_key=getpass("Tradytics API key: "),
    as_of=date(2026, 11, 23),
    funds=load_funds("my-funds.json"),
    top_funds=1,
    top_holdings=5,
)

for holding in portfolio["holdings"]:
    print(f"{holding['symbol']:8} {holding['weight']:7.2%}")
```

Call `current_portfolio()` to use the environment key and the default strategy.

## How it works

1. Add 45 days to each quarter end. If the result falls on a market closure,
   move it to the next SPY session. Rebalance at the close of the session
   after that. Use the book filed before the rebalance date in New York.
   For a manager with multiple CIKs, use the eligible book with the largest
   total reported value after removing options.
2. Rank each book's non-option positions by reported value. Take the top
   positions, drop entries without a mapped ticker or a closing price on the
   rebalance date, and divide the remaining reported values by their sum.
3. Hold each earlier book from one rebalance close to the next and subtract
   SPY's return for the same period. Score each manager by the mean excess
   return over the lookback. A manager needs at least two usable periods and
   at least half the lookback rounded down. A missing observation keeps its
   place in the window. If a ticker stops trading during a period, use its
   last available close through the period end.
4. Select the highest scores among managers with usable current holdings.
   Break ties by fund ID. Set negative scores to zero for weighting. If all
   selected scores are zero or lower, give the selected funds equal weights.
5. Multiply each stock's weight in its fund by that fund's weight. Add the
   results for stocks held by multiple funds. The final weights sum to 1.

The API supplies ticker mappings and daily prices. These can differ from the
study data. Historical runs use filing timestamps and available restatements,
but the API can fold later additions into an earlier 13F book. The calculation
uses price returns and excludes dividends and trading costs.

## Develop

Install in editable mode and run the offline tests:

```sh
python -m pip install -e .
python -m unittest discover -s tests -v
```

The tests use synthetic API responses and require no API key. The portfolio
calculation is in [portfolio.py](src/tradytics_13f_portfolio/portfolio.py).
API calls and filing selection are in [data.py](src/tradytics_13f_portfolio/data.py).

The code uses the [MIT License](LICENSE).
