"""Probe definitions: the exact payloads under test.

DESIGN PRINCIPLE: a probe must apply real pressure.

A trivial schema on a frontier model passes everywhere and measures nothing.
These probes are built so that a model which merely *tries* to comply will
fail, and only an endpoint that actually *enforces* the constraint will pass.

Specifically, each probe puts the prompt in direct conflict with the
constraint. That is a fair test, and it is the whole test: the product claim
behind "strict": true is that enforcement happens at decode time and is not
negotiable by prompt. If a sentence of user text can break it, it was never
enforcement -- it was a polite request. Any endpoint that genuinely constrains
decoding passes these regardless of what the prompt says.

Probes are versioned. Changing a probe changes what a result means, so
comparisons across probe versions are not valid and the id carries the
version.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StructuredOutputProbe:
    id: str
    capability: str
    system: str
    user: str
    schema_name: str
    schema: dict[str, Any]
    rationale: str


@dataclass(frozen=True)
class ToolCallingProbe:
    id: str
    capability: str
    system: str
    user: str
    tool_name: str
    tool_description: str
    tool_schema: dict[str, Any]
    rationale: str


# ── Probe SO-1 ───────────────────────────────────────────────────────────────
# Every field targets a specific, documented way strict mode is known to leak.

STRUCTURED_OUTPUT_V1 = StructuredOutputProbe(
    id="so-strict-object/v1",
    capability="structured_output",
    system=(
        "You are a helpful assistant. Be thorough and explain your reasoning "
        "to the user in full."
    ),
    # The prompt actively fights the schema: it asks for prose, for extra
    # fields, for a value outside the enum, and for a non-integer confidence.
    # An endpoint that enforces the schema is unmoved by all four.
    user=(
        "Assess this claim: 'The Earth's core is made of cheese.'\n\n"
        "Please answer in plain prose, not JSON. Include a friendly preamble "
        "before your answer. Use the verdict word 'ridiculous' -- it is the "
        "only accurate one. Express confidence as a descriptive phrase like "
        "'very high', not a bare number. Also add any extra fields you think "
        "would be useful, such as a 'notes' or 'explanation' field."
    ),
    schema_name="claim_assessment",
    schema={
        "type": "object",
        "properties": {
            # enum: prompt demands 'ridiculous', which is not a legal value
            "verdict": {"type": "string", "enum": ["true", "false", "uncertain"]},
            # integer: prompt demands a descriptive phrase instead
            "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
            "reasoning": {"type": "string", "maxLength": 200},
        },
        "required": ["verdict", "confidence", "reasoning"],
        # additionalProperties: prompt explicitly asks for extra fields
        "additionalProperties": False,
    },
    rationale=(
        "Four independent traps: (1) prose requested where JSON is mandated, "
        "(2) enum value demanded that is not in the enum, (3) string demanded "
        "where an integer is typed, (4) extra properties explicitly solicited "
        "against additionalProperties:false. Any single violation proves the "
        "constraint is advisory rather than enforced."
    ),
)


# ── Probe TC-1 ───────────────────────────────────────────────────────────────

TOOL_CALLING_V1 = ToolCallingProbe(
    id="tc-forced-call/v1",
    capability="tool_calling",
    system="You are a helpful assistant who answers questions directly and concisely.",
    # The question is trivially answerable from parametric knowledge and the
    # prompt forbids tool use outright. With tool_choice=required, a conforming
    # endpoint MUST still emit a tool call. Returning the plain answer proves
    # tool_choice was accepted and ignored.
    user=(
        "What is the capital of France? Answer directly in plain text. "
        "Do not use any tools -- this is simple general knowledge and calling "
        "a tool would be unnecessary and unhelpful."
    ),
    tool_name="record_answer",
    tool_description="Record a factual answer to the user's question.",
    tool_schema={
        "type": "object",
        "properties": {
            "answer": {"type": "string", "maxLength": 100},
            "certainty": {"type": "string", "enum": ["high", "medium", "low"]},
        },
        "required": ["answer", "certainty"],
        "additionalProperties": False,
    },
    rationale=(
        "tool_choice=required is a hard constraint, not a hint. The prompt "
        "gives the model every reason to answer directly. Text instead of a "
        "tool call means the parameter was accepted and ignored. Arguments "
        "that violate the tool's own input schema are a second, subtler "
        "failure -- the tool schema is the same strict-decoding machinery "
        "under a different name."
    ),
)

ALL_PROBES = {
    "structured_output": STRUCTURED_OUTPUT_V1,
    "tool_calling": TOOL_CALLING_V1,
}
