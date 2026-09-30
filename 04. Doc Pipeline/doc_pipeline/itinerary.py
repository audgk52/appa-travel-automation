"""Itinerary PDF → explicit flight / car facts, and the v1 shape check (PRD §7).

Only explicit Flight and Other Service fields are used. Headings never override details; a
heading/detail date conflict, a missing field or an unsupported shape stops before any preview.
"""
import re
from datetime import datetime

KR_AIRPORTS = {"ICN", "GMP"}


class ItineraryError(ValueError):
    """Unsupported or inconsistent itinerary — stop before preview."""


def read_pdf_text(path):
    import pdfplumber
    with pdfplumber.open(path) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


_HEADING = re.compile(r"^(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday), "
                      r"(\w+ \d{1,2}, \d{4}) - (Flight|Other Service)$")
_FIELD = re.compile(r"^(Status|Confirmation|Flight|Departure|Arrival|Terminal|Service|Pick-up|"
                    r"Drop-off|Route) (.+)$")
_FLIGHT_POINT = re.compile(r"^([A-Z]{3}) - (.+?) \| (\d{1,2}:\d{2} [AP]M, \w{3} \d{1,2}, \d{4})$")
_CAR_DEPART = re.compile(r"^(\d{1,2}:\d{2} [AP]M, \w{3} \d{1,2}, \d{4}) - (.+)$")
_FLIGHT_NO = re.compile(r"^(.*?\S)\s+([A-Z0-9]{2}) ?(\d{1,4})$")


def _dt(text):
    return datetime.strptime(text, "%I:%M %p, %b %d, %Y")


def _sections(text):
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    traveler = None
    for ln in lines:
        m = re.match(r"^Traveler (.+?) Trip ", ln)
        if m:
            if traveler is not None:
                raise ItineraryError("more than one Traveler line")
            traveler = m.group(1).strip()
    if not traveler:
        raise ItineraryError("no Traveler line")
    sections, cur = [], None
    for ln in lines:
        h = _HEADING.match(ln)
        if h:
            cur = {"heading_date": datetime.strptime(h.group(1), "%B %d, %Y").date(),
                   "kind": h.group(2), "fields": {}}
            sections.append(cur)
            continue
        f = _FIELD.match(ln)
        if cur is not None and f:
            if f.group(1) in cur["fields"]:
                raise ItineraryError(f"duplicate {f.group(1)} in one section")
            cur["fields"][f.group(1)] = f.group(2).strip()
    return traveler, sections


def _need(sec, name):
    v = sec["fields"].get(name)
    if not v:
        raise ItineraryError(f"{sec['kind']} section on {sec['heading_date']} lacks {name}")
    return v


def _terminals(text):
    out = {}
    for part in text.split(";"):
        m = re.match(r"^\s*([A-Z]{3})\s+(.*\S)\s*$", part)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def _flight(sec):
    fm = _FLIGHT_NO.match(_need(sec, "Flight"))
    dep = _FLIGHT_POINT.match(_need(sec, "Departure"))
    arr = _FLIGHT_POINT.match(_need(sec, "Arrival"))
    if not (fm and dep and arr):
        raise ItineraryError(f"unreadable Flight section on {sec['heading_date']}")
    terms = _terminals(sec["fields"].get("Terminal", ""))
    f = {"confirmation": _need(sec, "Confirmation"), "airline": fm.group(1),
         "flight_no": f"{fm.group(2)} {fm.group(3)}", "dep_iata": dep.group(1), "dep_city": dep.group(2),
         "dep": _dt(dep.group(3)), "arr_iata": arr.group(1), "arr_city": arr.group(2),
         "arr": _dt(arr.group(3)), "dep_terminal": terms.get(dep.group(1), ""),
         "arr_terminal": terms.get(arr.group(1), "")}
    if f["dep"].date() != sec["heading_date"]:
        raise ItineraryError(f"Flight heading date {sec['heading_date']} ≠ departure {f['dep'].date()}")
    return f


def _car(sec):
    d = _CAR_DEPART.match(_need(sec, "Departure"))
    if not d:
        raise ItineraryError(f"unreadable Other Service Departure on {sec['heading_date']}")
    c = {"service": _need(sec, "Service"), "confirmation": _need(sec, "Confirmation"),
         "dep": _dt(d.group(1)), "route": _need(sec, "Route")}
    if c["dep"].date() != sec["heading_date"]:
        raise ItineraryError(f"Other Service heading date {sec['heading_date']} ≠ departure {c['dep'].date()}")
    return c


def parse(text):
    """→ {traveler, trip: 'round'|'oneway', out, ret, out_car, ret_car} or ItineraryError."""
    traveler, sections = _sections(text)
    flights = [_flight(s) for s in sections if s["kind"] == "Flight"]
    cars = [_car(s) for s in sections if s["kind"] == "Other Service"]
    if len(flights) == 1:
        out, ret = flights[0], None
    elif len(flights) == 2:
        out, ret = flights
        if ret["dep_iata"] not in KR_AIRPORTS or ret["arr_iata"] in KR_AIRPORTS:
            raise ItineraryError("second flight is not KR → US")
        if out["confirmation"] != ret["confirmation"]:
            raise ItineraryError("round-trip with different Flight Confirmations (v1.1)")
    else:
        raise ItineraryError(f"{len(flights)} Flight sections; v1 supports 1 or 2 direct flights")
    if out["dep_iata"] in KR_AIRPORTS or out["arr_iata"] not in KR_AIRPORTS:
        raise ItineraryError("first flight is not US → ICN/GMP (KR → US one-way is v1.1)")
    for f in flights:
        if (f["arr"].date() - f["dep"].date()).days not in (0, 1, 2):
            raise ItineraryError(f"{f['flight_no']} arrives outside 0–2 days after departure (§8.1)")
    if ret and ret["dep"] <= out["arr"]:
        raise ItineraryError("return departs before outbound arrives")
    out_car = [c for c in cars if c["dep"] < out["dep"]]
    ret_car = [c for c in cars if ret and c["dep"] > ret["arr"]]   # US local vs US local
    if len(out_car) != 1 or len(ret_car) != (1 if ret else 0) or len(cars) != len(out_car) + len(ret_car):
        raise ItineraryError("expected exactly one US car before the outbound flight"
                             + (" and one after the return flight" if ret else ""))
    return {"traveler": traveler, "trip": "round" if ret else "oneway", "out": out, "ret": ret,
            "out_car": out_car[0], "ret_car": ret_car[0] if ret else None}


def canonical(itin):
    """Stable JSON-able form for the job hash and state."""
    def enc(v):
        if isinstance(v, dict):
            return {k: enc(x) for k, x in sorted(v.items())}
        if isinstance(v, datetime):
            return v.strftime("%Y-%m-%dT%H:%M")
        return v
    return enc(itin)
