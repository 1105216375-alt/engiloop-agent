"""Minimal, synthetic adapter for the extracted BridgeReasoningMode.

The Observation mapping is adapted from the original domain adapter. The full
application is intentionally not instantiated: no archives, knowledge JSON,
model clients, production configuration or default database are loaded.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from domain.reasoning_bridge import BridgeReasoningMode
from nskernel.contracts import (
    DomainProfile, IdentityProfile, Observation, ObservationSchema, PersonaProfile,
)
from nskernel.dialogue.session import SessionStore


class SyntheticBridgeObservationSchema(ObservationSchema):
    """Existing observation contract with explicit synthetic provenance."""

    observation_label = "合成病害"

    def from_raw(self, raw: dict[str, Any]) -> Observation:
        return Observation(
            entity_key=raw.get("桥名", ""),
            entity_display_name=raw.get("桥名", ""),
            category=raw.get("构件类别", ""),
            subject=raw.get("构件", ""),
            observation=raw.get("病害", ""),
            severity=raw.get("标度"),
            raw_data=deepcopy(raw),
            metadata={"synthetic": True},
        )

    def format_summary_line(self, obs: Observation) -> str:
        return f"[合成] {obs.subject}·{obs.observation}，标度{obs.severity}"

    def required_fields(self) -> list[str]:
        return ["构件", "病害"]


def create_synthetic_profile() -> DomainProfile:
    return DomainProfile(
        name="synthetic_bridge",
        display_name="桥梁残差推理·合成机制演示",
        identity=IdentityProfile(
            role_declaration="合成桥梁分析演示助手",
            scope_description="合成数据上的证据边界和残差推理演示",
            in_scope_keywords=["桥", "梁", "病害", "风险", "优先", "大修", "寿命"],
        ),
        personas=[
            PersonaProfile(level=level, role_label="合成演示", tone_hint="保留证据边界")
            for level in ("rigor", "senior", "master")
        ],
        metadata={"synthetic": True, "not_an_engineering_assessment": True},
    )


@dataclass
class SyntheticBridgeFixture:
    profile: DomainProfile = field(default_factory=create_synthetic_profile)
    obs_schema: ObservationSchema = field(default_factory=SyntheticBridgeObservationSchema)
    session_store: SessionStore = field(default_factory=SessionStore)
    state_store: Any = None
    llm: Any = None
    session_id: str = "synthetic-session"


def build_bridge_fixture(
    *,
    bridge_name: str = "合成演示桥",
    dr: Any = 92,
    bci: Any = 94,
    extra: dict | None = None,
    parts: dict | None = None,
    critical: int = 0,
    state_store: Any = None,
    semantic_frame: dict | None = None,
) -> SyntheticBridgeFixture:
    """Scores are test inputs, NOT scores recomputed from a published standard."""
    bridge = SyntheticBridgeFixture(state_store=state_store)
    fact = {
        "构件": "主梁", "病害": "裂缝", "标度": 2, "风险评分": 35,
        "桥名": bridge_name, "位置": "1号主梁腹板",
        "来源定位": f"synthetic:{bridge_name}:observation-1",
        "来源": "synthetic_fixture", "报告ID": "synthetic-report",
        **deepcopy(extra or {}),
    }
    session = bridge.session_store.get_or_create(bridge.session_id)
    session.save_observations([bridge.obs_schema.from_raw(fact)])
    session.metadata.update({
        "synthetic": True,
        "bridge_score": {
            "全桥评分": dr, "技术状况等级": "合成输入（不作评级）",
            "部位得分": deepcopy(parts or {}),
        },
        # Compatibility key from the original application, not a standard claim.
        "bridge_score_52xx": {"BCI": bci, "grade_label": "合成输入（不作评级）"},
        "structure": {"桥名": bridge_name, "跨数": 1, "梁型": "混凝土梁", "总主梁数": 1},
        "stats": {
            "总数": 1, "高风险数": 0, "严控区数": critical,
            "最高风险评分": fact["风险评分"],
        },
        "evidence_contract": {
            "bridge_name": bridge_name,
            "facts": [deepcopy(fact)],
            "reports": [{"id": "synthetic-report", "synthetic": True}],
        },
    })
    if semantic_frame is not None:
        session.metadata["semantic_frame"] = deepcopy(semantic_frame)
    return bridge


def analyze_fixture(
    bridge: SyntheticBridgeFixture,
    question: str,
    *,
    enable_memory: bool = False,
    **analysis_options,
) -> dict:
    """Execute the real extracted BridgeReasoningMode with model calls disabled."""
    return BridgeReasoningMode(bridge).analyze(
        question, bridge.session_id,
        enable_style_llm=False, enable_euler_memory=enable_memory,
        **analysis_options,
    )


def run_bridge_case(question: str, *, analysis_options: dict | None = None, **fixture_options) -> dict:
    return analyze_fixture(
        build_bridge_fixture(**fixture_options), question, **(analysis_options or {}),
    )


def unknown_keys(result: dict) -> set[str]:
    return {row["key"] for row in result["residual"]["final"]["unknowns"]}


def accepted_action_types(result: dict) -> list[str]:
    actions = []
    for step in result.get("steps", []):
        for candidate, decision in zip(step["candidates"], step["decisions"]):
            if decision.get("action") == "accept":
                actions.append(candidate["action"]["type"])
    return actions
