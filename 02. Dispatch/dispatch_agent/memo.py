"""Travel Memo readers.

`MemoSource` is the swap point: `DocxMemoSource` parses a local .docx now; a
`GoogleDocMemoSource` can be added later returning the same `TravelMemo` shape.
"""
import re
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path

import docx

_FLIGHT_NO = re.compile(r"^[A-Z0-9]{2}\s?\d+")
_TIME = re.compile(r"(\d{1,2}:\d{2})")
_TERMINAL = re.compile(r"Terminal\s+(\d)")
_PLUS_DAYS = re.compile(r"\+\s*(\d+)\s*day")
_NAME_ROLE = re.compile(r"^(.*?)\s*\((.*)\)\s*$")


@dataclass
class FlightLeg:
    date: date  # departure date of this leg (see korea_arrival for arrival-date adjustment)
    airline: str
    flight_no: str
    from_code: str
    from_terminal: str | None
    to_code: str
    to_terminal: str | None
    depart: str
    arrive: str
    arrive_offset_days: int = 0

    @property
    def terminal(self) -> str | None:
        """ICN terminal for this leg (from side if departing ICN, to side if arriving ICN)."""
        if self.from_code == "ICN":
            return self.from_terminal
        if self.to_code == "ICN":
            return self.to_terminal
        return None


@dataclass
class TravelMemo:
    passenger: str
    role: str
    legs: list

    @property
    def korea_departure(self) -> FlightLeg | None:
        return next((leg for leg in self.legs if leg.from_code == "ICN"), None)

    @property
    def korea_arrival(self) -> FlightLeg | None:
        leg = next((leg for leg in self.legs if leg.to_code == "ICN"), None)
        if leg is None:
            return None
        return replace(leg, date=leg.date + timedelta(days=leg.arrive_offset_days))


def _parse_date(cell: str) -> date:
    # "Sunday May 17, 2026" / "Monday Jun 22, 2026" -> date(...)
    _, rest = cell.strip().split(" ", 1)
    rest = rest.strip()
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(rest, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognized date: {cell!r}")


def _parse_city(cell: str):
    # "ICN / South Korea Terminal 1" -> ("ICN", "1"); "PDX / US -" -> ("PDX", None)
    code = cell.split("/")[0].strip().split()[0]
    m = _TERMINAL.search(cell)
    return code, (m.group(1) if m else None)


def _parse_time(cell: str):
    # "16:05 + 1 day" -> ("16:05", 1)
    m = _TIME.match(cell.strip())
    time = m.group(1) if m else cell.strip()
    off = _PLUS_DAYS.search(cell)
    return time, (int(off.group(1)) if off else 0)


class DocxMemoSource:
    """Parse a Travel Memo from a local .docx file."""

    def load(self, path) -> TravelMemo:
        doc = docx.Document(str(path))
        passenger, role = self._passenger(doc)
        legs = self._legs(doc)
        return TravelMemo(passenger=passenger, role=role, legs=legs)

    @staticmethod
    def _passenger(doc):
        cell = doc.tables[0].rows[0].cells[1].text.strip()
        m = _NAME_ROLE.match(cell)
        return (m.group(1), m.group(2)) if m else (cell, "")

    @staticmethod
    def _legs(doc):
        legs = []
        for row in doc.tables[1].rows:
            cells = [c.text.strip().replace("\n", " ") for c in row.cells]
            flight_no = " ".join(cells[2].split())
            if not _FLIGHT_NO.match(flight_no):
                continue  # header row or the disclaimer row
            from_code, from_term = _parse_city(cells[3])
            to_code, to_term = _parse_city(cells[4])
            depart, _ = _parse_time(cells[5])
            arrive, arr_off = _parse_time(cells[6])
            legs.append(
                FlightLeg(
                    date=_parse_date(cells[0]),
                    airline=cells[1],
                    flight_no=flight_no,
                    from_code=from_code,
                    from_terminal=from_term,
                    to_code=to_code,
                    to_terminal=to_term,
                    depart=depart,
                    arrive=arrive,
                    arrive_offset_days=arr_off,
                )
            )
        return legs
