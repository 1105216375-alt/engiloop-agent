"""Record a real intake -> graph retrieval -> safe-delivery tool chain as OTLP.

All inputs are synthetic. The temporary trace directory is removed on exit.
No historical tool records, model calls, customer data or production logs are
used. stdout contains validation metadata, not the trace's input/output payload.
"""
from __future__ import annotations

from datetime import date
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from bip_runtime.native_tracing import agent_trace
from engineering_agent.evidence import InMemoryEvidence, safe_delivery
from engineering_agent.nlp import compile_intake
from engineering_agent.safe_arithmetic import evaluate_arithmetic, SafeArithmeticError
from examples.reverse_verification import synthetic_evidence, synthetic_results


WORKFLOW_TOOLS = [
    {"type": "function", "name": "compile_intake",
     "description": "Parse explicit synthetic input with the real offline intake compiler.",
     "parameters": {"type": "object", "properties": {"text": {"type": "string"}},
                    "required": ["text"]}},
    {"type": "function", "name": "evidence.retrieve",
     "description": "Retrieve from an explicitly supplied synthetic graph and clause index.",
     "parameters": {"type": "object", "properties": {
         "component": {"type": "string"}, "disease": {"type": "string"}},
         "required": ["component", "disease"]}},
    {"type": "function", "name": "safe_delivery",
     "description": "Check a synthetic draft against supplied evidence and deliver only checked text.",
     "parameters": {"type": "object", "properties": {
         "draft": {"type": "string"}, "results": {"type": "array"},
         "original_query": {"type": "string"}},
         "required": ["draft", "results", "original_query"]}},
]

SYNTHETIC_QUERY = "主梁斜向裂缝，长80cm，宽0.3mm，只分析主梁病害，不出报告。"


ARITHMETIC_TOOL = {
    "type": "function",
    "name": "bounded_arithmetic",
    "description": "Execute an allowlisted numeric AST on explicit synthetic input.",
    "parameters": {
        "type": "object",
        "properties": {"expression": {"type": "string"}},
        "required": ["expression"],
    },
}


def span_attributes(span: dict) -> dict:
    result = {}
    for row in span.get("attributes", []):
        value = row.get("value", {})
        # This demonstration expects scalar OTLP AnyValue wrappers.
        result[row["key"]] = next(iter(value.values()), None)
    return result


def _read_and_check_trace(path: Path, expected_tools: list[dict]) -> tuple[dict, list[dict]]:
    """Inspect persisted OTLP, rather than trusting only in-memory state."""
    document = json.loads(path.read_text(encoding="utf-8"))
    spans = document["resourceSpans"][0]["scopeSpans"][0]["spans"]
    root, *children = spans
    definitions = json.loads(span_attributes(root)["gen_ai.tool.definitions"])
    assert definitions == expected_tools
    assert len(children) == len(expected_tools)
    previous_end = int(root["startTimeUnixNano"])
    for child, definition in zip(children, expected_tools):
        attrs = span_attributes(child)
        assert child["parentSpanId"] == root["spanId"]
        assert child["traceId"] == root["traceId"]
        assert previous_end <= int(child["startTimeUnixNano"])
        assert int(child["startTimeUnixNano"]) <= int(child["endTimeUnixNano"])
        assert int(child["endTimeUnixNano"]) <= int(root["endTimeUnixNano"])
        assert attrs["gen_ai.operation.name"] == "execute_tool"
        assert attrs["gen_ai.tool.name"] == definition["name"]
        previous_end = int(child["endTimeUnixNano"])
    assert all(span_attributes(row)["gen_ai.operation.name"] != "chat" for row in spans)
    return document, spans


def capture_synthetic_workflow() -> dict:
    """Run real public adapters; the draft and diagnostic rows are synthetic.

    The supplied risk values are a verification fixture, NOT scores calculated
    from the intake's measurements or its parser default scale. No model writes
    this draft. The only engineering claim made here is about execution wiring.
    """
    evidence: InMemoryEvidence = synthetic_evidence()
    with TemporaryDirectory(prefix="engineering-agent-native-workflow-") as directory:
        with patch.dict(os.environ, {"ENGINEERING_AGENT_TRACE_DIR": directory}):
            with agent_trace(
                [{"role": "user", "parts": [{"type": "text", "content": SYNTHETIC_QUERY}]}],
                conversation_id="synthetic-workflow-trace",
                tool_definitions=WORKFLOW_TOOLS,
            ) as trace:
                with trace.child("execute_tool compile_intake", "execute_tool", {
                    "gen_ai.tool.name": "compile_intake", "gen_ai.tool.type": "function",
                    "gen_ai.tool.call.id": "synthetic-intake-1",
                    "gen_ai.tool.call.arguments": {"text": SYNTHETIC_QUERY, "synthetic": True},
                }) as span:
                    intake = compile_intake(SYNTHETIC_QUERY, current_date=date(2026, 1, 1))
                    span.set_attribute("gen_ai.tool.call.result", intake)
                item = intake["semantically_eligible_items"][0]
                component, disease = item["comp"], item["disease"]
                with trace.child("execute_tool evidence.retrieve", "execute_tool", {
                    "gen_ai.tool.name": "evidence.retrieve", "gen_ai.tool.type": "function",
                    "gen_ai.tool.call.id": "synthetic-retrieve-2",
                    "gen_ai.tool.call.arguments": {"component": component, "disease": disease},
                }) as span:
                    retrieved = evidence.retrieve(component, disease)
                    span.set_attribute("gen_ai.tool.call.result", retrieved)
                clause = retrieved[0]
                # Explicit pre-provided synthetic results, not an omitted scorer.
                rows = [row for row in synthetic_results()
                        if row["构件"] == component and row["病害"] == disease]
                draft = (f"{component}的风险评分为30。{component}的风险评分为80。"
                         f"依据 {clause['reg']} {clause['clause']}，应复核记录。")
                with trace.child("execute_tool safe_delivery", "execute_tool", {
                    "gen_ai.tool.name": "safe_delivery", "gen_ai.tool.type": "function",
                    "gen_ai.tool.call.id": "synthetic-delivery-3",
                    "gen_ai.tool.call.arguments": {
                        "draft": draft, "results": rows, "original_query": SYNTHETIC_QUERY,
                        "result_source": "preprovided_synthetic_fixture_not_calculated_score",
                    },
                }) as span:
                    delivered = safe_delivery(draft, rows, verifier=evidence.verifier,
                                              original_query=SYNTHETIC_QUERY)
                    span.set_attribute("gen_ai.tool.call.result", delivered)
                trace.finish(delivered["text"])
            path = Path(directory) / "traces-dynamic.json"
            document, spans = _read_and_check_trace(path, WORKFLOW_TOOLS)
            assert len(spans) == 4
            assert all(row["status"]["code"] == 1 for row in spans)
            actual_outputs = [intake, retrieved, delivered]
            for span, output in zip(spans[1:], actual_outputs):
                assert json.loads(span_attributes(span)["gen_ai.tool.call.result"]) == output
            assert delivered["released"] and delivered["needs_review"]
            assert delivered["deleted_count"] == 1
            assert "风险评分为30" not in delivered["text"]
            assert "风险评分为80" in delivered["text"]
            assert clause["clause"] in delivered["text"]
            assert not intake["engineering_verified"] and not delivered["engineering_verified"]
            root_output = json.loads(span_attributes(spans[0])["gen_ai.output.messages"])
            assert root_output[0]["parts"][0]["content"] == delivered["text"]
        captured = {"document": document, "intake": intake, "retrieved": retrieved,
                    "delivered": delivered, "draft": draft, "temporary_path": str(path)}
    assert not path.exists()
    captured["temporary_artifact_removed"] = True
    return captured


def capture_synthetic_call(expression: str = "80 / 100") -> dict:
    """Return an in-memory copy after a real temporary-file round trip."""
    with TemporaryDirectory(prefix="engineering-agent-native-trace-") as directory:
        with patch.dict(os.environ, {"ENGINEERING_AGENT_TRACE_DIR": directory}):
            tool_called = False
            result = None
            error_kind = None
            with agent_trace(
                [{"role": "user", "parts": [{"type": "text", "content": "synthetic arithmetic request"}]}],
                conversation_id="synthetic-trace-example",
                tool_definitions=[ARITHMETIC_TOOL],
            ) as trace:
                try:
                    with trace.child("execute_tool bounded_arithmetic", "execute_tool", {
                        "gen_ai.tool.name": "bounded_arithmetic",
                        "gen_ai.tool.type": "function",
                        "gen_ai.tool.call.id": "synthetic-call-1",
                        "gen_ai.tool.call.arguments": {"expression": expression, "synthetic": True},
                    }) as span:
                        tool_called = True
                        result = evaluate_arithmetic(expression)
                        span.set_attribute("gen_ai.tool.call.result", {"value": result, "synthetic": True})
                except SafeArithmeticError as error:
                    error_kind = type(error).__name__
                    trace.mark_error(error)
                else:
                    trace.finish(json.dumps({"value": result, "synthetic": True}))
            path = Path(directory) / "traces-dynamic.json"
            document = json.loads(path.read_text(encoding="utf-8"))
            spans = document["resourceSpans"][0]["scopeSpans"][0]["spans"]
            assert len(spans) == 2
            root, child = spans
            assert child["parentSpanId"] == root["spanId"]
            assert child["traceId"] == root["traceId"]
            assert int(root["startTimeUnixNano"]) <= int(child["startTimeUnixNano"])
            assert int(child["startTimeUnixNano"]) <= int(child["endTimeUnixNano"])
            assert int(child["endTimeUnixNano"]) <= int(root["endTimeUnixNano"])
            definitions = json.loads(span_attributes(root)["gen_ai.tool.definitions"])
            assert [tool["name"] for tool in definitions] == ["bounded_arithmetic"]
            assert span_attributes(child)["gen_ai.operation.name"] == "execute_tool"
            if error_kind is None:
                assert json.loads(span_attributes(child)["gen_ai.tool.call.result"])["value"] == result
                assert child["status"]["code"] == root["status"]["code"] == 1
            else:
                assert child["status"]["code"] == root["status"]["code"] == 2
                assert "gen_ai.tool.call.result" not in span_attributes(child)
        captured = {"document": document, "result": result, "error_kind": error_kind,
                    "tool_called": tool_called, "temporary_path": str(path)}
    assert not path.exists()
    captured["temporary_artifact_removed"] = True
    return captured


def run_native_trace_demo() -> dict:
    success = capture_synthetic_workflow()
    failure = capture_synthetic_call("1 / 0")
    assert failure["error_kind"] == "SafeArithmeticError"
    return {
        "synthetic": True,
        "actual_tool_chain_called": [tool["name"] for tool in WORKFLOW_TOOLS],
        "success_span_count": 4,
        "parent_child_ids_verified": True,
        "execution_interval_contains_tool_call": True,
        "recorded_result_matches_actual_result": True,
        "root_output_is_checked_final_reply": True,
        "wrong_numeric_claim_removed": success["delivered"]["deleted_count"] == 1,
        "remaining_review_requirement_preserved": success["delivered"]["needs_review"],
        "failure_status_recorded": True,
        "declared_tool_count": 3,
        "model_calls": 0,
        "historical_tool_catalog_not_advertised": True,
        "temporary_artifacts_removed": success["temporary_artifact_removed"] and failure["temporary_artifact_removed"],
        "boundary": "条款、待核验分值和候选文案均为合成；未运行规范评分或模型。实际测量包裹的执行区间，不是全产品自动插桩、工程认证或官方赛事校验。",
    }


def main() -> None:
    print(json.dumps(run_native_trace_demo(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
