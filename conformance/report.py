"""Declared vs actual reporting.

Reporting rules, chosen so the numbers survive a hostile read:

1. Inconclusive results are NEVER counted as failures.
2. `unrepresentable` is reported on its own line and is NEVER inside the
   silent-failure rate. The silent rate is the kill criterion; contaminating
   it with results where the constraint was never sent would bias the decision
   toward the project's own survival.
3. Silent and honest failures are never summed into one headline.
4. Every percentage carries its numerator and denominator.
5. Trial-level and deployment-level rates are reported separately. "4% of
   trials" and "13% of deployments" mean very different things -- failures
   concentrated in two deployments are far more actionable than a uniform
   low-level error rate.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from typing import Any

from . import outcomes as O

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
RED, YELLOW, GREEN, CYAN, MAG = "\033[31m", "\033[33m", "\033[32m", "\033[36m", "\033[35m"

_MARK = {
    O.DIV_SILENT: (RED, "SILENT"),
    O.DIV_UNREPRESENTABLE: (MAG, "UNREPRESENTABLE"),
    O.DIV_ADVISORY: (CYAN, "advisory"),
    O.DIV_HONEST: (YELLOW, "honest"),
    O.DIV_UNDECLARED: (CYAN, "undeclared"),
    O.DIV_NONE: (GREEN, "match"),
    O.DIV_UNKNOWN: (DIM, "inconclusive"),
}

_CONF_MARK = {
    O.CONF_HONORED: (GREEN, "HONORED"),
    O.CONF_ADVISORY: (CYAN, "advisory leak"),
    O.CONF_SILENT: (RED, "SILENT FAILURE"),
    O.CONF_UNREPRESENTABLE: (MAG, "UNREPRESENTABLE"),
    O.CONF_HONEST: (YELLOW, "honest refusal"),
    O.CONF_INCONCLUSIVE: (DIM, "inconclusive"),
}
_CONF_SEV = {O.CONF_SILENT: 0, O.CONF_UNREPRESENTABLE: 1, O.CONF_HONEST: 2,
             O.CONF_ADVISORY: 3, O.CONF_HONORED: 4, O.CONF_INCONCLUSIVE: 5}

KILL_THRESHOLD = 0.02   # silent-failure rate below this => P1 false


def load_results(path: str) -> list[dict[str, Any]]:
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _pct(num: int, den: int) -> str:
    return f"{num/den:.1%} ({num}/{den})" if den else f"n/a (0/0)"


def _key(r: dict) -> tuple:
    dep = r.get("deployment") or {}
    return (dep.get("deployment_id", r["provider"]), r["capability"])


def render(results: list[dict[str, Any]], color: bool = True) -> str:
    def c(code: str, text: str) -> str:
        return f"{code}{text}{RESET}" if color else text

    out: list[str] = []
    add = out.append
    add("")
    add(c(BOLD, "═══ DECLARED vs ACTUAL ═══"))

    # ── group into deployment x capability cells ────────────────────────────
    cells: dict[tuple, list[dict]] = defaultdict(list)
    for r in results:
        cells[_key(r)].append(r)

    labels: dict[str, str] = {}
    for r in results:
        dep = r.get("deployment") or {}
        did = dep.get("deployment_id", r["provider"])
        if did not in labels:
            bits = [dep.get("provider", r["provider"]), dep.get("model_id", r["model_requested"])]
            if dep.get("adapter"):
                bits.append(f"via={dep['adapter']}")
            if dep.get("proxy_path") and dep["proxy_path"] != "direct":
                bits.append(f"proxy={dep['proxy_path']}")
            labels[did] = " ".join(bits)

    add("")
    for did in sorted({k[0] for k in cells}, key=lambda d: labels.get(d, d)):
        add(c(BOLD, f"── {labels.get(did, did)} ──"))
        snaps = {r.get("model_reported") for k, rs in cells.items() if k[0] == did
                 for r in rs if r.get("model_reported")}
        if snaps:
            add(c(DIM, f"   resolved snapshot: {', '.join(sorted(map(str, snaps)))}"))
        add(c(DIM, f"   deployment_id: {did}"))

        for (d2, capability), rs in sorted(cells.items(), key=lambda kv: kv[0][1]):
            if d2 != did:
                continue
            conclusive = [r for r in rs if r["outcome"] not in O.INCONCLUSIVE]
            counts = Counter(O.conformance_class(r["outcome"]) for r in conclusive)
            declared = rs[0]["declared"]
            add(c(DIM, f"   mechanism: {rs[0]['mechanism']}"))
            worst = min(counts, key=lambda k: _CONF_SEV.get(k, 9)) if counts else O.CONF_INCONCLUSIVE
            col, lab = _CONF_MARK[worst]
            breakdown = ", ".join(f"{k}={v}" for k, v in counts.most_common()) or "no conclusive trials"
            dtxt = {True: "true", False: "false", None: "NO REGISTRY ROW"}.get(declared, str(declared))
            add(f"   {capability:<19} declared={dtxt:<15} "
                f"trials={len(rs)}  {c(col, lab)}   [{breakdown}]")
            shown = set()
            for r in rs:
                if r["divergence"] in (O.DIV_SILENT, O.DIV_UNREPRESENTABLE, O.DIV_HONEST):
                    sig = r["detail"][:80]
                    if sig in shown:
                        continue
                    shown.add(sig)
                    add(c(DIM, f"      trial {r['trial_index']}: {r['detail'][:250]}"))
                    for v in r["violations"][:3]:
                        add(c(DIM, f"         - {v[:200]}"))
        add("")

    # ── rates ───────────────────────────────────────────────────────────────
    conclusive = [r for r in results if r["outcome"] not in O.INCONCLUSIVE]
    n_trials = len(conclusive)
    silent_t = sum(1 for r in conclusive if r["divergence"] == O.DIV_SILENT)
    unrep_t = sum(1 for r in conclusive if r["divergence"] == O.DIV_UNREPRESENTABLE)
    honest_t = sum(1 for r in conclusive if r["divergence"] == O.DIV_HONEST)
    match_t = sum(1 for r in conclusive if r["divergence"] == O.DIV_NONE)
    undecl_t = sum(1 for r in conclusive if r["divergence"] == O.DIV_UNDECLARED)

    cell_state: dict[tuple, set] = defaultdict(set)
    for r in conclusive:
        cell_state[_key(r)].add(r["divergence"])
    n_cells = len(cell_state)
    silent_c = sum(1 for v in cell_state.values() if O.DIV_SILENT in v)
    unrep_c = sum(1 for v in cell_state.values() if O.DIV_UNREPRESENTABLE in v)
    honest_c = sum(1 for v in cell_state.values() if O.DIV_HONEST in v)

    deps = {k[0] for k in cell_state}
    dep_silent = {k[0] for k, v in cell_state.items() if O.DIV_SILENT in v}

    add(c(BOLD, "── Rates ──"))
    add(c(DIM, "   every percentage carries numerator/denominator"))
    add("")
    add(c(BOLD, "   A. CAPABILITY CONFORMANCE") + c(DIM, "  -- what the endpoint did."))
    add(c(DIM, "      Independent of any registry. An endpoint that accepts a parameter"))
    add(c(DIM, "      and ignores it has failed its own contract whether or not a"))
    add(c(DIM, "      registry has a row for it."))
    add("")
    cc = Counter(O.conformance_class(r["outcome"]) for r in conclusive)
    add(f"     {c(GREEN,'honored')}                 {_pct(cc.get(O.CONF_HONORED,0), n_trials)}")
    add(f"     {c(RED,'SILENT FAILURE')}          {_pct(cc.get(O.CONF_SILENT,0), n_trials)}  "
        + c(DIM, "accepted the parameter, ignored it"))
    add(f"     {c(MAG,'UNREPRESENTABLE')}         {_pct(cc.get(O.CONF_UNREPRESENTABLE,0), n_trials)}  "
        + c(DIM, "constraint could not be expressed; never sent"))
    add(f"     {c(CYAN,'advisory leak')}           {_pct(cc.get(O.CONF_ADVISORY,0), n_trials)}  "
        + c(DIM, "structural constraints held; advisory keyword unenforced"))
    add(f"     {c(YELLOW,'honest refusal')}          {_pct(cc.get(O.CONF_HONEST,0), n_trials)}")

    cell_conf: dict[tuple, set] = defaultdict(set)
    for r in conclusive:
        cell_conf[_key(r)].add(O.conformance_class(r["outcome"]))
    n_cells2 = len(cell_conf)
    silent_cells = sum(1 for v in cell_conf.values() if O.CONF_SILENT in v)
    dep_sil = {k[0] for k, v in cell_conf.items() if O.CONF_SILENT in v}
    all_deps = {k[0] for k in cell_conf}
    add("")
    add(c(BOLD, "   CELL level") + c(DIM, "  (deployment x capability; fails if ANY trial failed)"))
    add(f"     {c(RED,'≥1 silent failure')}       {_pct(silent_cells, n_cells2)}")
    add(f"     {c(MAG,'unrepresentable')}         "
        f"{_pct(sum(1 for v in cell_conf.values() if O.CONF_UNREPRESENTABLE in v), n_cells2)}")
    add(c(BOLD, "   DEPLOYMENT level"))
    add(f"     {c(RED,'≥1 silent failure')}       {_pct(len(dep_sil), len(all_deps))}")

    add("")
    add(c(BOLD, "   B. REGISTRY DIVERGENCE") + c(DIM, "  -- declared vs observed."))
    add(c(DIM, "      Only computable where the registry actually makes a claim."))
    add("")
    claimed = [r for r in conclusive if r["declared"] is not None]
    unclaimed = [r for r in conclusive if r["declared"] is None]
    nd = len(claimed)
    if nd:
        dc = Counter(r["divergence"] for r in claimed)
        add(f"     declared == actual      {_pct(dc.get(O.DIV_NONE,0), nd)}")
        add(f"     {c(RED,'SILENT divergence')}       {_pct(dc.get(O.DIV_SILENT,0), nd)}")
        add(f"     {c(MAG,'UNREPRESENTABLE')}         {_pct(dc.get(O.DIV_UNREPRESENTABLE,0), nd)}")
        add(f"     {c(YELLOW,'honest divergence')}       {_pct(dc.get(O.DIV_HONEST,0), nd)}")
        add(f"     {c(CYAN,'undeclared support')}      {_pct(dc.get(O.DIV_UNDECLARED,0), nd)}")
    else:
        add(c(DIM, "     no comparable trials -- the registry makes no claim about any"))
        add(c(DIM, "     deployment in this sample."))
    if unclaimed:
        add("")
        add(f"     {c(YELLOW,'NO REGISTRY ROW')}         {_pct(len(unclaimed), n_trials)} of conclusive trials")
        add(c(DIM, "     The registry is silent about these deployments. That is a"))
        add(c(DIM, "     coverage gap, not a clean bill of health -- and it is itself"))
        add(c(DIM, "     evidence about registry adequacy."))

    silent_t = cc.get(O.CONF_SILENT, 0)
    unrep_t = cc.get(O.CONF_UNREPRESENTABLE, 0)
    deps = all_deps
    dep_silent = dep_sil
    n_cells = n_cells2

    incon = [r for r in results if r["outcome"] in O.INCONCLUSIVE]
    if incon:
        add("")
        add(c(BOLD, "── Inconclusive (excluded from every rate above) ──"))
        for reason, count in Counter(r["outcome"] for r in incon).most_common():
            add(f"   {reason:<26} {count}")

    # ── partial-conformance detail ──────────────────────────────────────────
    partial = {k: v for k, v in cell_conf.items() if len(v) > 1}
    if partial:
        add("")
        add(c(BOLD, "── Non-deterministic cells ──"))
        add(c(DIM, "   same deployment+capability produced different outcomes across trials;"))
        add(c(DIM, "   this is why single-shot results are inadmissible"))
        for (did, cap), states in partial.items():
            rs = cells[(did, cap)]
            cnt = Counter(O.conformance_class(r["outcome"]) for r in rs
                          if r["outcome"] not in O.INCONCLUSIVE)
            add(f"   {labels.get(did, did)} / {cap}: "
                + ", ".join(f"{k}×{v}" for k, v in cnt.most_common()))

    # ── verdict ─────────────────────────────────────────────────────────────
    add("")
    add(c(BOLD, "── P1 kill criterion ──"))
    add(c(DIM, f"   rule: silent-failure rate below {KILL_THRESHOLD:.0%} => P1 false, stop"))
    add(c(DIM, "   computed from CAPABILITY CONFORMANCE (section A), not registry"))
    add(c(DIM, "   divergence -- a deployment absent from the registry must not be"))
    add(c(DIM, "   scored as conforming."))
    if not n_trials:
        add("   UNDETERMINED - no conclusive trials.")
    else:
        rate = silent_t / n_trials
        add(f"   observed trial-level silent-failure rate: {_pct(silent_t, n_trials)}")
        if rate < KILL_THRESHOLD:
            add(c(YELLOW, f"   BELOW THRESHOLD on this sample."))
            add(c(DIM, "   Severity weighting may argue for continuing above the floor;"))
            add(c(DIM, "   it may not be used to rescue a result below it."))
        else:
            add(c(RED, "   ABOVE THRESHOLD on this sample -- P1 survives here."))
        add(c(DIM, f"   sample: {len(deps)} deployment(s), {n_cells} capability cell(s). "))
        if len(deps) < 15:
            add(c(YELLOW, f"   NOT ADMISSIBLE as an E1 result: E1 requires >=15 deployment"))
            add(c(YELLOW, f"   configurations; this sample has {len(deps)}."))
    add("")
    return "\n".join(out)


def summary_dict(results: list[dict[str, Any]]) -> dict[str, Any]:
    conclusive = [r for r in results if r["outcome"] not in O.INCONCLUSIVE]
    counts = Counter(r["divergence"] for r in conclusive)
    conf = Counter(O.conformance_class(r["outcome"]) for r in conclusive)
    cell_state: dict[tuple, set] = defaultdict(set)
    for r in conclusive:
        cell_state[_key(r)].add(O.conformance_class(r["outcome"]))
    n = len(conclusive)
    return {
        "trials_total": len(results),
        "trials_conclusive": n,
        "trials_inconclusive": len(results) - n,
        "deployments": len({k[0] for k in cell_state}),
        "capability_cells": len(cell_state),
        "trial_level": {
            "match": counts.get(O.DIV_NONE, 0),
            "silent": counts.get(O.DIV_SILENT, 0),
            "unrepresentable": counts.get(O.DIV_UNREPRESENTABLE, 0),
            "honest": counts.get(O.DIV_HONEST, 0),
            "undeclared": counts.get(O.DIV_UNDECLARED, 0),
        },
        "conformance": dict(conf),
        "silent_rate_trial_level": (conf.get(O.CONF_SILENT, 0) / n) if n else None,
        "cell_level_silent": sum(1 for v in cell_state.values() if O.CONF_SILENT in v),
        "trials_with_no_registry_row": sum(1 for r in conclusive if r["declared"] is None),
    }
