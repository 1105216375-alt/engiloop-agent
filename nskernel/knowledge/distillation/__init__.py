"""nskernel.knowledge.distillation · 知识蒸馏(系统越用越聪明)"""
from .distiller import (
    KnowledgeType, DistilledCandidate, AdmissionAction, AdmissionDecision,
    DistillerBase, RuleDistiller, FormulaDistiller, AlgorithmDistiller,
    KnowledgeDistiller, ReviewPipeline, KnowledgeLibrary, DistillationPipeline,
)
__all__ = [
    "KnowledgeType", "DistilledCandidate",
    "AdmissionAction", "AdmissionDecision",
    "DistillerBase", "RuleDistiller", "FormulaDistiller", "AlgorithmDistiller",
    "KnowledgeDistiller", "ReviewPipeline", "KnowledgeLibrary",
    "DistillationPipeline",
]
