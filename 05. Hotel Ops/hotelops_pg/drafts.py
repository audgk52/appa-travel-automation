"""Kakao / email drafts — truthful-minimum content (PRD §18, §19).

Exact tone/style is NOT an architecture concern (Myungha UAT owns it). PG's
contract is truthful-minimum content: traveler / affected stay, the VERIFIED
requested/applied change, and the requested-vs-hotel-confirmed status (§19). PG
never implies hotel confirmation that has not occurred; drafts describe the known
status only, and PG never treats its own prose as a correctness oracle.
"""
from hotelops_pg import fields
from hotelops_pg.history import _LABELS


def _describe(deltas) -> str:
    business = [d for d in deltas if d.field not in (fields.NTF_HISTORY, fields.NIGHTS)]
    return "; ".join(f"{_LABELS.get(d.field, d.field)} {d.old or '∅'} → {d.new}" for d in business)


def _status_line(hotel_confirmed: bool) -> str:
    # Requested ≠ hotel-confirmed (§19): never imply confirmation that hasn't happened.
    return ("상태: 호텔 확정 완료" if hotel_confirmed
            else "상태: 요청 사항 (호텔 확정 전 / awaiting hotel confirmation)")


def kakao_draft(applied: dict, records_by_id: dict, hotel_confirmed: bool = False) -> str:
    """A concise Kakao draft from the VERIFIED applied per-record deltas (§19)."""
    lines = ["[Rooming List 변경 요청]"]
    for rid, deltas in applied.items():
        rec = records_by_id.get(rid)
        who = rec.get(fields.NAME) if rec else rid
        desc = _describe(deltas)
        if desc:
            lines.append(f"- {who}: {desc}")
    lines.append(_status_line(hotel_confirmed))
    return "\n".join(lines)


def email_draft(applied: dict, records_by_id: dict, hotel_confirmed: bool = False) -> str:
    """A slightly fuller email draft with the same truthful-minimum content (§19)."""
    body = ["안녕하세요, 지배인님.", "", "아래와 같이 Rooming List 변경을 요청드립니다.", ""]
    for rid, deltas in applied.items():
        rec = records_by_id.get(rid)
        who = rec.get(fields.NAME) if rec else rid
        desc = _describe(deltas)
        if desc:
            body.append(f"  • {who}: {desc}")
    body += ["", _status_line(hotel_confirmed), "", "감사합니다."]
    return "\n".join(body)
