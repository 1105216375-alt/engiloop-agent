"""
nskernel.reasoning · 多步推理引擎(反哺自 Residual Gated Reasoner)

本包和 nskernel.dialogue.reasoning 的区别:
  - dialogue.reasoning: 状态/候选/转移等【核心数据结构】
  - reasoning:          【组件】proposer/controller/transition

用法:
  from nskernel.reasoning import (
      ProposalScheduler, FormulaProposer, NeuralProposer,
      RuntimeController, BudgetManager, StopPolicy, EscalationPolicy,
  )
"""
from .proposal import (
    ProposerBase, ProposalScheduler,
    FormulaProposer, RetrievalProposer, HeuristicProposer,
    ToolSuggestionProposer, NeuralProposer, HeuristicRule,
)
from .controller import (
    EscalationPolicy, RollbackHandler, BudgetManager, StopPolicy,
    RuntimeController, RunSummary,
)

__all__ = [
    "ProposerBase", "ProposalScheduler",
    "FormulaProposer", "RetrievalProposer", "HeuristicProposer",
    "ToolSuggestionProposer", "NeuralProposer", "HeuristicRule",
    "EscalationPolicy", "RollbackHandler", "BudgetManager", "StopPolicy",
    "RuntimeController", "RunSummary",
]
