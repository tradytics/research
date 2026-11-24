import argparse
import json
from datetime import date
from pathlib import Path
from typing import Sequence

from .funds import load_funds
from .portfolio import PortfolioError, current_portfolio


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print the Tradytics 13F portfolio and target weights as JSON.")
    parser.add_argument("--api-key", help="Use this Tradytics API key instead of TRADYTICS_API_KEY.")
    parser.add_argument("--as-of", type=date.fromisoformat, metavar="YYYY-MM-DD", help="Use this New York date.")
    parser.add_argument("--funds", type=Path, metavar="FILE", help="Read a custom fund list from JSON.")
    parser.add_argument("--lookback", type=int, default=4, help="Number of completed quarters to score. Default is 4.")
    parser.add_argument("--top-funds", type=int, default=5, help="Number of funds to select. Default is 5.")
    parser.add_argument("--top-holdings", type=int, default=20, help="Positions per fund. Default is 20.")
    parser.add_argument("--weighting", choices=("score", "equal"), default="score", help="How to weight selected funds.")
    args = parser.parse_args(argv)

    try:
        result = current_portfolio(
            api_key=args.api_key,
            as_of=args.as_of,
            funds=load_funds(args.funds) if args.funds is not None else None,
            lookback=args.lookback,
            top_funds=args.top_funds,
            top_holdings=args.top_holdings,
            weighting=args.weighting,
        )
    except (OSError, ValueError, PortfolioError) as error:
        parser.exit(1, f"{parser.prog}: {error}\n")

    print(json.dumps(result, indent=2, allow_nan=False))
    return 0
