"""Pooled execution study.

Testing ~70 exit/entry plans on one setup with 50-200 trades mostly selects
noise (verified on real data). Instead each plan is applied to MANY setups
(the validated + promising discovered patterns) and compared with the baseline
plan on each setup's out-of-sample period. A rule is only trusted when it
improves unseen results consistently across setups.
"""
from __future__ import annotations

import time
from collections import defaultdict

import numpy as np

from ..discovery import store as pstore
from ..live import db
from .optimize import ExecutionLab
from .simulate import BASELINE, Plan

KEY = "execution_study"
# Setups chosen by DEFINITION, not by past results. Discovered patterns were selected
# under the baseline exit, which biases any comparison toward that baseline.
CONCEPTS = {
    "long": ["fvg_bull_retest", "ob_bull_retest", "sweep_low", "sweep_pdl", "sweep_asia_low",
             "choch_bull", "bos_bull", "div_bull", "bull_engulf", "hammer"],
    "short": ["fvg_bear_retest", "ob_bear_retest", "sweep_high", "sweep_pdh", "sweep_asia_high",
              "choch_bear", "bos_bear", "div_bear", "bear_engulf", "shooting_star"],
}
ROBUST_SHARE = 0.6     # plan must beat the baseline out-of-sample in >= 60% of setups
ROBUST_GAIN = 0.03     # ... and by at least +0.03R per signal on average


def concept_setups(tfs=("H1", "H4")) -> list[dict]:
    from ..config import SYMBOLS
    from ..data.store import bars_path
    out = []
    for s in SYMBOLS:
        for tf in tfs:
            if not bars_path(s, tf).exists():
                continue
            for d, conds in CONCEPTS.items():
                out.extend({"id": f"{c}", "symbol": s, "tf": tf, "direction": d, "conditions": [c], "cooldown": 5} for c in conds)
    return out


def run_study(max_patterns: int = 80, progress=None, source: str = "concepts") -> dict:
    if source == "concepts":
        return _run(concept_setups(), progress, source)
    pats = pstore.list_patterns(statuses=["validated", "promising"], limit=1000)
    # keep the study diverse: at most 6 patterns per market/direction, best first
    per, chosen = defaultdict(int), []
    for p in pats:
        k = (p["symbol"], p["tf"], p["direction"])
        if per[k] < 6:
            per[k] += 1
            chosen.append(p)
        if len(chosen) >= max_patterns:
            break
    return _run(chosen, progress, source)


def _run(chosen: list[dict], progress, source: str) -> dict:
    chosen = sorted(chosen, key=lambda p: (p["symbol"], p["tf"]))  # reuse loaded tables
    rows = defaultdict(list)  # plan key -> list of per-pattern dicts
    used = 0
    for k, p in enumerate(chosen):
        if progress:
            progress(k, len(chosen), f"{p['symbol']} {p['tf']} {p['direction']} #{p['id']}")
        try:
            lab = ExecutionLab(p["symbol"], p["tf"], p["conditions"], p["direction"], p["cooldown"])
            from .optimize import plan_grid
            res = {pl.key: lab.run_plan(pl) for pl in plan_grid(lab.ltf is not None)}
        except Exception:
            continue
        base = res[BASELINE.key]
        b_is, b_oos = base["is"].get("per_signal_r"), base["oos"].get("per_signal_r")
        if b_is is None or b_oos is None or base["oos"].get("signals", 0) < 10:
            continue
        used += 1
        for key, r in res.items():
            if r["is"].get("per_signal_r") is None or r["oos"].get("per_signal_r") is None:
                continue
            rows[key].append({
                "pattern": p["id"], "symbol": p["symbol"], "tf": p["tf"],
                "d_is": r["is"]["per_signal_r"] - b_is, "d_oos": r["oos"]["per_signal_r"] - b_oos,
                "wr_oos": r["oos"].get("win_rate"), "wr_base": base["oos"].get("win_rate"),
            })
    plans = []
    for key, lst in rows.items():
        d_oos = np.array([x["d_oos"] for x in lst])
        d_is = np.array([x["d_is"] for x in lst])
        wr = np.array([(x["wr_oos"] or 0) - (x["wr_base"] or 0) for x in lst])
        share = float((d_oos > 0).mean()) if key != BASELINE.key else 0.0
        plans.append({
            "key": key, "description": Plan.from_key(key).describe(), "setups": len(lst),
            "mean_d_oos": float(d_oos.mean()), "median_d_oos": float(np.median(d_oos)), "share_improved": share,
            "mean_d_is": float(d_is.mean()), "mean_d_winrate": float(wr.mean()),
            "robust": bool(key != BASELINE.key and share >= ROBUST_SHARE and d_oos.mean() >= ROBUST_GAIN),
        })
    plans.sort(key=lambda r: -r["mean_d_oos"])
    # component view: which ingredient helps on average
    comps = {}
    for part, idx in (("entry", 0), ("stop", 1), ("target", 2), ("manage", 3)):
        g = defaultdict(list)
        for pl in plans:
            g[pl["key"].split("|")[idx]].append(pl["mean_d_oos"])
        comps[part] = {k: float(np.mean(v)) for k, v in g.items()}
    out = {"finished": int(time.time()), "setups": used, "source": source, "plans": plans, "components": comps,
           "robust": [p for p in plans if p["robust"]],
           "rule": f"A plan is trusted only if it beats the baseline out-of-sample in at least {ROBUST_SHARE:.0%} of setups "
                   f"and by at least {ROBUST_GAIN:+.2f}R per signal on average."}
    db.set_setting(KEY, out)
    return out


def latest() -> dict | None:
    return db.get_setting(KEY)


def recommend(per_setup: dict) -> dict:
    """Recommend a plan for one setup: only a globally robust plan that also did not hurt this setup out-of-sample."""
    study = latest()
    robust = {p["key"]: p for p in (study or {}).get("robust", [])}
    base_oos = per_setup["baseline"]["oos"].get("per_signal_r")
    best = None
    for r in per_setup["ranked"] + [per_setup["best"]]:
        if r["key"] in robust and base_oos is not None and (r["oos"].get("per_signal_r") or -9) >= base_oos:
            if best is None or (r["is_score"] or -9) > (best["is_score"] or -9):
                best = r
    if best is None:
        if not study:
            why = "Run the pooled execution study first; until then the baseline is recommended."
        elif not robust:
            why = "No execution change has proven robust across setups yet - keep the baseline."
        else:
            why = ("The plans that proved robust across setups did not help this particular setup on unseen data, "
                   "so the baseline is kept.")
        return {"key": BASELINE.key, "description": BASELINE.describe(), "reason": why}
    return {"key": best["key"], "description": best["description"],
            "reason": "Robust across many setups and did not hurt this setup on unseen data."}
