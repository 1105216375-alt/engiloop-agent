"""
Causal Chain Schema · 因果链契约
====================================

通用领域契约,描述"多个观察组合 → 系统性因果根因 + 联合处治方案"。

设计理念:
  · 任何垂域都有"孤立看不到 + 组合才能识别"的系统性问题
    - 桥梁: 渗水 + 锈胀露筋 + 混凝土剥落 → 钢筋锈蚀级联链
    - 道路: 排水不畅 + 水毁冲沟 → 排水失效级联链  
    - 法律: 多次违约 + 协议无书面记录 + 时效未过 → 商业欺诈链
    - 医疗: 咳嗽 + 发烧 + 肌肉酸痛 → 流感
    - 教育: 加减法不熟 + 乘法表错乱 + 应用题反应慢 → 数学基础链断裂

  · 都是"症状群 → 共同根因 → 优于逐项处理的联合方案"

核心结构(借鉴道路医生 damage_chains):

  CausalChain {
    id              : 链 ID
    name            : 链名("排水失效级联链")
    severity        : 严重度 ('low'/'medium'/'high'/'critical')
    confidence      : 置信度 ('low'/'medium'/'high')
    conditions      : [触发条件](必须全部满足)
        role            : 该条件在链中的角色("排水失效"/"水毁结果")
        observation_keywords : 匹配的观察关键词
        min_count       : 最少出现次数
        spatial_match   : 空间约束("same_section"/"any"/...)
    inferred_root_cause : 推断的根因(自然语言)
    joint_treatment    : 联合处治方案(列表)
    related_authorities: 关联规范条款
    source             : 知识来源
  }

工作流程:
  1. ChainProposer    : 扫所有 obs,按 conditions 匹配,提候选链
  2. ChainVerifier    : 校验空间约束/严重度门槛
  3. ChainOutput      : 通过的链 + 根因 + 联合处治
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ══════════════════════════════════════════════════════════════
# 数据结构
# ══════════════════════════════════════════════════════════════

@dataclass
class ChainCondition:
    """链中的一个触发条件"""
    role: str                                # 角色 ("排水失效")
    observation_keywords: List[str] = field(default_factory=list)
    # 哪些观察名/关键词命中算这一条件
    min_count: int = 1                       # 最少出现几次
    spatial_match: str = "any"               # any / same_section / adjacent / ...
    severity_min: Optional[str] = None       # 最低严重度门槛
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CausalChain:
    """一条因果链定义"""
    chain_id: str
    name: str                                # "排水失效级联链"
    category: str = ""                       # "水毁连锁"
    severity: str = "medium"                 # 链的整体严重度
    confidence: str = "high"                 # 置信度

    conditions: List[ChainCondition] = field(default_factory=list)

    inferred_root_cause: str = ""            # 推断的根因
    joint_treatment: List[str] = field(default_factory=list)
    # 联合处治方案

    related_authorities: List[str] = field(default_factory=list)
    # 关联规范引用(如 ["JTG 5142-2019 第 6.4 条"])

    source: str = ""                         # 知识来源
    description: str = ""                    # 链描述
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ChainMatch:
    """一条链被命中的实例"""
    chain: CausalChain
    matched_observations: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    # role → 匹配到的 observation 列表
    confidence_score: float = 0.0            # 计算后的置信分
    is_critical: bool = False                # 是否升级为关键
    audit_log: List[str] = field(default_factory=list)


@dataclass
class CausalAnalysisResult:
    """因果分析整体输出"""
    chains: List[ChainMatch] = field(default_factory=list)
    audit_log: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def has_critical(self) -> bool:
        return any(c.is_critical for c in self.chains)

    def critical_chains(self) -> List[ChainMatch]:
        return [c for c in self.chains if c.is_critical]


# ══════════════════════════════════════════════════════════════
# 抽象契约
# ══════════════════════════════════════════════════════════════

class CausalChainSchema(ABC):
    """
    因果链契约 - 任何领域要做"症状群推理"都要实现

    最小实现:
      class MyDomainChains(CausalChainSchema):
          def list_chains(self) -> List[CausalChain]: ...

      其他都用默认实现的 Proposer + Verifier。
    """

    # ────────────────────────────────────────────────────
    # 必须实现
    # ────────────────────────────────────────────────────

    @abstractmethod
    def list_chains(self) -> List[CausalChain]:
        """返回本领域的全部因果链定义"""
        ...

    # ────────────────────────────────────────────────────
    # 默认实现 - Proposer 阶段
    # ────────────────────────────────────────────────────

    def propose_matches(
        self,
        observations: List[Dict[str, Any]],
        section_id: Optional[str] = None,
    ) -> List[ChainMatch]:
        """
        遍历所有链,按条件匹配,提候选

        observations: [{"observation": str, "section": str, "severity": str, ...}]
        """
        matches: List[ChainMatch] = []

        for chain in self.list_chains():
            cm = ChainMatch(
                chain=chain,
                matched_observations={},
                audit_log=[f"开始检查链 {chain.chain_id}: {chain.name}"],
            )

            all_conditions_met = True
            for cond in chain.conditions:
                hits = self._match_condition(observations, cond, section_id)
                if len(hits) < cond.min_count:
                    all_conditions_met = False
                    cm.audit_log.append(
                        f"  ✗ 条件 {cond.role} 不足 (need {cond.min_count}, got {len(hits)})"
                    )
                    break
                cm.matched_observations[cond.role] = hits
                cm.audit_log.append(
                    f"  ✓ 条件 {cond.role}: 命中 {len(hits)} 项"
                )

            if all_conditions_met:
                # 默认置信度根据 chain.confidence 转换
                cm.confidence_score = {"high": 0.9, "medium": 0.7, "low": 0.5}.get(
                    chain.confidence, 0.5,
                )
                cm.is_critical = chain.severity in ("high", "critical")
                matches.append(cm)

        return matches

    def _match_condition(
        self,
        observations: List[Dict[str, Any]],
        condition: ChainCondition,
        section_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """匹配某个 condition 是否被 observations 满足"""
        hits = []
        for obs in observations:
            obs_name = (obs.get("observation") or obs.get("disease")
                        or obs.get("name") or "")
            # 关键词匹配
            matched_kw = False
            for kw in condition.observation_keywords:
                if kw in obs_name:
                    matched_kw = True; break
            if not matched_kw:
                continue

            # 严重度门槛
            if condition.severity_min:
                obs_sev = (obs.get("severity") or "").lower()
                if not self._severity_meets(obs_sev, condition.severity_min):
                    continue

            # 空间约束
            if condition.spatial_match == "same_section" and section_id:
                obs_section = obs.get("section") or obs.get("entity_key")
                if obs_section and obs_section != section_id:
                    continue

            hits.append(obs)

        return hits

    @staticmethod
    def _severity_meets(obs_sev: str, threshold: str) -> bool:
        """obs 严重度 >= threshold ?"""
        rank = {"minor": 1, "moderate": 2, "severe": 3, "critical": 4,
                "low": 1, "medium": 2, "high": 3}
        return rank.get(obs_sev, 1) >= rank.get(threshold, 1)

    # ────────────────────────────────────────────────────
    # Verifier 阶段(可覆盖)
    # ────────────────────────────────────────────────────

    def verify_match(self, match: ChainMatch) -> bool:
        """对候选 match 做最终验证(子类可加自定义校验)"""
        return True

    # ────────────────────────────────────────────────────
    # 主入口
    # ────────────────────────────────────────────────────

    def analyze(
        self,
        observations: List[Dict[str, Any]],
        section_id: Optional[str] = None,
    ) -> CausalAnalysisResult:
        """对一组观察做因果链分析"""
        result = CausalAnalysisResult()

        if not observations:
            result.warnings.append("无 observations 输入")
            return result

        try:
            candidates = self.propose_matches(observations, section_id)
            result.audit_log.append(
                f"Proposer: {len(candidates)} 候选链通过初筛"
            )

            for cm in candidates:
                if self.verify_match(cm):
                    result.chains.append(cm)
                    result.audit_log.append(
                        f"  ✓ {cm.chain.chain_id} 通过 Verifier"
                    )
                else:
                    result.audit_log.append(
                        f"  ✗ {cm.chain.chain_id} 未通过 Verifier"
                    )
        except Exception as e:
            result.warnings.append(f"分析异常: {e}")

        return result

    # ────────────────────────────────────────────────────
    # 元信息
    # ────────────────────────────────────────────────────

    def chain_count(self) -> int:
        try:
            return len(self.list_chains())
        except Exception:
            return 0

    def describe(self) -> Dict[str, Any]:
        return {
            "schema": self.__class__.__name__,
            "chain_count": self.chain_count(),
        }
