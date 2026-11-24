from bisect import bisect_right
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from math import isfinite
from zoneinfo import ZoneInfo

from tradytics import Client, TradyticsError

NEW_YORK = ZoneInfo("America/New_York")


@dataclass
class Prices:
    closes: dict[date, float]
    sessions: list[date] = field(init=False)

    def __post_init__(self) -> None:
        self.sessions = sorted(self.closes)

    def through(self, session: date) -> float:
        index = bisect_right(self.sessions, session)
        if index == 0:
            raise LookupError(f"No close on or before {session}.")
        return self.closes[self.sessions[index - 1]]


@dataclass(frozen=True)
class Book:
    cik: int
    value: float
    holdings: dict[str, float]


def _available_rows(
    client: Client,
    method: Callable[..., dict],
    *,
    missing_symbol: bool = False,
    **params,
) -> Iterator[dict]:
    def fetch(**query) -> dict:
        try:
            return method(**query)
        except TradyticsError as error:
            if query.get("cursor") is None:
                if (error.status, error.code) == (404, "not_found"):
                    return {"data": []}
                if missing_symbol and (error.status, error.code) == (400, "invalid_symbol"):
                    return {"data": []}
            raise

    return client.paginate(fetch, **params)


def daily_prices(client: Client, symbol: str, start: date, cutoff: datetime) -> Prices:
    rows = _available_rows(
        client, client.stocks.bars, missing_symbol=True,
        symbol=symbol, timeframe="1d", from_=start.isoformat(),
        to=cutoff.isoformat(timespec="seconds"), limit=5000,
    )
    return Prices({
        date.fromisoformat(row["time"][:10]): row["close"]
        for row in rows if row["close"] is not None and isfinite(row["close"]) and row["close"] > 0
    })


def filing_book(client: Client, cik: int, quarter: date, rebalance: date, top_holdings: int) -> Book | None:
    # The SDK provides generic GET for the amendment flag.
    rows = _available_rows(
        client, client.get, path=f"/disclosures/institutions/{cik}",
        quarter=quarter.isoformat(), include_amended=True, limit=1000,
    )
    versions = {}
    for row in rows:
        filed = datetime.fromisoformat(row["filed_at"])
        if filed.astimezone(NEW_YORK).date() < rebalance:
            versions.setdefault((filed, row["accession"]), []).append(row)
    if not versions:
        return None

    positions = [
        row for row in versions[max(versions)]
        if not row["put_call"] and row["value_usd"] is not None
        and isfinite(row["value_usd"]) and row["value_usd"] > 0
    ]
    positions.sort(key=lambda row: (-row["value_usd"], row["cusip"]))
    holdings: dict[str, float] = {}
    for row in positions[:top_holdings]:
        if symbol := row["symbol"]:
            holdings[symbol] = holdings.get(symbol, 0.0) + row["value_usd"]
    return Book(cik, sum(row["value_usd"] for row in positions), holdings)
