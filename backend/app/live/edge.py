"""How strong each watched setup's edge is NOW, and notifications when that changes.

Used to order the watchlist (strongest current edge first) and to tell the user when a setup slips from
healthy to weakening / broken / underperforming in its forward test, or recovers.
"""
from __future__ import annotations

from . import db, notify

EDGE_SHRINK = 10   # recent trades needed before the recent average counts at half weight
TIER_TEXT = {3: "healthy", 2: "weakening", 1: "broken / underperforming", 0: "paused"}


def edge_health(w: dict, verdicts: dict) -> dict:
    """tier 3 healthy, 2 weakening, 1 broken or underperforming in the forward test, 0 paused;
    score = recent R per trade (last third of trades) shrunk toward 0 for few trades."""
    s = w.get("stats") or {}
    d = s.get("decay") or {}
    oos = (s.get("stats") or {}).get("out_of_sample") or {}
    if d.get("recent_avg_r") is not None:
        now, was, n = d["recent_avg_r"], d.get("earlier_avg_r"), d.get("recent_n") or 0
    else:
        now, was, n = oos.get("avg_r"), None, oos.get("n") or 0
    status = d.get("status") or "unknown"
    verdict = verdicts.get(str(w["id"]))
    if not w["active"]:
        tier, why = 0, "paused"
    elif status == "broken" or verdict == "underperforming":
        tier, why = 1, "forward test underperforming" if verdict == "underperforming" else "edge broken (recent trades lose)"
    elif status == "weakening":
        tier, why = 2, "edge weakening (recent R under half of before)"
    else:
        tier, why = 3, status
    score = (now or 0) * n / (n + EDGE_SHRINK) if n else 0.0
    return {"tier": tier, "score": score, "now": now, "was": was, "n": n, "status": status, "verdict": verdict, "why": why}


def verdicts() -> dict:
    return (db.get_setting("scoreboard_verdicts") or {}).get("verdicts", {})


def ranked() -> list[dict]:
    rows = db.list_watches()
    v = verdicts()
    for w in rows:
        w["edge"] = edge_health(w, v)
    rows.sort(key=lambda w: (-w["edge"]["tier"], -w["edge"]["score"], w["id"]))
    for k, w in enumerate(rows):
        w["edge"]["rank"] = k + 1
    return rows


def check_changes() -> list[dict]:
    """Notify when an active watch moves between healthy / weakening / broken. The first run only records."""
    known = db.get_setting("edge_tiers")
    rows = ranked()
    now_tiers = {str(w["id"]): w["edge"]["tier"] for w in rows}
    out = []
    if known is not None:
        for w in rows:
            old, new = known.get(str(w["id"])), w["edge"]["tier"]
            if old is None or old == new or 0 in (old, new):   # new, paused or resumed: not an edge change
                continue
            e = w["edge"]
            worse = new < old
            title = f"{'⚠ Edge ' + ('weakening' if new == 2 else 'broken') if worse else '✓ Edge recovered'}: #{w['id']} {w['symbol']} {w['tf']}"
            body = (f"{w['name']} — now {TIER_TEXT[new]} (was {TIER_TEXT[old]}). {e['why'][0].upper() + e['why'][1:]}. "
                    f"Recent R per trade {e['now']:+.2f}" + (f" vs {e['was']:+.2f} before." if e["was"] is not None else ".")
                    + (" It has moved down the watchlist; consider pausing it." if worse else " It has moved back up the watchlist."))
            db.add_notification("edge", title, body, symbol=w["symbol"], tf=w["tf"], ref_id=w["id"],
                                link={"page": "scanner"}, level="warn" if worse else "info")
            if worse and notify.configured():
                try:
                    notify.send(f"<b>{title}</b>\n{body}")
                except Exception:
                    pass
            out.append(w)
    db.set_setting("edge_tiers", now_tiers)
    return out
