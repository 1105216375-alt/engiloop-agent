from __future__ import annotations

from typing import Any

from .prompt_injection_guard import (
    build_prompt_injection_reply,
    inspect_prompt_injection,
)
from .regulation_input_guard import (
    build_regulation_guard_reply,
    validate_regulation_references,
)


def build_agent_input_boundary(question: str) -> dict[str, Any] | None:
    """Build a challenge-route boundary result before any reasoning tool runs."""
    injection = inspect_prompt_injection(question)
    if injection.get("blocked"):
        return {
            "answer": build_prompt_injection_reply(),
            "status": "guard_blocked",
            "passed": True,
            "engine": "prompt_injection_guard",
            "bridge_name": "",
            "tool_calls": [{
                "tool": "PromptInjectionGuard",
                "status": "blocked",
                "reason": injection.get("reason"),
                "target": injection.get("target"),
            }],
            "verification": {
                "passed": True,
                "boundary": "sensitive_input_blocked",
                "sensitive_output_allowed": False,
            },
            "residual_summary": {"status": "blocked", "preserved_unknowns": 0},
            "evidence": {},
            "prompt_injection_guard": injection,
        }

    regulation = validate_regulation_references(question)
    if not regulation.get("passed"):
        unsupported = regulation.get("unsupported") or []
        return {
            "answer": build_regulation_guard_reply(unsupported),
            "status": "guard_blocked",
            "passed": True,
            "engine": "regulation_reference_guard",
            "bridge_name": "",
            "tool_calls": [{
                "tool": "RegulationReferenceGuard",
                "status": "blocked",
                "references": regulation.get("references") or [],
                "unsupported": unsupported,
            }],
            "verification": {
                "passed": True,
                "boundary": "unsupported_regulation_blocked",
                "input_reference_valid": False,
            },
            "residual_summary": {"status": "blocked", "preserved_unknowns": 0},
            "evidence": {},
            "regulation_input_guard": regulation,
        }

    return None
