"""Audit contract for the standard maintenance recommendation report."""

from __future__ import annotations

import hashlib
import os
from typing import Any, Dict


REPORT_TEXT_WITHHELD = "本节自动生成内容未通过证据核验，已停止展示。请核对原始资料后重新生成。"


def _validated_cleaned_text(report: Any) -> str:
    """Reject incomplete verifier responses instead of treating them as success."""
    cleaned = getattr(report, "cleaned", None)
    original = getattr(report, "original", None)
    if not isinstance(cleaned, str) or not isinstance(original, str):
        raise ValueError("验证器未返回有效的原文及净化文本")
    for field in ("deleted_count", "flagged_count"):
        value = getattr(report, field, None)
        if type(value) is not int or value < 0:
            raise ValueError(f"验证器返回无效的{field}")
    issues = getattr(report, "issues", None)
    if not isinstance(issues, (list, tuple)):
        raise ValueError("验证器未返回有效的问题记录")
    hard_issues = soft_issues = 0
    for issue in issues:
        severity = getattr(issue, "severity", None)
        if severity not in ("hard", "soft"):
            raise ValueError("验证器返回无效的问题级别")
        if severity == "hard":
            hard_issues += 1
            sentence = getattr(issue, "sentence", None)
            if not isinstance(sentence, str) or not sentence.strip():
                raise ValueError("硬性问题缺少原句定位")
            if sentence.strip() in cleaned:
                raise ValueError("净化文本仍包含已判定的硬性错误原句")
        else:
            soft_issues += 1
    if bool(hard_issues) != bool(report.deleted_count) or bool(soft_issues) != bool(report.flagged_count):
        raise ValueError("验证器的问题记录与净化计数不一致")
    return cleaned.strip()


def verified_report_text(report: Any) -> str:
    """The only permitted fallback is an explicit withholding notice, never the draft."""
    return _validated_cleaned_text(report) or REPORT_TEXT_WITHHELD


def _issue_rows(report: Any, section: str) -> list[Dict[str, str]]:
    rows = []
    for issue in list(getattr(report, "issues", None) or [])[:12]:
        rows.append({
            "section": section,
            "severity": str(getattr(issue, "severity", "") or "unknown"),
            "category": str(getattr(issue, "category", "") or "unknown"),
            "detail": str(getattr(issue, "detail", "") or "")[:240],
        })
    return rows


def summarize_claim_verification(
    cause_report: Any,
    suggestion_report: Any,
    *,
    final_causes: str,
    final_suggestions: str,
) -> Dict[str, Any]:
    """Audit the actual text handed to the writer, not just the verifier's counters."""
    reports = (
        ("病害原因分析", cause_report, final_causes),
        ("处治建议", suggestion_report, final_suggestions),
    )
    deleted_count = 0
    flagged_count = 0
    issues = []
    output_bindings = []
    withheld_sections = []
    mismatched_sections = []
    for section, report, final_text in reports:
        cleaned = _validated_cleaned_text(report)
        if not isinstance(final_text, str):
            raise ValueError("送入报告排版的正文不是有效文本")
        deleted_count += report.deleted_count
        flagged_count += report.flagged_count
        issues.extend(_issue_rows(report, section))
        expected = cleaned or REPORT_TEXT_WITHHELD
        matched = final_text == expected
        if not cleaned:
            withheld_sections.append(section)
        if not matched:
            mismatched_sections.append(section)
            issues.append({
                "section": section, "severity": "hard", "category": "output_binding",
                "detail": "实际报告正文与验证器净化结果不一致，禁止判定为通过",
            })
        output_bindings.append({
            "section": section,
            "withheld": not bool(cleaned),
            "matches_safe_delivery": matched,
            "verified_text_sha256": hashlib.sha256(cleaned.encode("utf-8")).hexdigest(),
            "delivered_text_sha256": hashlib.sha256(final_text.encode("utf-8")).hexdigest(),
        })

    if mismatched_sections:
        status = "output_binding_failed"
        label = "报告正文与验证结果不一致，必须人工复核"
    elif withheld_sections:
        status = "output_withheld"
        label = "未形成可交付的已验证正文，相关章节已拦截"
    elif flagged_count:
        status = "verified_with_review"
        label = "已净化，软性问题待人工复核"
    elif deleted_count:
        status = "passed_after_cleanup"
        label = "已删除不合规主张并通过复核"
    else:
        status = "passed_clean"
        label = "通过，未发现可机械判定冲突"

    return {
        "executed": True,
        "passed": not (flagged_count or withheld_sections or mismatched_sections),
        "status": status,
        "label": label,
        "deleted_count": deleted_count,
        "flagged_count": flagged_count,
        "issue_count": len(issues),
        "issues": issues,
        "output_withheld": bool(withheld_sections),
        "withheld_sections": withheld_sections,
        "output_bindings": output_bindings,
        "policy": "LLM仅生成候选文本；数值、构件、病害、规范引用和越界主张由符号侧复核。",
    }


def failed_claim_verification(error: Exception | str) -> Dict[str, Any]:
    return {
        "executed": False,
        "passed": False,
        "status": "verification_failed",
        "label": "验证器执行失败，必须人工复核",
        "deleted_count": 0,
        "flagged_count": 0,
        "issue_count": 1,
        "issues": [{
            "section": "输出验证",
            "severity": "hard",
            "category": "runtime",
            "detail": str(error)[:240],
        }],
        "output_withheld": True,
        "policy": "验证器未完成时，不得把自动生成内容作为正式工程结论发布。",
    }


def build_repair_report_audit(
    *,
    source_path: str,
    results: list[Dict[str, Any]],
    score_provenance: Dict[str, Any] | None,
    bridge_score_recomputed: Dict[str, Any] | None,
    bridge_score_52xx: Dict[str, Any] | None,
    claim_verification: Dict[str, Any],
    compliance: Dict[str, Any] | None,
    regulation_refs: Dict[str, Any] | None,
    environment_context: Dict[str, Any] | None,
) -> Dict[str, Any]:
    """Build the exact audit payload consumed by the DOCX writer and API."""
    recomputed = bridge_score_recomputed or {}
    reliability = recomputed.get("评分可靠性") or {}
    score_provenance = score_provenance or {}
    score_52xx = bridge_score_52xx or {}
    compliance = compliance or {}
    regulation_refs = regulation_refs or {}
    environment_context = environment_context or {}
    strict_count = sum(1 for row in results if row.get("严控区"))
    high_risk_count = sum(1 for row in results if float(row.get("风险评分") or 0) >= 60)

    publication_ready = bool(
        claim_verification.get("executed")
        and claim_verification.get("passed")
        and not claim_verification.get("output_withheld")
        and not score_provenance.get("requires_review")
        and not reliability.get("provisional")
        and int(compliance.get("需复核数") or 0) == 0
        and int(compliance.get("不合规数") or 0) == 0
    )
    if publication_ready:
        publication_label = "自动审计通过，可提交授权人员复核签发"
    else:
        publication_label = "自动审计已完成但仍有待复核项，不替代人工签字"

    return {
        "schema_version": "bridge.repair-report.audit.v1",
        "source": {
            "filename": os.path.basename(source_path or "") or "未提供",
            "fact_count": len(results),
            "high_risk_count": high_risk_count,
            "strict_control_count": strict_count,
            "regulation_group_count": len(regulation_refs),
        },
        "score_provenance": score_provenance,
        "score_reliability": reliability,
        "score_52xx": score_52xx,
        "claim_verification": claim_verification,
        "compliance": {
            "rate": compliance.get("合规率") or "-",
            "total": compliance.get("验证总数") or len(results),
            "compliant": compliance.get("合规数") or 0,
            "needs_review": compliance.get("需复核数") or 0,
            "noncompliant": compliance.get("不合规数") or 0,
        },
        "environment": {
            "province": environment_context.get("province") or "",
            "city": environment_context.get("city") or "",
            "effects": list(environment_context.get("effects") or [])[:8],
            "source": environment_context.get("source") or "",
        },
        "pipeline": [
            "报告解析与事实抽取",
            "病害规则诊断",
            "Dr独立重算与报告发布值双轨对账",
            "BCI影子评定",
            "规范知识检索",
            "ClaimVerifier输出侧净化",
            "规则合规复核",
            "可审计Word输出",
        ],
        "publication": {
            "ready_for_authorized_review": publication_ready,
            "label": publication_label,
            "boundary": "本报告为辅助分析成果，不替代现场复核、专项检测和有权限人员签字；涉及限载、封闭、加固或大修决策时，须另行履行正式决策程序。",
        },
    }
