"""Build a render-ready DispatchRecord from a TravelMemo + direction + notes."""
from dispatch_agent import config
from dispatch_agent.records import DispatchRecord, dispatch_time_and_basis


def build_record(memo, direction: str, notes: str = "") -> DispatchRecord:
    if direction == "sendoff":
        leg = memo.korea_departure
        time, basis = dispatch_time_and_basis("sendoff", leg.flight_no, dep_time=leg.depart)
        origin = config.HOTEL
        destination = config.airport_address(leg.from_code, leg.terminal)
    elif direction == "pickup":
        leg = memo.korea_arrival
        time, basis = dispatch_time_and_basis("pickup", leg.flight_no, arr_time=leg.arrive)
        origin = config.airport_address(leg.to_code, leg.terminal)
        destination = config.HOTEL
    else:
        raise ValueError(f"unknown direction: {direction!r}")

    return DispatchRecord(
        direction=direction,
        date=leg.date.strftime("%Y.%m.%d"),
        passengers=memo.passenger,
        dispatch_time=time,
        time_basis=basis,
        origin=origin,
        destination=destination,
        notes=notes or "N/A",
    )
