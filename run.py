#!/usr/bin/env python3
"""llm-conformance -- probe LLM deployments for declared-vs-actual divergence.

Zero dependencies. Standard library only.

    python3 run.py probe                  # probe everything you have keys for
    python3 run.py probe --provider openai --models gpt-5.6
    python3 run.py report results/<run>.jsonl
    python3 run.py doctor                 # what can I probe right now?
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from conformance import credentials as cred
from conformance import outcomes as O
from conformance.evidence import EvidenceWriter, new_run_id
from conformance.registry import Registry, snapshot_meta
from conformance.report import load_results, render, summary_dict
from conformance.runner import (ADAPTERS, DEFAULT_TRIALS, LOCAL_SURFACES,
                                load_dotenv, make_deployment, profiles_for,
                                resolve_key, run_all)

CAPABILITIES = ["structured_output", "tool_calling"]


def _local_models(cls) -> list[str]:
    """Ask a local runtime what it actually has loaded."""
    import urllib.request
    try:
        url = cls.base_url.rsplit("/", 1)[0] + "/api/tags"
        data = json.loads(urllib.request.urlopen(url, timeout=5).read())
        return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []
RESULTS_DIR = "results"


def cmd_doctor(args) -> int:
    env = load_dotenv(args.env)
    registry = Registry.load(force_refresh=args.refresh)
    meta = snapshot_meta()

    print("\nllm-conformance doctor\n" + "=" * 60)
    print(f"registry snapshot : {meta.get('fetched_at', 'not fetched')}")
    print(f"           sha256 : {meta.get('sha256', '-')[:24]}")
    print(f"        providers : {len(registry.data)}")
    print("\ndeployment surfaces:")
    ready = 0
    for name, cls in ADAPTERS.items():
        if name in LOCAL_SURFACES:
            import urllib.request
            try:
                probe_url = cls.base_url.rsplit("/", 1)[0] + "/api/version"
                urllib.request.urlopen(probe_url, timeout=3).read()
                ready += 1
                status = f"READY   (local, no credential) {cls.base_url}"
            except Exception:
                status = f"OFFLINE (start ollama) {cls.base_url}"
        elif resolve_key(cls, env):
            ready += 1
            status = f"READY   (…{resolve_key(cls, env)[-4:]})"
        else:
            status = f"MISSING (set {' or '.join(cls.env_vars)})"
        print(f"  {name:<16} {status}")

    env_vars: list[str] = []
    for cls in ADAPTERS.values():
        env_vars.extend(cls.env_vars)
    statuses = [s for s in cred.check_all(env_vars, env) if s.present]
    if statuses:
        print("\ncredential lifecycle:")
        for st in statuses:
            print(f"  {st.message()}")
        stale = [s for s in statuses if s.needs_action]
        if stale:
            print(f"  -> {len(stale)} need rotation (python3 run.py credentials)")

    print(f"\n{ready}/{len(ADAPTERS)} surfaces probeable.")
    if ready == 0:
        print("\nNothing reachable. Add keys to .env, or start a local runtime.")
        return 1
    return 0


def cmd_discover(args) -> int:
    """Scan for local runtimes. Local endpoints are the credential-free path to
    reproduction -- anyone can re-run a finding with no vendor account."""
    import urllib.request
    from conformance.catalog import build_adapters as _ba

    _all, cfgs = _ba(include_disabled=True)
    print("\nlocal runtime discovery\n" + "=" * 62)
    live, dead = [], []
    for pid, cfg in sorted(cfgs.items()):
        if not (cfg.local or cfg.auth == "none"):
            continue
        for path in ("/models", "/tags", "/api/tags", "/version", "/api/version"):
            url = cfg.base_url.rstrip("/") + path
            try:
                with urllib.request.urlopen(url, timeout=2) as r:
                    r.read(200)
                live.append((pid, cfg, url))
                break
            except Exception:
                continue
        else:
            dead.append((pid, cfg))

    for pid, cfg, url in live:
        flag = "" if cfg.enabled else "   <- set \"enabled\": true in deployments.json"
        print(f"  LIVE      {pid:<14} {cfg.base_url}{flag}")
    for pid, cfg in dead:
        state = "enabled" if cfg.enabled else "template"
        print(f"  offline   {pid:<14} {cfg.base_url}   ({state})")

    print("\nAdding a local runtime is a deployments.json block — no Python —")
    print("as long as it speaks a known wire (openai / anthropic / gemini /")
    print("ollama-native). Confirm the port in the app's own settings first;")
    print("the defaults in the catalog are documented values, not verified.")
    return 0 if live else 1


def cmd_credentials(args) -> int:
    """Credential lifecycle status. Exit code is the alerting signal."""
    env = load_dotenv(args.env)
    env_vars: list[str] = []
    for cls in ADAPTERS.values():
        env_vars.extend(cls.env_vars)
    for extra in ("AZURE_OPENAI_API_KEY", "AWS_ACCESS_KEY_ID"):
        if extra in env or extra in os.environ:
            env_vars.append(extra)

    statuses = cred.check_all(env_vars, env)
    payload = cred.alert_payload(statuses)

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print("\ncredential lifecycle\n" + "=" * 60)
        order = {cred.STATUS_EXPIRED: 0, cred.STATUS_CRITICAL: 1, cred.STATUS_WARNING: 2,
                 cred.STATUS_NO_EXPIRY: 3, cred.STATUS_OK: 4, cred.STATUS_ABSENT: 5}
        for st in sorted(statuses, key=lambda s: (order.get(s.status, 9), s.env_var)):
            flag = {cred.STATUS_EXPIRED: "!!", cred.STATUS_CRITICAL: "!!",
                    cred.STATUS_WARNING: " !", cred.STATUS_NO_EXPIRY: " ?",
                    cred.STATUS_OK: " ok", cred.STATUS_ABSENT: "  -"}[st.status]
            print(f"  [{flag}] {st.message()}")
            if st.note:
                print(f"         note: {st.note}")
        print(f"\n{payload['summary']}")
        print("\nexit codes: 0 healthy | 1 expiry not recorded | 2 rotate soon | 3 expired")

    sev = payload["worst_severity"]
    return {0: 0, 1: 1, 2: 2, 3: 3}.get(sev, 0)


def cmd_probe(args) -> int:
    env = load_dotenv(args.env)
    registry = Registry.load(force_refresh=args.refresh)

    surfaces = args.provider or list(ADAPTERS)
    for p in surfaces:
        if p not in ADAPTERS:
            print(f"unknown surface: {p} (known: {', '.join(ADAPTERS)})", file=sys.stderr)
            return 2

    plan: list[tuple[str, str, object]] = []
    for surface in surfaces:
        cls = ADAPTERS[surface]
        if args.models and len(surfaces) == 1:
            models = args.models
        elif surface in LOCAL_SURFACES:
            models = args.models or _local_models(cls)
        else:
            models = registry.resolve_probe_models(cls.registry_id or cls.name,
                                                   args.models_per_provider)
        profs = profiles_for(surface, env)
        if args.profile:
            profs = [p for p in profs if p is not None and p.name in args.profile]
        for prof in profs:
            for w in (prof.warnings if prof is not None else []):
                print(f"  WARNING: {w}")
        for prof in profs:
            plan += [(surface, m, prof) for m in models]

    capabilities = args.capability or CAPABILITIES
    planned = len(plan) * len(capabilities) * args.trials

    run_id = new_run_id()
    out_path = os.path.join(RESULTS_DIR, f"{run_id}.jsonl")

    env_vars2: list[str] = []
    for surface in surfaces:
        env_vars2.extend(ADAPTERS[surface].env_vars)
    for st in cred.check_all(env_vars2, env):
        if st.needs_action:
            print(f"  WARNING: {st.message()}")

    print(f"\nrun: {run_id}")
    print(f"planned probes: {planned}   evidence -> {out_path}\n")

    if args.dry_run:
        for surface, model, prof in plan:
            cls = ADAPTERS[surface]
            local = surface in LOCAL_SURFACES
            key = "" if local else resolve_key(cls, env, prof)
            flag = "" if (local or key) else "  [NO KEY -> skipped]"
            dep = make_deployment(surface, model, prof)
            if prof is not None:
                print(f"  profile: {prof.label()}  ({prof.key_var})")
            print(f"  {dep.label()}{flag}")
            print(f"      {dep.identity()}")
            for cap in capabilities:
                d = registry.declares(cls.registry_id or cls.name, model, cap)
                print(f"      {cap:<20} declared={d}   x{args.trials} trials")
        print("\ndry run: nothing sent.")
        return 0

    def progress(r):
        mark = {O.DIV_SILENT: "!!", O.DIV_UNREPRESENTABLE: "##", O.DIV_HONEST: " ~",
                O.DIV_NONE: " ok", O.DIV_UNDECLARED: " +", O.DIV_UNKNOWN: " ?"}[r.divergence]
        print(f"  [{mark}] {r.provider}/{r.model_requested} via {r.deployment['adapter']} "
              f"{r.capability} t{r.trial_index}: {r.outcome}", flush=True)

    with EvidenceWriter(out_path) as writer:
        results = run_all(run_id, plan, registry, env, writer, capabilities,
                          args.trials, progress)

    rows = [json.loads(r.to_json()) for r in results]
    print(render(rows, color=sys.stdout.isatty()))
    print(f"evidence: {out_path}")
    print(f"summary : {json.dumps(summary_dict(rows))}\n")
    return 0


def cmd_readjudicate(args) -> int:
    from conformance.readjudicate import readjudicate_file
    if not os.path.exists(args.path):
        print(f"no such results file: {args.path}", file=sys.stderr)
        return 2
    rows, changes = readjudicate_file(args.path)
    print(f"\nre-scored {len(rows)} trial(s) from {args.path}")
    print(f"reclassified: {len(changes)}")
    for ch in changes:
        print(f"  {ch['model']} {ch['capability']} t{ch['trial']}: "
              f"{ch['from']} -> {ch['to']}")
    if args.write:
        with open(args.write, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, sort_keys=True) + "\n")
        print(f"\nwrote {args.write}")
    print(render(rows, color=sys.stdout.isatty() and not args.no_color))
    return 0


def cmd_report(args) -> int:
    if not os.path.exists(args.path):
        print(f"no such results file: {args.path}", file=sys.stderr)
        return 2
    rows = load_results(args.path)
    print(render(rows, color=sys.stdout.isatty() and not args.no_color))
    if args.json:
        print(json.dumps(summary_dict(rows), indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="run.py", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env", default=".env", help="path to env file (default: .env)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("doctor", help="show what can be probed right now")
    d.add_argument("--refresh", action="store_true", help="re-fetch models.dev snapshot")
    d.set_defaults(func=cmd_doctor)

    p = sub.add_parser("probe", help="run probes and write an evidence bundle")
    p.add_argument("--provider", action="append", choices=list(ADAPTERS))
    p.add_argument("--models", nargs="+", help="explicit model ids (single provider only)")
    p.add_argument("--capability", action="append", choices=CAPABILITIES)
    p.add_argument("--profile", action="append",
                   help="limit to named credential profile(s), e.g. --profile useast")
    p.add_argument("--models-per-provider", type=int, default=3)
    p.add_argument("--trials", type=int, default=DEFAULT_TRIALS,
                   help=f"repetitions per response-level probe (default {DEFAULT_TRIALS}); "
                        "single-shot results are inadmissible for response-level outcomes")
    p.add_argument("--refresh", action="store_true", help="re-fetch models.dev snapshot")
    p.add_argument("--dry-run", action="store_true", help="print the plan, send nothing")
    p.set_defaults(func=cmd_probe)

    dsc = sub.add_parser("discover", help="scan for local model runtimes")
    dsc.set_defaults(func=cmd_discover)

    cr = sub.add_parser("credentials", help="credential expiry / rotation status")
    cr.add_argument("--json", action="store_true",
                    help="machine-readable payload for cron, webhooks, or email templates")
    cr.set_defaults(func=cmd_credentials)

    ra = sub.add_parser("readjudicate",
                        help="re-score a stored bundle under the current taxonomy (no API calls)")
    ra.add_argument("path")
    ra.add_argument("--write", metavar="OUT", help="write re-scored bundle to OUT")
    ra.add_argument("--no-color", action="store_true")
    ra.set_defaults(func=cmd_readjudicate)

    r = sub.add_parser("report", help="render a report from an evidence bundle")
    r.add_argument("path")
    r.add_argument("--json", action="store_true", help="also emit machine-readable summary")
    r.add_argument("--no-color", action="store_true")
    r.set_defaults(func=cmd_report)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
