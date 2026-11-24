import json
from collections.abc import Sequence
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path


@dataclass(frozen=True)
class Fund:
    """One manager, with one or more public SEC filer IDs."""

    id: str
    name: str
    ciks: tuple[int, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("Each fund needs a nonempty id.")
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Each fund needs a nonempty name.")
        if not self.ciks or any(type(cik) is not int or not 1 <= cik <= 9_999_999_999 for cik in self.ciks):
            raise ValueError("Each fund needs at least one positive integer CIK.")
        if len(set(self.ciks)) != len(self.ciks):
            raise ValueError("Each CIK must appear once per fund.")


def validate_funds(funds: Sequence[Fund]) -> tuple[Fund, ...]:
    funds = tuple(funds)
    if not funds:
        raise ValueError("Supply at least one fund.")
    ciks = [cik for fund in funds for cik in fund.ciks]
    if len({fund.id for fund in funds}) != len(funds) or len(set(ciks)) != len(ciks):
        raise ValueError("Fund IDs and CIKs must be unique across the fund list.")
    return funds


def load_funds(path: str | Path | None = None) -> tuple[Fund, ...]:
    """Load the default fund list or a JSON file with the same format."""
    source = Path(path) if path is not None else files(__package__).joinpath("funds.json")
    rows = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("The fund file must contain a JSON array.")
    funds = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"id", "name", "ciks"} or not isinstance(row["ciks"], list):
            raise ValueError("Each fund needs id, name, and an array of ciks.")
        funds.append(Fund(row["id"], row["name"], tuple(row["ciks"])))
    return validate_funds(funds)
