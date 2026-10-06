"""Pattern discovery.

Protocol (designed so the search cannot fool itself):

1. SEARCH on the in-sample period only (the oldest 70% of history):
   a. exhaustive single conditions and pairs (vectorised co-occurrence matrices),
   b. beam search extending the best pairs to 3 and 4 conditions,
   c. a decision tree on the condition matrix (finds rules incl. "NOT x"),
   d. k-means clustering of continuous market descriptors; each profitable
      cluster is explained by the conditions most over-represented in it.
   Every candidate is an explainable AND of named conditions.
2. SELECT a diverse shortlist (near-duplicate event sets removed).
3. CONFIRM each shortlisted candidate on the untouched out-of-sample period,
   with occurrences de-duplicated (cooldown) and a Benjamini-Hochberg
   correction across the shortlist. Only candidates that hold up there are
   "validated"; the rest are reported as "promising" or "failed".
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import stats as sps

from ..config import DEFAULT_HORIZON, DEFAULT_STOP_ATR
from ..features.engine import get_features
from ..research.conditions import BY_ID, label_of
from ..research.hypothesis import WARMUP, condition_matrix, dedupe, point_size, split_index
from ..research.outcomes import get_outcomes
from ..research.stats import evidence_score, summarize

SHRINK_BARS = 150      # pseudo-bars pulling small supports toward the baseline during search
BEAM = 40
SHORTLIST = 40
CHUNK = 60000
FDR_Q = 0.10

CLUSTER_FEATURES = ["rsi", "pd_pos", "atr_pct", "roc10_atr", "body_ratio", "upper_wick_ratio", "lower_wick_ratio",
                    "range20_atr", "dist_sh_atr", "dist_sl_atr", "streak", "h4_pd_pos", "h4_rsi", "h1_pd_pos", "h1_rsi",
                    "d1_pd_pos", "d1_rsi"]


@dataclass
class Candidate:
    conds: tuple[str, ...]
    source: str
    is_bars: int = 0
    is_lift: float = 0.0
    extra: dict = field(default_factory=dict)


def _bh(p: np.ndarray) -> np.ndarray:
    """Benjamini-Hochberg adjusted q-values."""
    m = len(p)
    if m == 0:
        return p
    order = np.argsort(p)
    q = np.empty(m)
    prev = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        prev = min(prev, p[i] * m / rank)
        q[i] = prev
    return q


class Discovery:
    def __init__(self, symbol: str, tf: str, direction: str, target_r: float = 2.0,
                 stop_atr: float = DEFAULT_STOP_ATR, cooldown: int = 5, min_n: int | None = None, log=None):
        self.symbol, self.tf, self.direction = symbol, tf, direction
        self.target_r, self.stop_atr, self.cooldown = target_r, stop_atr, cooldown
        self.log = log or (lambda msg: None)
        df, _ = get_features(symbol, tf, wait=True)
        self.df = df
        self.ids, self.mat = condition_matrix(symbol, tf, df)
        out = get_outcomes((symbol, tf), df, DEFAULT_HORIZON[tf], stop_atr, point_size(symbol))
        side = "L" if direction == "long" else "S"
        self.gross = out[f"{side}_r{target_r:g}"].to_numpy()
        self.y = self.gross - out["cost_r"].fillna(0).to_numpy()
        self.mfe = out[f"{side}_mfe"].to_numpy()
        self.mae = out[f"{side}_mae"].to_numpy()
        self.bars = out[f"{side}_bars"].to_numpy()
        n = len(df)
        self.valid = np.isfinite(self.y)
        self.valid[:WARMUP] = False
        self.sp = split_index(df)
        idx = np.arange(n)
        # purge: in-sample trades must finish before the split (no unseen prices in the search)
        self.is_mask = self.valid & (idx < self.sp - DEFAULT_HORIZON[tf])
        self.oos_mask = self.valid & (idx >= self.sp)
        self.base_is = float(self.y[self.is_mask].mean())
        self.base_oos = float(self.y[self.oos_mask].mean()) if self.oos_mask.any() else 0.0
        self.years_is = max((df["time"].iloc[self.sp - 1] - df["time"].iloc[WARMUP]).days / 365.25, 0.1)
        self.years_oos = max((df["time"].iloc[-1] - df["time"].iloc[self.sp]).days / 365.25, 0.1)
        # minimum occurrences: ~ at least 3 per in-sample year and never fewer than 30
        self.min_n = min_n or max(30, int(3 * self.years_is))
        self.n_tested = 0

    # ------------------------------------------------------------------ helpers
    def _lift(self, cnt, s):
        return (s + SHRINK_BARS * self.base_is) / (cnt + SHRINK_BARS) - self.base_is

    def _mask(self, conds) -> np.ndarray:
        m = np.ones(len(self.df), bool)
        col = {c: j for j, c in enumerate(self.ids)}
        for c in conds:
            v = self.mat[:, col[c.lstrip("!")]]
            m &= ~v if c.startswith("!") else v
        return m

    # ------------------------------------------------------------------ 1a/1b combination search
    def combo_search(self) -> list[Candidate]:
        k = len(self.ids)
        rows = np.flatnonzero(self.is_mask)
        y = self.y[rows].astype(np.float32)
        C = np.zeros((k, k), np.float64)
        S = np.zeros((k, k), np.float64)
        for s0 in range(0, len(rows), CHUNK):
            A = self.mat[rows[s0:s0 + CHUNK]].astype(np.float32)
            yy = y[s0:s0 + CHUNK, None]
            C += A.T @ A
            S += A.T @ (A * yy)
        min_bars = self.min_n * 1.5  # bar-level support (occurrences are de-duplicated later)
        lift = self._lift(C, S)
        np.fill_diagonal(lift, np.where(np.diag(C) >= min_bars, np.diag(lift), -np.inf))
        lift[C < min_bars] = -np.inf
        iu = np.triu_indices(k, 1)
        self.n_tested += k + len(iu[0])

        cands: list[Candidate] = []
        diag = np.diag(lift)
        for j in np.argsort(-diag)[:60]:
            if np.isfinite(diag[j]) and diag[j] > 0:
                cands.append(Candidate((self.ids[j],), "single", int(C[j, j]), float(diag[j])))
        pair_l = lift[iu]
        order = np.argsort(-pair_l)
        pairs = []
        for o in order[:400]:
            if not np.isfinite(pair_l[o]) or pair_l[o] <= 0:
                break
            i, j = iu[0][o], iu[1][o]
            pairs.append(((i, j), float(pair_l[o]), int(C[i, j])))
        for (i, j), lv, cnt in pairs[:150]:
            cands.append(Candidate((self.ids[i], self.ids[j]), "combination", cnt, lv))

        # beam: extend the best pairs to 3, then 4 conditions
        beam = [((i, j), lv) for (i, j), lv, _ in pairs[:BEAM]]
        for level in (3, 4):
            nxt = {}
            for combo, _ in beam:
                m = np.ones(len(rows), bool)
                for j in combo:
                    m &= self.mat[rows, j]
                sub_rows = np.flatnonzero(m)
                if len(sub_rows) < min_bars:
                    continue
                A = self.mat[rows[sub_rows]]
                cnt = A.sum(axis=0).astype(np.float64)
                s = y[sub_rows] @ A
                lv = self._lift(cnt, s)
                lv[cnt < min_bars] = -np.inf
                lv[list(combo)] = -np.inf
                self.n_tested += len(self.ids)
                for j in np.argsort(-lv)[:8]:
                    if np.isfinite(lv[j]) and lv[j] > 0:
                        key = tuple(sorted(combo + (int(j),)))
                        if key not in nxt or nxt[key][0] < lv[j]:
                            nxt[key] = (float(lv[j]), int(cnt[j]))
            ranked = sorted(nxt.items(), key=lambda kv: -kv[1][0])
            for key, (lv, cnt) in ranked[:80]:
                cands.append(Candidate(tuple(self.ids[j] for j in key), "combination", cnt, lv))
            beam = [(key, lv) for key, (lv, _) in ranked[:BEAM]]
            if not beam:
                break
        return cands

    # ------------------------------------------------------------------ 1c decision tree
    def tree_search(self) -> list[Candidate]:
        from sklearn.tree import DecisionTreeRegressor
        rows = np.flatnonzero(self.is_mask)
        if len(rows) > 200_000:
            rows = np.sort(np.random.default_rng(0).choice(rows, 200_000, replace=False))
        X = self.mat[rows]
        y = np.clip(self.y[rows], -1.5, self.target_r)
        leaf = max(int(self.min_n * 1.5), int(0.003 * len(rows)))
        cands = []
        for depth in (3, 5):
            t = DecisionTreeRegressor(max_depth=depth, min_samples_leaf=leaf, random_state=0).fit(X, y)
            tr = t.tree_
            self.n_tested += tr.node_count

            def walk(node, path):
                if tr.children_left[node] == -1:
                    v = float(tr.value[node][0][0])
                    pos = [p for p in path if not p.startswith("!")]
                    if pos and v - self.base_is > 0.03:
                        cands.append(Candidate(tuple(path), "decision tree", int(tr.n_node_samples[node]),
                                               v - self.base_is))
                    return
                f = self.ids[tr.feature[node]]
                walk(tr.children_left[node], path + ["!" + f])   # feature <= 0.5  -> condition false
                walk(tr.children_right[node], path + [f])         # condition true
            walk(0, [])
        return cands

    # ------------------------------------------------------------------ 1d clustering
    def cluster_search(self) -> list[Candidate]:
        from sklearn.cluster import MiniBatchKMeans
        cols = [c for c in CLUSTER_FEATURES if c in self.df.columns]
        rows = np.flatnonzero(self.is_mask)
        if len(rows) > 150_000:
            rows = np.sort(np.random.default_rng(1).choice(rows, 150_000, replace=False))
        F = self.df[cols].to_numpy(float)[rows]
        F = np.clip(np.nan_to_num(F, nan=0.0), -10, 10)
        mu, sd = F.mean(0), F.std(0) + 1e-9
        Z = (F - mu) / sd
        k = 16
        km = MiniBatchKMeans(n_clusters=k, random_state=0, n_init=3, batch_size=4096).fit(Z)
        lab = km.labels_
        y = self.y[rows]
        p_all = self.mat[rows].mean(axis=0)
        cands = []
        self.n_tested += k
        for c in range(k):
            m = lab == c
            cnt = int(m.sum())
            if cnt < self.min_n * 3:
                continue
            lv = float(self._lift(cnt, y[m].sum()))
            if lv <= 0.02:
                continue
            p_c = self.mat[rows[m]].mean(axis=0)
            lift = np.where(p_all > 0.005, p_c / np.maximum(p_all, 1e-9), 0)
            good = [(lift[j], j) for j in range(len(self.ids)) if p_c[j] >= 0.5 and lift[j] >= 1.4]
            good.sort(reverse=True)
            conds = tuple(self.ids[j] for _, j in good[:3])
            if not conds:
                continue
            centre = km.cluster_centers_[c] * sd + mu
            profile = {col: {"cluster": float(centre[i]), "overall": float(mu[i])} for i, col in enumerate(cols)}
            cands.append(Candidate(conds, "clustering", cnt, lv, {"cluster_profile": profile,
                                                                  "cluster_share": cnt / len(rows)}))
        return cands

    # ------------------------------------------------------------------ 2 + 3 shortlist and confirm
    def _stats(self, ev: np.ndarray, base: float, years: float) -> dict:
        if len(ev) == 0:
            return {"n": 0}
        return summarize(self.y[ev], self.target_r, base, years, self.mfe[ev], self.mae[ev], self.bars[ev], self.gross[ev])

    def evaluate(self, cands: list[Candidate]) -> list[dict]:
        # merge identical condition sets (keep best-ranked source label)
        uniq: dict[tuple, Candidate] = {}
        for c in cands:
            key = tuple(sorted(c.conds))
            if key not in uniq or uniq[key].is_lift < c.is_lift:
                uniq[key] = c
        ranked = sorted(uniq.values(), key=lambda c: -c.is_lift)

        # diverse shortlist: skip candidates whose in-sample events mostly overlap an accepted one
        short, sets = [], []
        for c in ranked:
            m = self._mask(c.conds) & self.is_mask
            ev = dedupe(np.flatnonzero(m), self.cooldown)
            if len(ev) < self.min_n:
                continue
            evs = set(ev.tolist())
            if any(len(evs & s) / max(1, len(evs | s)) > 0.6 for s in sets):
                continue
            short.append((c, ev))
            sets.append(evs)
            if len(short) >= SHORTLIST:
                break

        results = []
        for c, ev_is in short:
            s_is = self._stats(ev_is, self.base_is, self.years_is)
            m_all = self._mask(c.conds) & self.valid
            ev_all = dedupe(np.flatnonzero(m_all), self.cooldown)
            ev_oos = ev_all[ev_all >= self.sp]
            s_oos = self._stats(ev_oos, self.base_oos, self.years_oos)
            if s_oos.get("n", 0) >= 2:
                r = self.y[ev_oos]
                sd = r.std(ddof=1)
                t = (r.mean() - self.base_oos) / (sd / math.sqrt(len(r))) if sd > 0 else 0.0
                p_oos = float(1 - sps.t.cdf(t, df=len(r) - 1))
            else:
                p_oos = 1.0
            # walk-forward stability: share of in-sample years in which the setup beat random entry
            years = self.df["time"].dt.year.to_numpy()
            yr_rows = []
            for yv in np.unique(years[ev_is]):
                sel = ev_is[years[ev_is] == yv]
                if len(sel) >= 5:
                    yr_rows.append(self.y[sel].mean() > self.base_is)
            stability = float(np.mean(yr_rows)) if yr_rows else None
            results.append({"cand": c, "ev_all": ev_all, "is": s_is, "oos": s_oos, "p_oos": p_oos,
                            "stability": stability, "all": self._stats(ev_all, float(self.y[self.valid].mean()),
                                                                       self.years_is + self.years_oos)})
        q = _bh(np.array([r["p_oos"] for r in results])) if results else np.array([])

        out = []
        for r, qv in zip(results, q):
            c, s_is, s_oos = r["cand"], r["is"], r["oos"]
            ev = evidence_score(s_is, s_oos)
            if s_oos.get("n", 0) >= 20 and qv <= FDR_Q and s_oos["avg_r"] > 0 and s_oos["edge_r"] > 0:
                status = "validated"
            elif s_oos.get("n", 0) >= 5 and s_oos.get("edge_r", -1) > 0 and s_oos.get("avg_r", -1) > 0:
                status = "promising"
            else:
                status = "failed"
            ev = cap_evidence(ev, status)
            out.append({
                "symbol": self.symbol, "tf": self.tf, "direction": self.direction,
                "target_r": self.target_r, "stop_atr": self.stop_atr, "cooldown": self.cooldown,
                "conditions": list(c.conds), "labels": [label_of(x) for x in c.conds], "source": c.source,
                "status": status, "q_oos": float(qv), "p_oos": r["p_oos"], "stability": r["stability"],
                "score": ev["score"], "grade": ev["grade"], "evidence": ev,
                "is": s_is, "oos": s_oos, "all": r["all"],
                "extra": c.extra | {"n_tested": self.n_tested, "split_time": int(self.df["time"].iloc[self.sp].timestamp()),
                                    "min_n": self.min_n},
                "description": describe(self, c, s_is, s_oos, r["all"]),
            })
        return out

    def run(self) -> list[dict]:
        self.log(f"{self.symbol} {self.tf} {self.direction}: combination search")
        cands = self.combo_search()
        self.log(f"{self.symbol} {self.tf} {self.direction}: decision tree")
        cands += self.tree_search()
        self.log(f"{self.symbol} {self.tf} {self.direction}: clustering")
        try:
            cands += self.cluster_search()
        except Exception as e:  # clustering is optional; never fail the run because of it
            self.log(f"clustering skipped: {e}")
        self.log(f"{self.symbol} {self.tf} {self.direction}: confirming {len(cands)} candidates out-of-sample")
        return self.evaluate(cands)


def cap_evidence(ev: dict, status: str) -> dict:
    """A grade must reflect the whole validation: candidates that did not survive the
    multiple-testing-corrected out-of-sample check cannot be graded above C (promising) or F (failed)."""
    cap = {"validated": 100, "promising": 54, "failed": 14}[status]
    if ev["score"] <= cap:
        return ev
    score = cap
    grade = "A" if score >= 75 else "B" if score >= 55 else "C" if score >= 35 else "D" if score >= 15 else "F"
    note = ("Capped: not yet confirmed after correcting for the number of candidates tested."
            if status == "promising" else "Capped: did not hold up on unseen data.")
    return ev | {"score": score, "grade": grade, "reasons": ev["reasons"] + [note]}


def describe(d: Discovery, c: Candidate, s_is: dict, s_oos: dict, s_all: dict) -> str:
    conds = [label_of(x) for x in c.conds]
    when = conds[0] if len(conds) == 1 else ", ".join(conds[:-1]) + " and " + conds[-1]
    act = "buying" if d.direction == "long" else "selling"
    txt = (f"When {when} on {d.symbol} {d.tf}, {act} at the next candle with a {d.stop_atr:g}×ATR stop and a "
           f"{d.target_r:g}R target returned {s_all.get('avg_r', 0):+.2f}R per trade over {s_all.get('n', 0)} occurrences "
           f"(win rate {s_all.get('win_rate', 0) * 100:.0f}%, breakeven {100 / (1 + d.target_r):.0f}%). ")
    if s_oos.get("n"):
        txt += (f"On data the search never saw ({s_oos['n']} occurrences) it returned {s_oos['avg_r']:+.2f}R per trade "
                f"versus {s_oos.get('baseline_r', 0):+.2f}R for random entries.")
    if c.source == "clustering":
        txt += " Found by clustering similar market situations; the conditions above describe what that cluster has in common."
    elif c.source == "decision tree":
        txt += " Found by a decision tree; NOT-conditions mark situations the tree learned to exclude."
    return txt
