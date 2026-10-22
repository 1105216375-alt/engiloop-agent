"""
ResidualState · 残差状态(反哺自 Residual Gated Reasoner)
===========================================================

回答一个问题:**当前还差什么没解决?**

对比传统会话状态:
  传统: "已经有什么" (observations, messages)
  残差: "还差什么"  (goals, unresolved, constraints)

这是把 Kautz Type 6 推理范式引入对话型 AI 的核心抽象。
来自自动定理证明 / proof assistant 的思维方式:
  - Prolog 的 subgoal
  - Lean 的 goal state
  - Coq 的 proof obligation

在 nskernel 里的价值:
  - 显式建模对话进度 — 不是靠"意图识别"猜
  - 明确知道对话何时可以结束 — goals 全 solved
  - 支持复杂多步推理 — 每步缩减 residual

适用场景:
  - 法律:"我能告吗?" → goals=[判断可告性],
    unresolved=[时效/证据/管辖/利益关系]
  - 医疗:"头痛" → goals=[判断病因],
    unresolved=[持续时间/部位/诱因/伴随症状]
  - 教学:"这题不会" → goals=[解决这题],
    unresolved=[先搞懂概念/做类似题/独立完成]
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid
import time


# ══════════════════════════════════════════════════════════════
# 枚举
# ══════════════════════════════════════════════════════════════
class ResidualStatus(str, Enum):
    """残差状态的生命周期"""
    ACTIVE = "active"            # 还在推进
    SOLVED = "solved"            # 目标全部达成
    BLOCKED = "blocked"          # 卡住(可能需要人工)
    FAILED = "failed"            # 不可解 / 超出预算


# ══════════════════════════════════════════════════════════════
# 原子数据
# ══════════════════════════════════════════════════════════════
@dataclass
class Goal:
    """一个目标 - 用户希望达成的结果"""
    goal_id: str
    description: str                       # "判断劳动争议能否胜诉"
    priority: int = 100                    # 优先级(数字小=优先级高)
    solved: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class UnresolvedItem:
    """一个未解决项 - 达成目标路径上需要先完成的子任务"""
    item_id: str
    description: str                       # "诉讼时效是否已过"
    blocking_goals: List[str] = field(default_factory=list)
                                           # 这项会阻塞哪些 goal
    attempts: int = 0                      # 尝试解决的次数
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Constraint:
    """一个约束 - 必须满足的硬条件"""
    constraint_id: str
    description: str                       # "证据必须在时效内"
    constraint_type: str = "hard"          # "hard" / "soft"
    satisfied: Optional[bool] = None       # None=未知, True=满足, False=违反
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class VerifiedFact:
    """已验证的事实 - 符号层确认过的"""
    fact_id: str
    statement: str                         # "用户工作 3 个月未收到工资"
    source: str = ""                       # "user_input" / "llm_inferred" / "symbolic_derived"
    evidence: List[str] = field(default_factory=list)
                                           # 佐证材料(其他 fact_id / observation_id)
    confidence: float = 1.0
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BlockedItem:
    """卡住的点 - 暂时无法推进"""
    blocked_id: str
    description: str
    reason: str                            # 为什么卡住("缺少信息" / "规则冲突" / ...)
    suggestion: str = ""                   # 如何推进("追问用户 X" / "查 Y 规范" / ...)
    metadata: Dict[str, Any] = field(default_factory=dict)


# ══════════════════════════════════════════════════════════════
# ResidualState 主对象
# ══════════════════════════════════════════════════════════════
@dataclass
class ResidualState:
    """
    残差状态 R_k — 一次推理会话的核心状态

    设计:
      - 不可变意图下的可变容器:每次 transition 创建新的 ResidualState
        (通过 evolve() 方法),保留前置快照便于回滚
      - 领域无关:goals/unresolved_items/constraints/verified_facts 都是通用结构
      - 可序列化:to_dict / from_dict 完整支持

    生命周期:
      1. create():                 初始化,status=ACTIVE
      2. register_goal():          注册目标
      3. add_unresolved():         添加未解决项
      4. add_verified_fact():      累积已验证事实
      5. mark_solved():            解决一项
      6. evolve():                 生成下一版本(供 ResidualRebuilder 使用)
      7. is_solved():              所有 goals 已 solved → True
    """
    residual_id: str = field(default_factory=lambda: f"R_{uuid.uuid4().hex[:8]}")
    created_at: float = field(default_factory=time.time)

    # 核心:当前还差什么
    goals: List[Goal] = field(default_factory=list)
    unresolved_items: List[UnresolvedItem] = field(default_factory=list)
    unknowns: List[Dict[str, Any]] = field(default_factory=list)
    constraints: List[Constraint] = field(default_factory=list)

    # 已经得到什么(会持续累积)
    verified_facts: List[VerifiedFact] = field(default_factory=list)

    # 工作记忆(中间推导结果,非最终事实)
    working_memory: List[Dict[str, Any]] = field(default_factory=list)

    # 卡住的点
    blocked_items: List[BlockedItem] = field(default_factory=list)

    # 进度度量(领域自己定义如何算,默认给一个基础版)
    progress_measure: Dict[str, Any] = field(default_factory=dict)

    # 生命周期
    status: ResidualStatus = ResidualStatus.ACTIVE

    description: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    # -----------------------------------------------------------------
    # 状态判定
    # -----------------------------------------------------------------
    def is_solved(self) -> bool:
        return (self.status == ResidualStatus.SOLVED
                or (self.goals and all(g.solved for g in self.goals)))

    def is_blocked(self) -> bool:
        return self.status == ResidualStatus.BLOCKED

    def is_failed(self) -> bool:
        return self.status == ResidualStatus.FAILED

    def is_active(self) -> bool:
        return self.status == ResidualStatus.ACTIVE

    # -----------------------------------------------------------------
    # 进度
    # -----------------------------------------------------------------
    def compute_progress(self) -> float:
        """
        默认进度计算:已解决目标数 / 总目标数
        领域可在 progress_measure 里自定义公式
        """
        custom = self.progress_measure.get("value")
        if custom is not None:
            return float(custom)
        if not self.goals:
            return 0.0
        solved = sum(1 for g in self.goals if g.solved)
        return solved / len(self.goals)

    def unresolved_count(self) -> int:
        return len(self.unresolved_items)

    def verified_count(self) -> int:
        return len(self.verified_facts)

    def blocked_count(self) -> int:
        return len(self.blocked_items)

    # -----------------------------------------------------------------
    # 修改(带校验)
    # -----------------------------------------------------------------
    def register_goal(self, description: str, priority: int = 100,
                      goal_id: Optional[str] = None, **meta) -> Goal:
        """注册一个目标"""
        g = Goal(
            goal_id=goal_id or f"G_{uuid.uuid4().hex[:6]}",
            description=description,
            priority=priority,
            metadata=meta,
        )
        self.goals.append(g)
        return g

    def add_unresolved(self, description: str, blocking_goals: Optional[List[str]] = None,
                       item_id: Optional[str] = None, **meta) -> UnresolvedItem:
        """添加一项未解决项"""
        u = UnresolvedItem(
            item_id=item_id or f"U_{uuid.uuid4().hex[:6]}",
            description=description,
            blocking_goals=blocking_goals or [],
            metadata=meta,
        )
        self.unresolved_items.append(u)
        return u

    def resolve_item(self, item_id: str) -> bool:
        """移除某个未解决项(解决了)"""
        for i, u in enumerate(self.unresolved_items):
            if u.item_id == item_id:
                self.unresolved_items.pop(i)
                return True
        return False

    def add_constraint(self, description: str, constraint_type: str = "hard",
                       **meta) -> Constraint:
        c = Constraint(
            constraint_id=f"C_{uuid.uuid4().hex[:6]}",
            description=description,
            constraint_type=constraint_type,
            metadata=meta,
        )
        self.constraints.append(c)
        return c

    def add_verified_fact(self, statement: str, source: str = "",
                          evidence: Optional[List[str]] = None,
                          confidence: float = 1.0, **meta) -> VerifiedFact:
        """添加一条已验证的事实"""
        f = VerifiedFact(
            fact_id=f"F_{uuid.uuid4().hex[:6]}",
            statement=statement,
            source=source,
            evidence=evidence or [],
            confidence=confidence,
            metadata=meta,
        )
        self.verified_facts.append(f)
        return f

    def mark_goal_solved(self, goal_id: str) -> bool:
        for g in self.goals:
            if g.goal_id == goal_id:
                g.solved = True
                # 如果所有 goals 都 solved,自动切 status
                if all(x.solved for x in self.goals):
                    self.status = ResidualStatus.SOLVED
                return True
        return False

    def mark_blocked(self, description: str, reason: str, suggestion: str = ""):
        """标记卡住"""
        self.blocked_items.append(BlockedItem(
            blocked_id=f"B_{uuid.uuid4().hex[:6]}",
            description=description,
            reason=reason,
            suggestion=suggestion,
        ))
        self.status = ResidualStatus.BLOCKED

    # -----------------------------------------------------------------
    # 进化:生成下一版本(不可变语义的推演)
    # -----------------------------------------------------------------
    def evolve(
        self,
        added_facts: Optional[List[VerifiedFact]] = None,
        removed_unresolved_ids: Optional[List[str]] = None,
        added_unresolved: Optional[List[UnresolvedItem]] = None,
        solved_goals: Optional[List[str]] = None,
        new_status: Optional[ResidualStatus] = None,
    ) -> "ResidualState":
        """
        生成当前 state 的下一个版本(新 residual_id,继承所有旧数据 + 增量改动)
        用于 MainStateUpdater/ResidualRebuilder
        """
        import copy
        new_state = copy.deepcopy(self)
        new_state.residual_id = f"R_{uuid.uuid4().hex[:8]}"
        new_state.created_at = time.time()
        new_state.metadata.setdefault("evolved_from", []).append(self.residual_id)

        if added_facts:
            new_state.verified_facts.extend(added_facts)
        if removed_unresolved_ids:
            for rid in removed_unresolved_ids:
                new_state.resolve_item(rid)
        if added_unresolved:
            new_state.unresolved_items.extend(added_unresolved)
        if solved_goals:
            for gid in solved_goals:
                new_state.mark_goal_solved(gid)
        if new_status:
            new_state.status = new_status

        return new_state

    # -----------------------------------------------------------------
    # 序列化
    # -----------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        from dataclasses import asdict
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ResidualState":
        # 简化版反序列化
        state = cls(
            residual_id=data.get("residual_id", f"R_{uuid.uuid4().hex[:8]}"),
            created_at=data.get("created_at", time.time()),
            description=data.get("description"),
            metadata=data.get("metadata", {}),
            status=ResidualStatus(data.get("status", "active")),
        )
        state.goals = [Goal(**g) if not isinstance(g, Goal) else g
                       for g in data.get("goals", [])]
        state.unresolved_items = [UnresolvedItem(**u) if not isinstance(u, UnresolvedItem) else u
                                  for u in data.get("unresolved_items", [])]
        state.constraints = [Constraint(**c) if not isinstance(c, Constraint) else c
                             for c in data.get("constraints", [])]
        state.verified_facts = [VerifiedFact(**f) if not isinstance(f, VerifiedFact) else f
                                for f in data.get("verified_facts", [])]
        state.blocked_items = [BlockedItem(**b) if not isinstance(b, BlockedItem) else b
                               for b in data.get("blocked_items", [])]
        state.unknowns = data.get("unknowns", [])
        state.working_memory = data.get("working_memory", [])
        state.progress_measure = data.get("progress_measure", {})
        return state

    def summary(self) -> str:
        """人类可读的简要描述"""
        lines = [
            f"ResidualState[{self.residual_id}] status={self.status.value}",
            f"  goals: {sum(1 for g in self.goals if g.solved)}/{len(self.goals)} solved",
            f"  unresolved: {len(self.unresolved_items)}",
            f"  constraints: {len(self.constraints)}",
            f"  verified_facts: {len(self.verified_facts)}",
            f"  blocked: {len(self.blocked_items)}",
            f"  progress: {self.compute_progress():.0%}",
        ]
        return "\n".join(lines)
