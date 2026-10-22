"""
nskernel.dialogue.reasoning · 多步推理闭环(反哺自 Residual Gated Reasoner)

核心概念:
  - ResidualState  当前还差什么(Kautz Type 6 思维)
  - CandidateStep  proposer 提议的一步
  - VerifierGate   多级裁决(accept/reject/defer/interrupt)
  - StateTransition 一次正式的状态变化
  - LoopManager    propose-verify-update-rebuild 闭环

用法:
  from nskernel.dialogue.reasoning import (
      ResidualState, ResidualStatus,
      CandidateStep, ProposerSource, StepType,
      ReasoningDecision, DecisionAction, ReasonCode,
      VerifierGate, MainStateUpdater, ResidualRebuilder,
      LoopManager, LoopStats,
      SafetyChecker, ResidualReductionChecker, TraceabilityChecker,
  )
"""
from .residual_state import (
    ResidualState, ResidualStatus,
    Goal, UnresolvedItem, Constraint, VerifiedFact, BlockedItem,
)
from .candidate_and_transition import (
    CandidateStep, StateTransition, ProposerSource, StepType,
)
from .decision import (
    ReasoningDecision, VerifierResult, DecisionAction, ReasonCode,
)
from .verifier_gate import (
    VerifierGate, MainStateUpdater, ResidualRebuilder,
    SafetyChecker, ResidualReductionChecker, TraceabilityChecker,
)
from .extended_checkers import (
    ApplicabilityChecker, ConsistencyChecker, DeduplicationChecker,
    ExecutionChecker, LibraryComplianceChecker, NumericalSamplingChecker,
    OperationRuleChecker, SolutionChecker, SoundnessChecker,
    SymbolicEquivalenceChecker, ConfidenceChecker, InputRefsChecker,
)
from .loop_manager import LoopManager, LoopStats, StepSummary
from .strict_orchestrator import (
    StrictOrchestrator, StrictResponse, FactRealizer, StyleRefinement,
)

__all__ = [
    # State
    "ResidualState", "ResidualStatus",
    "Goal", "UnresolvedItem", "Constraint", "VerifiedFact", "BlockedItem",
    # Candidate / Transition
    "CandidateStep", "StateTransition", "ProposerSource", "StepType",
    # Decision
    "ReasoningDecision", "VerifierResult", "DecisionAction", "ReasonCode",
    # Verifier / Updater / Rebuilder
    "VerifierGate", "MainStateUpdater", "ResidualRebuilder",
    # 基础 checker
    "SafetyChecker", "ResidualReductionChecker", "TraceabilityChecker",
    # RRI-9 扩展 checker(12 种)
    "ApplicabilityChecker", "ConsistencyChecker", "DeduplicationChecker",
    "ExecutionChecker", "LibraryComplianceChecker", "NumericalSamplingChecker",
    "OperationRuleChecker", "SolutionChecker", "SoundnessChecker",
    "SymbolicEquivalenceChecker", "ConfidenceChecker", "InputRefsChecker",
    # Loop
    "LoopManager", "LoopStats", "StepSummary",
    # RRI-12 StrictOrchestrator (纯 Kautz Type 6)
    "StrictOrchestrator", "StrictResponse", "FactRealizer", "StyleRefinement",
]
