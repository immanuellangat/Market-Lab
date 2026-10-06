"""Discovery must run end-to-end and must not 'validate' patterns in pure noise."""
from __future__ import annotations

from .test_core import datadir, synth_m5, write_all  # noqa: F401


def test_discovery_on_noise_validates_nothing(datadir):
    from app.discovery.search import Discovery
    write_all(synth_m5(days=400, seed=21))
    d = Discovery("EURUSD", "H1", "long")
    pats = d.run()
    assert d.n_tested > 1000
    assert pats, "shortlist should not be empty"
    for p in pats:
        assert p["conditions"] and p["status"] in ("validated", "promising", "failed")
        assert p["description"]
    # random-walk data has no edge: (almost) nothing may survive out-of-sample confirmation
    assert sum(p["status"] == "validated" for p in pats) <= 1


def test_rare_events_runs(datadir):
    from app.discovery.rare import rare_now
    write_all(synth_m5(days=200, seed=4))
    r = rare_now("EURUSD", "H1")
    assert "events" in r
    for e in r["events"]:
        assert e["n"] >= 5 and 0 <= e["freq"] < 0.02 or e["kind"] == "statistical extreme"
