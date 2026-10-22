"""Reviewed route reuse through the real bridge runtime; no prior facts copied."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from bip_runtime.euler_memory import EulerMemoryMatcher
from engineering_agent.bridge_fixture import (
    accepted_action_types, analyze_fixture, build_bridge_fixture,
)
from legacy_v7.core.state_store import SQLiteStateStore


def run_reviewed_memory_demo() -> dict:
    with TemporaryDirectory(prefix="engineering-agent-reviewed-memory-") as directory:
        store = SQLiteStateStore(Path(directory) / "synthetic-memory.sqlite3")
        try:
            source_name = "合成来源桥"
            target_name = "合成目标桥"
            source = analyze_fixture(build_bridge_fixture(
                bridge_name=source_name, state_store=store, dr=91, bci=93,
                parts={"桥面系": {"伸缩缝": 72}},
                extra={"病害": "锈蚀", "风险评分": 77},
            ), f"{source_name}哪些构件优先维修？", enable_memory=True)
            candidate_id = source["memory_candidate"]["candidate_id"]
            candidate = store.get_memory_candidate(candidate_id)
            assert candidate["review_status"] == "pending"
            assert candidate["payload"]["reuse_policy"]["may_copy_bridge_facts"] is False
            assert candidate["payload"]["reuse_policy"]["may_copy_final_answer"] is False

            question = f"{target_name}风险如何，哪些构件优先维修？"

            def target_fixture():
                return build_bridge_fixture(
                    bridge_name=target_name, state_store=store, dr=60, bci=66,
                    parts={"桥面系": {"伸缩缝": 44}},
                )

            before_review = analyze_fixture(target_fixture(), question, enable_memory=True)
            assert before_review["euler_memory"]["applied"] is False
            assert before_review["euler_memory"]["retrieved"] == 0
            store.review_memory_candidate(
                candidate_id, "approved", "synthetic-reviewer",
                "合成演示：批准优先级分析路线；不批准复制来源桥数据。",
            )
            after_review = analyze_fixture(target_fixture(), question, enable_memory=True)
            before_actions = accepted_action_types(before_review)
            after_actions = accepted_action_types(after_review)
            assert before_actions[0] == "risk_assess"
            assert after_actions[0] == "priority"
            assert after_review["euler_memory"]["applied"] is True
            assert "risk_assess" in after_actions
            assert target_name in after_review["reply"]
            assert source_name not in after_review["reply"]
            assert "Dr=60, BCI=66" in after_review["reply"]
            assert "44.0分" in after_review["reply"]
            assert "风险77" not in after_review["reply"]
            assert "锈蚀" not in after_review["reply"]

            # Another actually executed route requires capacity and repair evidence.
            major_source = analyze_fixture(build_bridge_fixture(
                bridge_name=source_name, state_store=store,
                extra={
                    "荷载试验结论": "荷载试验结论：承载能力满足原设计要求。",
                    "维修记录": "合成记录：1号主梁裂缝已修复并验收合格。",
                },
            ), f"{source_name}值不值得大修？", enable_memory=True)
            store.review_memory_candidate(
                major_source["memory_candidate"]["candidate_id"],
                "approved", "synthetic-reviewer", "合成演示，需重新检查适用条件。",
            )
            missing = analyze_fixture(
                build_bridge_fixture(bridge_name=target_name, state_store=store),
                f"{target_name}值不值得大修？", enable_memory=True,
            )
            assert missing["euler_memory"]["retrieved"] >= 1
            assert missing["euler_memory"]["applied"] is False
            assert {"load_capacity", "repair_records"} <= set(
                missing["euler_memory"]["matches"][0]["missing_evidence"],
            )

            approved_priority = EulerMemoryMatcher(store).match(
                question=question, bridge_name=target_name,
                available_evidence={
                    "bridge_identity", "report_provenance", "defect_facts",
                    "severity", "component", "location", "dr_or_bci",
                },
                session_context={"components": ["主梁"]},
            )
            assert approved_priority and approved_priority[0]["can_apply"]

            return {
                "synthetic": True,
                "source_run": source,
                "pending_candidate": candidate,
                "before_review": before_review,
                "after_review": after_review,
                "missing_required_evidence": missing,
                "before_actions": before_actions,
                "after_actions": after_actions,
                "new_bridge_facts_preserved": True,
            }
        finally:
            store.close()


def main() -> None:
    result = run_reviewed_memory_demo()
    print(json.dumps({
        "synthetic": True,
        "source_route": accepted_action_types(result["source_run"]),
        "before_review_actions": result["before_actions"],
        "after_review_actions": result["after_actions"],
        "applied": result["after_review"]["euler_memory"]["applied"],
        "guided_steps": result["after_review"]["euler_memory"]["guided_steps"],
        "new_bridge_facts_preserved": result["new_bridge_facts_preserved"],
        "current_reply": result["after_review"]["reply"],
        "missing_evidence_blocks_reuse": result["missing_required_evidence"]["euler_memory"],
        "notice": "实际执行的抽取内核＋新增合成适配；无模型请求，临时数据库退出即清理。",
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
