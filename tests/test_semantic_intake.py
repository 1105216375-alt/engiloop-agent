"""Offline tests for extracted NLU and explicitly new public intake adapters."""
import json
import socket
from datetime import date

import pytest

from engineering_agent.nlp import (
    MeasurementIntakeSession, compile_intake, parse_chat_query,
)
from engineering_agent.query_normalizer import QueryNormalizer


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("These synthetic intake tests must not use the network")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def test_preprocessor_is_pure_and_preserves_numeric_text():
    assert QueryNormalizer._pre_normalize("主梁　宽０．３ｍｍ！") == "主梁 宽0.3mm"


def test_extracted_parser_normalizes_units_and_keeps_scale_provenance():
    item = parse_chat_query("主梁裂缝，长80cm，宽0.03cm。")[0]
    assert item["comp"] == "主梁"
    assert item["width"] == pytest.approx(.3)
    assert item["length"] == pytest.approx(.8)
    assert item["_scale_explicit"] is False
    result = compile_intake("主梁裂缝，长80cm，宽0.03cm。")
    assert result["semantic_frame"]["defect_mentions"][0]["scale"] is None
    assert result["engineering_verified"] is False


def test_multi_component_values_do_not_form_cartesian_pairs():
    items = parse_chat_query("主梁裂缝宽0.2mm；支座脱空，标度3")
    assert [(item["comp"], item["disease"]) for item in items] == [
        ("主梁", "裂缝"), ("支座", "支座脱空")]
    assert items[0]["width"] == pytest.approx(.2)
    assert items[1]["width"] == 0
    assert items[1]["scale"] == 3


@pytest.mark.parametrize("text", [
    "主梁没说有裂缝，只是建议复检", "支座是不是脱空", "主梁疑似裂缝",
])
def test_uncertain_or_unreported_disease_is_not_eligible(text):
    result = compile_intake(text)
    assert result["blocked_items"]
    assert not result["semantically_eligible_items"]


def test_temporal_scope_and_no_report_are_compiled():
    result = compile_intake("比较2023和2025年的主梁病害，修过的别算，也别出Word",
                            current_date=date(2030, 1, 1))
    frame = result["semantic_frame"]
    assert frame["entities"]["years"] == [2023, 2025]
    assert frame["constraints"]["generate_report"] is False
    assert frame["constraints"]["exclude_repaired"] is True
    assert result["residual_plan"]


def test_explicit_fake_model_cannot_promote_a_question_to_fact():
    calls = []
    def fake_model(system, user):
        calls.append((system, user))
        return json.dumps({"propositions": [{
            "subject": "支座", "predicate": "defect_exists", "object": "支座脱空",
            "existence": "present", "epistemic_status": "confirmed",
            "source_span": "支座是不是脱空", "confidence": .99,
        }]}, ensure_ascii=False)
    result = compile_intake("支座是不是脱空", llm_call=fake_model, force_neural=True)
    assert len(calls) == 1
    assert not result["semantically_eligible_items"]
    arbitration = result["semantic_frame"]["semantic_v2"]["arbitration"]
    assert arbitration["hard_gate"] == "no_neural_fact_promotion"
    assert arbitration["conflicts"]


def test_unanchored_model_fields_are_discarded():
    def fake_model(system, user):
        return json.dumps({"entities": {"years": [
            {"value": 2099, "source_span": "2099年", "confidence": .99}]}}, ensure_ascii=False)
    result = compile_intake("这桥还能用几年", llm_call=fake_model, force_neural=True)
    assert 2099 not in result["semantic_frame"]["entities"]["years"]


def test_colloquial_gaps_narrow_without_converting_analogy_to_measurement():
    session = MeasurementIntakeSession()
    first = session.ingest("1-1号T梁梁底有一处锈胀露筋。")
    second = session.ingest("在距0号台6米、距右侧0米的位置，一巴掌长、两指宽。")
    third = session.ingest("尺子量过：长1.5米，宽十厘米。")
    assert len(first["missing_fields"]) > len(second["missing_fields"]) > len(third["missing_fields"])
    assert "length_m" not in second["symbol"]
    assert third["symbol"]["length_m"] == pytest.approx(1.5)
    assert third["symbol"]["width_m"] == pytest.approx(.1)
    assert third["symbol"]["side_offset"]["distance_m"] == 0
    assert third["provenance"]["width_m"]["source_span"] == "宽十厘米"
    assert third["provenance"]["width_m"]["evidence_status"] == "user_reported_measurement"
    assert third["residual"]["verification_gaps"]
    assert third["record_status"] == "待复核"
    assert third["eligible_for_scoring"] is False


def test_crack_width_uses_mm_and_length_uses_m():
    session = MeasurementIntakeSession()
    session.ingest("1-1号T梁梁底有裂缝。")
    result = session.ingest("尺子量到长八十厘米，测缝卡读数零点三毫米。")
    assert result["symbol"]["length_m"] == pytest.approx(.8)
    assert result["symbol"]["width_mm"] == pytest.approx(.3)


@pytest.mark.parametrize("text", [
    "长1.5米、宽10厘米", "尺子量过，大概长1.5米、宽10厘米",
    "没用尺子量，长1.5米、宽10厘米", "假装尺子量过长1.5米、宽10厘米",
    "尺子量过长1.5，宽10", "一巴掌长、两指宽",
])
def test_missing_unit_rough_estimate_or_unmeasured_is_not_accepted(text):
    session = MeasurementIntakeSession()
    session.ingest("1-1号T梁梁底锈胀露筋。")
    result = session.ingest(text)
    assert "length_m" not in result["symbol"]
    assert "width_m" not in result["symbol"]
    assert result["eligible_for_scoring"] is False


def test_conflicting_measurements_are_not_silently_overwritten():
    session = MeasurementIntakeSession()
    session.ingest("1-1号T梁梁底锈胀露筋。")
    session.ingest("尺子量过长1.5米、宽10厘米。")
    result = session.ingest("尺子量过长3米、宽10厘米。")
    assert result["symbol"]["length_m"] == pytest.approx(1.5)
    assert result["conflicts"][0]["field"] == "length_m"
    assert "人工核对" in result["next_question"]
