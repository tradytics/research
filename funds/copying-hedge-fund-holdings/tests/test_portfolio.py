import io
import json
import os
import unittest
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from tradytics import Client

from tradytics_13f_portfolio import Fund, PortfolioError, current_portfolio, load_funds
from tradytics_13f_portfolio.cli import main
from tradytics_13f_portfolio.data import filing_book

AS_OF = date(2025, 5, 19)
QUARTERS = ("2024-09-30", "2024-12-31", "2025-03-31")
SESSIONS = ("2024-11-15", "2025-02-18", "2025-05-16")
FILINGS = ("2024-11-14T18:00:00Z", "2025-02-14T18:00:00Z", "2025-05-15T18:00:00Z")
FUNDS = (
    Fund("beta", "Beta", (200,)),
    Fund("alpha", "Alpha", (100, 101)),
    Fund("gamma", "Gamma", (300,)),
)


def position(symbol, value, filed_at, accession="original", put_call=None, cusip=None):
    return {
        "symbol": symbol, "value_usd": value, "filed_at": filed_at, "accession": accession,
        "put_call": put_call, "cusip": cusip or symbol or "unmapped",
    }


class FixtureAPI:
    def __init__(self):
        self.calls = []
        self.fail_on_cursor = False

    def __call__(self, request, timeout):
        url = urlsplit(request.full_url)
        params = {key: values[0] for key, values in parse_qs(url.query).items()}
        self.calls.append((url.path, params))
        if url.path.endswith("/stocks/bars"):
            return self.prices(params)
        if "/disclosures/institutions/" in url.path:
            return self.filing(int(url.path.rsplit("/", 1)[-1]), params)
        raise AssertionError(f"Unexpected endpoint {url.path}")

    def prices(self, params):
        history = {
            "SPY": dict(zip(SESSIONS, (100, 110, 121))),
            "AAPL": dict(zip(SESSIONS, (100, 130, 156))),
            "MSFT": dict(zip(SESSIONS, (100, 120, 144))),
            "GONE": dict(zip(SESSIONS, (100, 100, None))),
        }
        symbol = params["symbol"]
        if symbol not in history:
            raise AssertionError(f"Unexpected ticker {symbol}")
        bars = history[symbol]
        if symbol == "SPY":
            bars = {
                "2024-08-13": 100, "2024-08-14": 100, "2024-08-15": 100,
                "2024-11-14": 100, "2025-02-14": 110, "2025-05-15": 121, **bars,
            }
        rows = [{"time": f"{day}T00:00:00Z", "close": close} for day, close in sorted(bars.items())]
        return self.page(rows)

    def filing(self, cik, params):
        assert params["include_amended"] == "true"
        quarter = QUARTERS.index(params["quarter"])
        filed = FILINGS[quarter]
        rows = []
        if quarter < 2 and cik != 101:
            symbol = {100: "AAPL", 200: "MSFT", 300: "GONE"}[cik]
            rows = [position(symbol, 100, filed)]
        elif quarter == 2:
            if cik == 100:
                if "cursor" in params:
                    if self.fail_on_cursor:
                        raise self.error(404, "not_found", "Page missing.")
                    return self.page([position("MSFT", 25, filed), position("UNUSED", 1, filed)])
                return self.page([
                    position("LATE", 5_000, "2025-05-16T04:00:00Z", accession="restatement"),
                    position(None, 100, filed),
                    position("OPTION", 1_000, filed, put_call="call"),
                    position("AAPL", 60, filed),
                    position("AAPL", 15, filed, cusip="second"),
                ], "next")
            if cik == 101:
                rows = [position("NEVER_FETCHED", 90, "2025-05-16T03:30:00Z")]
            if cik == 200:
                rows = [position("AAPL", 25, filed), position("MSFT", 75, filed)]
            if cik == 300:
                rows = [position("MSFT", 100, filed)]
        return self.page(rows)

    @staticmethod
    def page(rows, cursor=None):
        return io.BytesIO(json.dumps({"data": rows, "next_cursor": cursor}).encode())

    @staticmethod
    def error(status, code, message):
        body = {"error": {"code": code, "message": message}}
        return HTTPError("https://offline.example", status, message, {}, io.BytesIO(json.dumps(body).encode()))


class PortfolioTests(unittest.TestCase):
    def test_ranks_funds_and_returns_current_weights(self):
        api = FixtureAPI()
        with patch("urllib.request.urlopen", api), patch.dict(os.environ, {"TRADYTICS_API_KEY": "offline-environment"}):
            report = current_portfolio(
                api_key="offline-explicit", as_of=AS_OF, funds=FUNDS, lookback=2,
                top_funds=3, top_holdings=4,
            )

        self.assertEqual(report["quarter_end"], "2025-03-31")
        self.assertEqual(report["rebalance_date"], "2025-05-16")
        alpha, beta, gamma = report["ranked_funds"]
        self.assertEqual([fund["id"] for fund in (alpha, beta, gamma)], ["alpha", "beta", "gamma"])
        self.assertEqual(alpha["cik"], 100)
        self.assertAlmostEqual(alpha["mean_excess_return"], 0.15)
        self.assertAlmostEqual(beta["mean_excess_return"], 0.1)
        self.assertAlmostEqual(gamma["mean_excess_return"], -0.1)
        self.assertEqual(gamma["weight"], 0)
        weights = {holding["symbol"]: holding["weight"] for holding in report["holdings"]}
        self.assertEqual(set(weights), {"AAPL", "MSFT"})
        self.assertAlmostEqual(weights["AAPL"], 0.55)
        self.assertAlmostEqual(weights["MSFT"], 0.45)
        self.assertAlmostEqual(sum(weights.values()), 1)
        self.assertEqual(
            Counter(params["symbol"] for path, params in api.calls if path.endswith("/stocks/bars")),
            {"SPY": 1, "AAPL": 1, "MSFT": 1, "GONE": 1},
        )
        self.assertTrue(all(params["apiKey"] == "offline-explicit" for _, params in api.calls))
        self.assertIn("2025-05-16", next(params["to"] for _, params in api.calls if params.get("symbol") == "AAPL"))

    def test_filing_before_midnight_new_york_time_is_eligible(self):
        with patch("urllib.request.urlopen", FixtureAPI()):
            book = filing_book(Client(api_key="offline"), 101, date(2025, 3, 31), date(2025, 5, 16), 1)
        self.assertIsNotNone(book)
        self.assertEqual(book.holdings, {"NEVER_FETCHED": 90})

    def test_nonpositive_scores_fall_back_to_equal_weights(self):
        api = FixtureAPI()
        with patch("urllib.request.urlopen", api):
            report = current_portfolio(
                api_key="offline", as_of=AS_OF, funds=[FUNDS[2]], lookback=2,
            )
        self.assertEqual(report["ranked_funds"][0]["weight"], 1)
        self.assertEqual(report["holdings"], [{"symbol": "MSFT", "weight": 1.0}])

    def test_partial_api_page_fails_the_run(self):
        api = FixtureAPI()
        api.fail_on_cursor = True
        with patch("urllib.request.urlopen", api), self.assertRaisesRegex(PortfolioError, "HTTP 404"):
            current_portfolio(api_key="offline", as_of=AS_OF, funds=FUNDS, lookback=2)

    def test_cli_uses_environment_and_does_not_echo_api_errors(self):
        api = FixtureAPI()
        output = io.StringIO()
        with patch("urllib.request.urlopen", api), patch.dict(os.environ, {"TRADYTICS_API_KEY": "offline-env"}):
            with patch("tradytics_13f_portfolio.portfolio.load_funds", return_value=FUNDS), redirect_stdout(output):
                self.assertEqual(main(["--as-of", AS_OF.isoformat(), "--lookback", "2", "--top-holdings", "4"]), 0)
        self.assertEqual(json.loads(output.getvalue())["universe_size"], 3)
        self.assertTrue(all(params["apiKey"] == "offline-env" for _, params in api.calls))

        error = api.error(403, "missing_scope", "offline-key found in error")
        errors = io.StringIO()
        with patch("urllib.request.urlopen", side_effect=error), redirect_stderr(errors):
            with self.assertRaises(SystemExit) as exit:
                main(["--api-key", "offline-key"])
        self.assertEqual(exit.exception.code, 1)
        self.assertIn("market and research scopes", errors.getvalue())
        self.assertNotIn("offline-key", errors.getvalue())

    def test_default_universe_is_packaged(self):
        self.assertEqual(len(load_funds()), 49)


if __name__ == "__main__":
    unittest.main()
