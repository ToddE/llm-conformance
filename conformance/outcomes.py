"""The outcome taxonomy.

This module is small and it is the most important file in the project.

The naive framing of this project is "does the capability work, yes or no."
That framing is wrong, and if the harness adopts it the resulting numbers are
worthless. The distinction that actually matters to someone running these
models in production is:

    An endpoint that REJECTS a parameter it doesn't support is behaving
    correctly. You find out at integration time, you handle it, you move on.

    An endpoint that ACCEPTS a parameter and then ignores it is the failure
    mode that reaches production. Your code works in testing, and then one
    response in fifty is unparseable prose and your pipeline breaks at 2am.

Both are "declared true, doesn't work." Only one is dangerous. Collapsing
them into a single divergence percentage destroys the finding. So every
result carries BOTH an outcome (what happened) and a divergence class (what
it means).
"""

from __future__ import annotations

# ── Outcomes: what literally happened ────────────────────────────────────────

PASS = "pass"
"""Capability was requested and honored. The response satisfies the contract."""

FAIL_VIOLATED = "fail_violated"
"""The endpoint accepted the request and returned a well-formed response that
VIOLATES the contract -- JSON that breaks the schema, or a text answer when a
tool call was required. This is silent non-enforcement: the dangerous case."""

FAIL_UNPARSEABLE = "fail_unparseable"
"""Accepted the constraint, returned something that isn't even the right shape
(prose where JSON was mandated). Also silent non-enforcement, more obvious."""

FAIL_ADVISORY_ONLY = "fail_advisory_only"
"""The response broke ONLY advisory schema keywords (maxLength, pattern,
minimum...) while honoring every structural constraint (type, enum, required,
additionalProperties).

Advisory keywords are widely outside the enforceable subset of strict decoding
-- OpenAI's strict mode rejects `maxLength` as unsupported; Anthropic's tool
`input_schema` accepts it and does not constrain on it. Scoring this as a
silent failure would be a false positive a provider could refute in one
sentence, and one refutable finding costs more credibility than ten correct
ones gain.

So it gets its own class and is EXCLUDED from the headline silent-failure
rate. This deliberately lowers our own divergence number, which is the correct
direction for a measurement project to be wrong in. It is still reported: "you
may send maxLength and it will be silently unenforced" is real, actionable
information."""

REJECTED = "rejected"
"""The endpoint refused the request with an error that names the capability --
4xx saying the parameter is unsupported for this model. Honest failure."""

UNSUPPORTED_DIALECT = "unsupported_dialect"
"""Accepted the capability in principle but REJECTED our schema outright --
a 4xx naming the schema. The endpoint told us it could not represent the
constraint. Honest, because it errored."""

UNREPRESENTABLE = "unrepresentable"
"""The constraint could not be STATED in this platform's schema dialect at
all, so it was dropped in translation and the endpoint was never asked to
enforce it. The response then violated it.

This is NOT a silent failure and must never be counted as one. Nothing lied:
the endpoint honored every constraint it was actually given. The defect is in
the contract language, not the implementation.

Keeping this separate is a correctness requirement, not a stylistic one. The
project's kill criterion is a silent-failure rate threshold. Folding
unrepresentable results into that rate inflates the one number that decides
whether the thesis lives -- in the direction of rescuing it. A measurement
project that biases toward its own survival is worthless.

What it proves instead is subtler and arguably stronger: capability metadata
has to describe WHICH SUBSET OF THE CONTRACT LANGUAGE a deployment can
express. That is schema-language compatibility, not a feature flag, and no
boolean can encode it."""

ERROR_TRANSPORT = "error_transport"
"""Network failure, timeout, 5xx, connection reset. Says nothing about the
capability. Never counted as a conformance failure."""

ERROR_AUTH = "error_auth"
"""401/403. Setup problem, not a capability signal."""

ERROR_RATE_LIMIT = "error_rate_limit"
"""429. Retry exhausted. Not a capability signal."""

ERROR_TRUNCATED = "error_truncated"
"""The response hit the output token limit mid-generation. A truncated JSON
object is invalid JSON, and scoring that as a schema violation would be a
FALSE POSITIVE -- the single most damaging bug this harness could have. A
finding that a provider can refute by pointing at finish_reason costs more
credibility than it gains. Always inconclusive."""

ERROR_NOT_FOUND = "error_not_found"
"""404 / "model does not exist" for this key. Not a capability failure, but IS
a registry divergence worth reporting: models.dev lists a model this
credential cannot reach."""

SKIPPED_NO_CREDENTIAL = "skipped_no_credential"
"""No API key for this provider. Reported, never silently dropped."""

# Outcomes that carry no information about the capability. Excluded from every
# conformance rate we publish -- counting a timeout as a failure would inflate
# the divergence number, which is the specific way this project could most
# easily produce a dishonest headline.
INCONCLUSIVE = {
    ERROR_TRANSPORT,
    ERROR_TRUNCATED,
    ERROR_AUTH,
    ERROR_RATE_LIMIT,
    ERROR_NOT_FOUND,
    SKIPPED_NO_CREDENTIAL,
}

# Outcomes where the endpoint took our money and lied.
SILENT_FAILURE = {FAIL_VIOLATED, FAIL_UNPARSEABLE}

# Accepted-but-unenforced, on keywords no strict decoder promises to enforce.
ADVISORY_FAILURE = {FAIL_ADVISORY_ONLY}

# Outcomes where the endpoint told us the truth, even if the truth was "no".
HONEST_FAILURE = {REJECTED, UNSUPPORTED_DIALECT}

# The constraint never reached the endpoint. Its own class, counted nowhere else.
UNREPRESENTABLE_SET = {UNREPRESENTABLE}


# ── Divergence classes: what it means vs. the registry ───────────────────────

DIV_NONE = "none"
"""Declared and observed agree."""

DIV_SILENT = "silent"
"""Declared supported; endpoint accepted the request and did not honor it.
The headline finding. This is what breaks production systems."""

DIV_HONEST = "honest"
"""Declared supported; endpoint refused, clearly, at request time. A registry
inaccuracy, but a safe one -- you find out immediately."""

DIV_UNDECLARED = "undeclared"
"""Declared unsupported (or absent from the registry); endpoint honored it
anyway. Registry is pessimistic. Worth reporting: it means people are avoiding
capabilities that actually work."""

DIV_ADVISORY = "advisory"
"""Declared supported; structural constraints honored, advisory keywords not.
Reported separately, never inside the silent-failure rate."""

DIV_UNREPRESENTABLE = "unrepresentable"
"""Declared supported; the constraint could not be expressed in this
platform's schema dialect. Reported on its own line, never inside the
silent-failure rate."""

DIV_UNKNOWN = "unknown"
"""Inconclusive result, or the model isn't in the registry at all."""


def classify_divergence(declared: bool | None, outcome: str) -> tuple[str, bool | None]:
    """Map (what the registry claims, what we observed) to a divergence class.

    Returns (divergence_class, conforms) where `conforms` is tri-state:
    True/False/None, with None meaning "we learned nothing" -- deliberately
    distinct from False so inconclusive runs can never masquerade as failures.
    """
    if outcome in INCONCLUSIVE:
        return DIV_UNKNOWN, None

    honored = outcome == PASS

    if declared is None:
        # Not in the registry. We still learned the ground truth, but there's
        # no claim to compare it against.
        return DIV_UNKNOWN, honored

    if declared and honored:
        return DIV_NONE, True
    if not declared and not honored:
        return DIV_NONE, True
    if declared and not honored:
        if outcome == UNREPRESENTABLE:
            return DIV_UNREPRESENTABLE, False
        if outcome == FAIL_ADVISORY_ONLY:
            return DIV_ADVISORY, False
        return (DIV_SILENT if outcome in SILENT_FAILURE else DIV_HONEST), False
    # not declared, but it worked
    return DIV_UNDECLARED, True


def severity(divergence: str) -> int:
    """Sort order for reporting: most actionable first."""
    return {
        DIV_SILENT: 0,
        DIV_UNREPRESENTABLE: 1,
        DIV_ADVISORY: 2,
        DIV_HONEST: 2,
        DIV_UNDECLARED: 3,
        DIV_UNKNOWN: 4,
        DIV_NONE: 5,
    }.get(divergence, 6)


# ── Conformance class: what the ENDPOINT did, independent of any registry ────
#
# Registry divergence needs a claim to diverge from. Capability conformance
# does not: an endpoint that accepts `tool_choice: required` and ignores it has
# failed its own contract whether or not models.dev has a row for it.
#
# These must be reported separately. The silent-failure rate -- the kill
# criterion -- is a property of endpoint behavior, so it is computed here, not
# from divergence. Otherwise a deployment missing from the registry would be
# scored as "no divergence", and absence of a claim would launder a real
# failure into a clean result.

CONF_ADVISORY = "advisory"
CONF_HONORED = "honored"
CONF_SILENT = "silent"
CONF_UNREPRESENTABLE = "unrepresentable"
CONF_HONEST = "honest"
CONF_INCONCLUSIVE = "inconclusive"


def conformance_class(outcome: str) -> str:
    if outcome in INCONCLUSIVE:
        return CONF_INCONCLUSIVE
    if outcome == PASS:
        return CONF_HONORED
    if outcome == UNREPRESENTABLE:
        return CONF_UNREPRESENTABLE
    if outcome == FAIL_ADVISORY_ONLY:
        return CONF_ADVISORY
    if outcome in SILENT_FAILURE:
        return CONF_SILENT
    if outcome in HONEST_FAILURE:
        return CONF_HONEST
    return CONF_INCONCLUSIVE


# ── Cell verdict: does a deployment+capability behave the SAME WAY every ─────
#    time, or does it depend on the draw?
#
# A single trial's conformance_class() says what happened once. It says
# nothing about whether that trial was representative. Capabilities.md
# specifies a fifth classification for exactly this: repeated trials
# producing materially different results under the same recorded conditions.
# "4 of 5 trials conformed, 1 of 5 violated the schema" must not be reported
# as either a clean pass or a clean silent failure -- both readings are
# wrong, and an inconsistent result must not be treated as strict support.
#
# This lives here rather than as a sixth value alongside PASS/FAIL_VIOLATED/
# etc. in the outcome enum, because it isn't a property of one trial. It's an
# aggregate over a cell's trials, computed after the fact -- the same
# relationship conformance_class() already has to a raw outcome string.

CELL_INCONSISTENT = "inconsistent"


def cell_verdict(conformance_classes: list[str]) -> str:
    """Aggregate one deployment+capability cell's CONCLUSIVE-trial
    conformance classes into a single cell-level verdict.

    Pass conformance_class(r["outcome"]) for every trial in the cell whose
    outcome is not in INCONCLUSIVE -- inconclusive trials (a timeout, a
    missing credential) carry no signal about the endpoint's behavior and
    must never count toward "did this behave consistently."

    Every trial agreeing returns that single class -- an all-PASS cell's
    verdict is CONF_HONORED, an all-silent cell's verdict is CONF_SILENT, and
    so on; nothing changes for the common case where a deployment behaves the
    same way every time it is probed. Only a genuine split returns
    CELL_INCONSISTENT. Zero conclusive trials returns CONF_INCONCLUSIVE,
    matching how a cell with no signal at all is treated everywhere else.
    """
    distinct = set(conformance_classes)
    if not distinct:
        return CONF_INCONCLUSIVE
    if len(distinct) == 1:
        return next(iter(distinct))
    return CELL_INCONSISTENT
