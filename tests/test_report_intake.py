"""Synthetic report parsing; no real bridge files, model calls, or disk export."""
from io import BytesIO
import socket

from docx import Document
import pytest

from engineering_agent.report_input import parse_report
from examples.report_intake import synthetic_report_bytes


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Report intake tests must remain offline")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def _report(headers, rows):
    document = Document()
    table = document.add_table(rows=1, cols=len(headers))
    for cell, value in zip(table.rows[0].cells, headers):
        cell.text = value
    for values in rows:
        for cell, value in zip(table.add_row().cells, values):
            cell.text = value
    stream = BytesIO()
    document.save(stream)
    return stream.getvalue()


def test_real_parser_extracts_synthetic_report_and_skips_repaired_row():
    result = parse_report(synthetic_report_bytes(), document_id="synthetic.docx")
    assert len(result["records"]) == 2
    assert result["skipped_rows_in_supported_tables"] == 1
    crack, exposed = result["records"]
    assert crack["extent_data"]["length"] == pytest.approx(.8)
    assert crack["extent_data"]["width"] == pytest.approx(.3)
    assert exposed["extent_data"]["area"] == pytest.approx(.15)
    assert crack["source"]["table"] == 1
    assert crack["source"]["row"] == 2
    assert crack["source"]["description_column"] == 4
    assert crack["source"]["document_id"] == "synthetic.docx"
    assert crack["source"]["sha256"] == result["sha256"]


def test_estimated_scale_is_never_claimed_as_reported_or_verified():
    result = parse_report(synthetic_report_bytes())
    for record in result["records"]:
        assert record["_scale_source"] == "estimated"
        assert record["scale_is_reported_fact"] is False
        assert record["engineering_verified"] is False
        assert record["eligible_for_scoring"] is False


def test_duplicate_descriptions_retain_distinct_rows():
    text = "1号梁梁底裂缝1处，l=0.8m，w=0.3mm"
    payload = _report(["位置", "种类", "具体描述"], [
        ["主梁", "裂缝", text], ["主梁", "裂缝", text]])
    result = parse_report(payload)
    assert [record["source"]["row"] for record in result["records"]] == [2, 3]


def test_independent_measurement_columns_keep_original_priority_and_sources():
    payload = _report(["位置", "种类", "具体描述", "长度(m)", "宽度(mm)"], [[
        "主梁", "裂缝", "1号梁梁底裂缝1处，l=0.8m，w=0.3mm", "1.2", "0.4"]])
    record = parse_report(payload)["records"][0]
    assert record["extent_data"]["length"] == pytest.approx(1.2)
    assert record["extent_data"]["width"] == pytest.approx(.4)
    assert record["source"]["candidate_source_columns"]["length"] == [4, 3]
    assert record["source"]["candidate_source_columns"]["width"] == [5, 3]


def test_unsupported_tables_are_reported_not_invented():
    payload = _report(["名称", "备注"], [["合成记录", "没有病害表字段"]])
    result = parse_report(payload)
    assert result["records"] == []
    assert result["unsupported_table_count"] == 1
    assert result["supported_table_count"] == 0


def test_path_input_reports_basename_not_private_absolute_path(tmp_path):
    path = tmp_path / "synthetic.docx"
    path.write_bytes(synthetic_report_bytes())
    result = parse_report(path)
    assert result["document_id"] == "synthetic.docx"
    assert str(tmp_path) not in str(result)
