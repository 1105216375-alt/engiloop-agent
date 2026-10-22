"""Trace actual public tool execution and assert capability/privacy boundaries."""
from copy import deepcopy
import json
import os
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from bip_runtime.native_tracing import agent_trace, TraceSession, current_trace
from engineering_agent.evidence import InMemoryEvidence, safe_delivery
from engineering_agent.nlp import compile_intake
from engineering_agent.safe_arithmetic import evaluate_arithmetic
from examples.native_trace import (
    ARITHMETIC_TOOL, WORKFLOW_TOOLS, SYNTHETIC_QUERY,
    capture_synthetic_call, capture_synthetic_workflow,
    run_native_trace_demo, span_attributes,
)


def test_default_registry_does_not_advertise_historical_product_tools():
    with TemporaryDirectory() as directory:
        with patch.dict(os.environ, {"ENGINEERING_AGENT_TRACE_DIR": directory}):
            with agent_trace([], conversation_id="synthetic-no-tools") as trace:
                trace.finish("synthetic result")
            document = json.loads((Path(directory) / "traces-dynamic.json").read_text())
    root = document["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    assert json.loads(span_attributes(root)["gen_ai.tool.definitions"]) == []
    assert "BridgeArchiveSearch" not in json.dumps(document)


def test_actual_arithmetic_is_called_inside_recorded_span():
    observed = []

    def spy(expression):
        trace = current_trace()
        assert trace is not None
        assert trace.current_parent_span_id != trace.root_span_id
        value = evaluate_arithmetic(expression)
        observed.append(value)
        return value

    with patch("examples.native_trace.evaluate_arithmetic", side_effect=spy):
        with patch.object(socket.socket, "connect", side_effect=AssertionError("Network forbidden")):
            captured = capture_synthetic_call()
    assert observed == [0.8]
    assert captured["tool_called"]
    assert captured["result"] == 0.8
    assert captured["temporary_artifact_removed"]
    assert not Path(captured["temporary_path"]).exists()
    spans = captured["document"]["resourceSpans"][0]["scopeSpans"][0]["spans"]
    root, child = spans
    assert child["traceId"] == root["traceId"]
    assert child["parentSpanId"] == root["spanId"]
    assert json.loads(span_attributes(child)["gen_ai.tool.call.result"])["value"] == 0.8
    assert all(span_attributes(row)["gen_ai.operation.name"] != "chat" for row in spans)
    assert current_trace() is None


def test_actual_workflow_calls_are_in_spans_and_results_flow_to_delivery():
    observed = []
    actual_retrieve = InMemoryEvidence.retrieve

    def record_call(name):
        trace = current_trace()
        assert trace is not None
        assert trace.current_parent_span_id != trace.root_span_id
        observed.append((name, trace.current_parent_span_id))

    def intake_spy(text, **kwargs):
        record_call("compile_intake")
        return compile_intake(text, **kwargs)

    def retrieve_spy(evidence, component, disease, **kwargs):
        record_call("evidence.retrieve")
        assert (component, disease) == ("主梁", "斜向裂缝")
        return actual_retrieve(evidence, component, disease, **kwargs)

    def delivery_spy(draft, rows, **kwargs):
        record_call("safe_delivery")
        # Retrieval contributes the actual clause identifier to the candidate.
        assert "JTG 0000-2099 第4.1条" in draft
        assert kwargs["original_query"] == SYNTHETIC_QUERY
        # These are supplied fixture values, never the parser's default scale.
        assert rows == [{"构件": "主梁", "病害": "斜向裂缝", "风险评分": 80,
                         "标度": 3, "DP值": 25}]
        return safe_delivery(draft, rows, **kwargs)

    with patch("examples.native_trace.compile_intake", side_effect=intake_spy), \
         patch.object(InMemoryEvidence, "retrieve", new=retrieve_spy), \
         patch("examples.native_trace.safe_delivery", side_effect=delivery_spy), \
         patch.object(socket.socket, "connect", side_effect=AssertionError("Network forbidden")):
        captured = capture_synthetic_workflow()
    assert [name for name, _ in observed] == [tool["name"] for tool in WORKFLOW_TOOLS]
    spans = captured["document"]["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert [span_id for _, span_id in observed] == [span["spanId"] for span in spans[1:]]
    assert captured["retrieved"][0]["_source"] == "graph"
    assert captured["intake"]["semantic_frame"]["constraints"]["generate_report"] is False
    assert captured["intake"]["semantic_frame"]["semantic_v2"]["arbitration"]["neural_triggered"] is False
    assert captured["delivered"]["deleted_count"] == 1
    assert captured["delivered"]["needs_review"]
    assert not captured["delivered"]["engineering_verified"]
    assert "风险评分为30" in captured["draft"]
    assert "风险评分为30" not in captured["delivered"]["text"]
    assert "风险评分为80" in captured["delivered"]["text"]
    final_messages = json.loads(span_attributes(spans[0])["gen_ai.output.messages"])
    assert final_messages[0]["parts"][0]["content"] == captured["delivered"]["text"]
    assert all(span_attributes(row)["gen_ai.operation.name"] != "chat" for row in spans)
    assert not Path(captured["temporary_path"]).exists()
    assert current_trace() is None


def test_rejected_arithmetic_records_failure_not_fabricated_result():
    captured = capture_synthetic_call("1 / 0")
    assert captured["result"] is None
    assert captured["error_kind"] == "SafeArithmeticError"
    spans = captured["document"]["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert all(row["status"]["code"] == 2 for row in spans)
    assert "gen_ai.tool.call.result" not in span_attributes(spans[1])
    assert current_trace() is None


def test_explicit_tool_definitions_are_copied_not_linked():
    definitions = [deepcopy(ARITHMETIC_TOOL)]
    trace = TraceSession([], conversation_id="synthetic-copy", tool_definitions=definitions)
    definitions[0]["name"] = "mutated"
    definitions[0]["parameters"]["required"].append("mutated")
    assert trace.tool_definitions == [ARITHMETIC_TOOL]


@pytest.mark.parametrize("definitions", [{"name": "not-a-list"}, ["not-a-mapping"], "historical"])
def test_malformed_explicit_registry_is_rejected(definitions):
    with pytest.raises(TypeError):
        agent_trace([], conversation_id="synthetic-invalid", tool_definitions=definitions)


def test_demo_summary_contains_no_prompt_or_document_payload():
    summary = run_native_trace_demo()
    assert summary["actual_tool_chain_called"] == [tool["name"] for tool in WORKFLOW_TOOLS]
    assert summary["success_span_count"] == 4
    assert summary["wrong_numeric_claim_removed"]
    assert summary["root_output_is_checked_final_reply"]
    assert summary["remaining_review_requirement_preserved"]
    assert summary["failure_status_recorded"]
    assert summary["model_calls"] == 0
    assert summary["temporary_artifacts_removed"]
    assert "document" not in summary
    assert "input_messages" not in summary
    assert "temporary_path" not in summary
