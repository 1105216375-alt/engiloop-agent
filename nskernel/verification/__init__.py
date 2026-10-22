"""nskernel.verification · 验证能力层"""
from .claim_verifier import (
    ClaimVerifier, ClaimRule, Severity,
    VerificationIssue, VerificationReport,
    ForbiddenWordsRule, HallucinationAuthorityRule,
    NumberMismatchRule, EntityCrossContaminationRule,
)
__all__ = [
    "ClaimVerifier", "ClaimRule", "Severity",
    "VerificationIssue", "VerificationReport",
    "ForbiddenWordsRule", "HallucinationAuthorityRule",
    "NumberMismatchRule", "EntityCrossContaminationRule",
]
