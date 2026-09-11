---
title: Validation approach
nav_order: 5
---

# Validation approach
{: .no_toc }

1. TOC
{:toc}

---

This page states the premises this project's own harness is built to test,
and how the first experiment is scoped. It is a trimmed derivative of an
internal planning document; the parts specific to a separate, related
project's product strategy are not reproduced here.

## Why probe instead of declare

`supports_structured_output: true` cannot represent what actually needs to
be known. Building the first version of this harness surfaced distinct
failure modes that a single boolean collapses into one:

- **Honest failure.** The endpoint returns 400 naming the unsupported
  capability. Safe. Caught on day one of integration.
- **Silent failure.** The endpoint returns 200 and ignores the constraint.
  This is the one that reaches production.
- **Unrepresentable.** The constraint could not be stated in the provider's
  schema dialect at all. Gemini's `responseSchema`, for instance, cannot
  express `additionalProperties`. The constraint was not violated; it was
  never representable, so scoring it as a silent failure would be a false
  positive.

The third category may be the strongest argument that declared registries
are structurally inadequate. It holds regardless of how any divergence rate
comes out, because no boolean flag can encode it.

Capability claims also need levels that separate evidence from guarantee:
a 98% schema-conformance rate across many trials is evidence. It is not the
same thing as strict, decode-time enforcement, and the two should never be
reported as if they were.

## Two falsifiable premises

**P1: registries are inaccurate in ways that matter.**
Declared capability data materially diverges from actual behavior, and the
divergence includes silent failures alongside honest rejections. A registry
that is wrong in a way the caller discovers immediately is a documentation
bug. A registry that is wrong in a way that reaches production is a
different kind of problem.
*If false: there is no measurement product here, and the right move is to
consume an existing registry and stop.*

**P3: capabilities belong to the deployment, not the model.**
The same model reached through different platforms, regions, API versions,
or adapters can differ in observable capability. This is why every result
this project produces is tied to a full deployment identity (provider,
endpoint, model snapshot, region, adapter, credential) rather than just a
model name.
*If false: model-level registries suffice, and the deployment-identity
machinery in this harness is unnecessary complexity.*

## How the first experiment is scoped

**Depth over breadth.** The harness probes two capabilities (structured
output, forced tool calling) deeply rather than five or more shallowly:
repeated trials, schemas designed to put real pressure on the constraint,
and adversarial prompts that actively fight the schema. A trivial schema on
a capable model passes everywhere and measures nothing; the probes here are
built so that only genuine enforcement survives them.

**Deployment identity, recorded in full.** Every probe result records
provider, endpoint, model identifier and snapshot, region, API version,
adapter, and credential profile, the full deployment tuple. A finding that
cannot be attributed to a specific deployment is not reproducible.

**Repeated trials.** A single response-level pass proves non-violation on
one draw, not enforcement. Constraint-level results (an honest rejection, a
schema-dialect error) are deterministic and valid at a single trial;
response-level results need repetition before a conformance rate built from
them is trustworthy.

**A hard floor on the headline number, with no override upward.** The
harness reports a silent-failure rate as the primary signal, computed only
from conclusive trials and only from capability conformance (what the
endpoint actually did), never from registry divergence, since a deployment
absent from the registry must not be scored as conforming by default.
Severity weighting may argue for treating a below-floor result as still
worth attention; it may never be used to argue a result above the floor
should be discounted. A metric that can always be argued into significance
in either direction is not a useful floor.

**Selection bias is named, not hidden.** The reachable set of deployments
overrepresents popular, well-documented providers. What is not covered
(enterprise deployments, regional endpoints, older model aliases) is stated
as a limitation rather than generalized past.

See [Methodology](methodology) for the full outcome taxonomy and the rules
that keep the reported numbers from being gamed in either direction, and
[Findings](findings) for what has actually been measured against this
scope so far.
