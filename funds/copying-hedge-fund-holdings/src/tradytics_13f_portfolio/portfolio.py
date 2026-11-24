from bisect import bisect_left
from calendar import monthrange
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import cache
from itertools import pairwise
from math import fsum
from statistics import mean
from typing import Literal, TypedDict

from tradytics import Client, TradyticsError

from .data import NEW_YORK, Book, Prices, daily_prices, filing_book
from .funds import Fund, load_funds, validate_funds

Weighting = Literal["score", "equal"]


class PortfolioError(RuntimeError):
    """The API or available data could not produce a portfolio."""


class Holding(TypedDict):
    symbol: str
    weight: float


class RankedFund(TypedDict):
    id: str
    name: str
    cik: int
    mean_excess_return: float
    periods: int
    weight: float


class Portfolio(TypedDict):
    as_of: str
    quarter_end: str
    rebalance_date: str
    lookback: int
    top_funds: int
    top_holdings: int
    weighting: Weighting
    universe_size: int
    eligible_fund_count: int
    ranked_funds: list[RankedFund]
    holdings: list[Holding]


@dataclass(frozen=True)
class Rebalance:
    quarter_end: date
    session: date


def _quarter_ends(as_of: date, count: int) -> list[date]:
    current = 4 * as_of.year + (as_of.month - 1) // 3
    ends = []
    for index in range(max(4 * 2000, current - count), current):
        year, quarter = divmod(index, 4)
        month = (quarter + 1) * 3
        ends.append(date(year, month, monthrange(year, month)[1]))
    return ends


def _schedule(ends: Sequence[date], sessions: Sequence[date]) -> list[Rebalance]:
    if not sessions:
        return []
    schedule = []
    for end in ends:
        deadline = end + timedelta(days=45)
        index = bisect_left(sessions, deadline) + 1
        if sessions[0] <= deadline and index < len(sessions):
            schedule.append(Rebalance(end, sessions[index]))
    return schedule


def _fund_book(client: Client, fund: Fund, rebalance: Rebalance, top_holdings: int) -> Book | None:
    books = (filing_book(client, cik, rebalance.quarter_end, rebalance.session, top_holdings) for cik in fund.ciks)
    return max((book for book in books if book is not None), key=lambda book: (book.value, -book.cik), default=None)


def _priced_weights(book: Book, session: date, prices: Callable[[str], Prices]) -> dict[str, float]:
    values = {symbol: value for symbol, value in book.holdings.items() if session in prices(symbol).closes}
    total = fsum(values.values())
    return {symbol: value / total for symbol, value in values.items()}


def _period_excess(
    weights: dict[str, float], start: date, end: date, prices: Callable[[str], Prices], benchmark: Prices,
) -> float:
    fund_return = fsum(
        weight * (prices(symbol).through(end) / prices(symbol).closes[start] - 1)
        for symbol, weight in weights.items()
    )
    return fund_return - (benchmark.closes[end] / benchmark.closes[start] - 1)


def _calculate(
    client: Client, as_of: date, cutoff: datetime, funds: tuple[Fund, ...],
    lookback: int, top_funds: int, top_holdings: int, weighting: Weighting,
) -> Portfolio:
    ends = _quarter_ends(as_of, lookback + 2)
    if not ends:
        raise PortfolioError("SPY history starts in 2000. Choose a later as-of date.")
    benchmark = daily_prices(client, "SPY", ends[0] + timedelta(days=38), cutoff)
    schedule = _schedule(ends, benchmark.sessions)[-(lookback + 1):]
    minimum = max(2, lookback // 2)
    if len(schedule) <= minimum:
        raise PortfolioError("SPY history does not cover enough completed holding periods at this date.")

    latest = schedule[-1]
    if latest.session < as_of:
        cutoff = datetime.combine(latest.session + timedelta(days=1), time.min, NEW_YORK) - timedelta(seconds=1)

    @cache
    def prices(symbol: str) -> Prices:
        if symbol == "SPY":
            return benchmark
        return daily_prices(client, symbol, schedule[0].session, cutoff)

    candidates: list[tuple[RankedFund, dict[str, float]]] = []
    for fund in funds:
        book = _fund_book(client, fund, latest, top_holdings)
        if book is None:
            continue
        current = _priced_weights(book, latest.session, prices)
        if not current:
            continue

        excess = []
        for start, end in pairwise(schedule):
            earlier = _fund_book(client, fund, start, top_holdings)
            if earlier is not None and (weights := _priced_weights(earlier, start.session, prices)):
                excess.append(_period_excess(weights, start.session, end.session, prices, benchmark))
        if len(excess) >= minimum:
            candidates.append(({
                "id": fund.id,
                "name": fund.name,
                "cik": book.cik,
                "mean_excess_return": mean(excess),
                "periods": len(excess),
                "weight": 0.0,
            }, current))

    if not candidates:
        raise PortfolioError(f"No fund had a usable filing for {latest.quarter_end} and enough scored periods.")

    candidates.sort(key=lambda item: (-item[0]["mean_excess_return"], item[0]["id"]))
    selected = candidates[:top_funds]
    scores = [max(0.0, fund["mean_excess_return"]) for fund, _ in selected]
    score_total = fsum(scores)
    allocations: dict[str, float] = defaultdict(float)
    for (fund, weights), score in zip(selected, scores, strict=True):
        weight = score / score_total if weighting == "score" and score_total else 1 / len(selected)
        fund["weight"] = weight
        if weight:
            for symbol, position_weight in weights.items():
                allocations[symbol] += weight * position_weight

    total = fsum(allocations.values())
    return {
        "as_of": as_of.isoformat(),
        "quarter_end": latest.quarter_end.isoformat(),
        "rebalance_date": latest.session.isoformat(),
        "lookback": lookback,
        "top_funds": top_funds,
        "top_holdings": top_holdings,
        "weighting": weighting,
        "universe_size": len(funds),
        "eligible_fund_count": len(candidates),
        "ranked_funds": [fund for fund, _ in selected],
        "holdings": [
            {"symbol": symbol, "weight": weight / total}
            for symbol, weight in sorted(allocations.items(), key=lambda item: (-item[1], item[0]))
        ],
    }


def current_portfolio(
    *,
    api_key: str | None = None,
    as_of: date | None = None,
    funds: Sequence[Fund] | None = None,
    lookback: int = 4,
    top_funds: int = 5,
    top_holdings: int = 20,
    weighting: Weighting = "score",
) -> Portfolio:
    """Return long-only target weights for the latest completed rebalance as of a New York date.

    The explicit key takes precedence over TRADYTICS_API_KEY. Weights and excess
    returns are decimal fractions.
    """
    if type(lookback) is not int or lookback < 2:
        raise ValueError("Lookback must be an integer of at least 2.")
    if any(type(value) is not int or value < 1 for value in (top_funds, top_holdings)):
        raise ValueError("Top funds and top holdings must be positive integers.")
    if weighting not in ("score", "equal"):
        raise ValueError("Weighting must be score or equal.")

    now = datetime.now(NEW_YORK)
    as_of = now.date() if as_of is None else as_of
    if type(as_of) is not date or as_of > now.date():
        raise ValueError("As-of date must be today or earlier.")
    end_of_day = datetime.combine(as_of + timedelta(days=1), time.min, NEW_YORK)
    cutoff = min(now - timedelta(seconds=2), end_of_day - timedelta(seconds=1))
    universe = load_funds() if funds is None else validate_funds(funds)

    try:
        return _calculate(Client(api_key=api_key), as_of, cutoff, universe, lookback, top_funds, top_holdings, weighting)
    except TradyticsError as error:
        messages = {
            0: "Could not reach the Tradytics API.",
            401: "Set TRADYTICS_API_KEY or pass a valid Tradytics API key.",
            403: "The Tradytics API denied access. Check the key's market and research scopes.",
            429: "The Tradytics API rate limit was reached. Wait for the limit to reset or use a smaller fund list.",
        }
        message = messages.get(error.status, f"The Tradytics API returned HTTP {error.status}.")
        raise PortfolioError(message) from None
