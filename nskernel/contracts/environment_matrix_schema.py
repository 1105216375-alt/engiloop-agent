"""
Environment Matrix Schema · 环境修正契约
==========================================

通用领域契约,描述"外部环境/上下文"如何修正某个观察的紧急度/风险/重要性。

设计理念:
  · 任何垂域都有"外部修正因子"
    - 桥梁: 海洋盐雾 / 冻融 / 高温
    - 法律: 司法管辖区 / 时效紧迫度 / 当事人特殊身份
    - 医疗: 患者基础病 / 年龄段 / 季节
    - 教育: 考试时段 / 学生基础 / 学制阶段
  · 这些都是"外在条件"修正"内在严重度"的同构问题

核心模型(借鉴道路医生 EnvAgent 抽象):

  对某个 entity 在某个 environment 下,某 observation 的修正系数:

      amplify(obs) = 1 + Σ_i (k_i × w_i)

      其中:
        k_i = 该 observation 对环境因子 i 的敏感系数 ∈ [0,1]
        w_i = 环境因子 i 在该 entity 当前的强度权重

  整体风险评分:
      RiskScore = Σ_obs (amplify(obs) × count(obs) × severity_weight(obs))

  紧急度修正:
      若任一命中环境因子的 urgency_bump ≥ 1
      则该 observation 的处治紧急度向前跳一档(例:"3个月" → "30天")

  时间窗推荐:
      给出"什么时候处治最合适"的建议(避开恶劣季节,选择干燥/无寒冬等)

设计原则:
  1. 数据外置(JSON),代码无硬编码领域数值
  2. 异常静默降级(返回中性结果,不影响其他流程)
  3. 不是规范权威数据,属于本系统的工程模型
  4. 跨领域可复用,只需领域提供 3 份数据文件:
     · entity_env.json:        实体/位置 → 环境因子集合
     · sensitivity_matrix.json: 观察类型 × 环境因子 → 敏感系数 k
     · treatment_window.json:   环境因子 → 时间窗 / 紧急度修正
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ══════════════════════════════════════════════════════════════
# 数据结构
# ══════════════════════════════════════════════════════════════

@dataclass
class EnvironmentProfile:
    """某个实体/位置的环境画像"""
    entity_key: str              # 实体标识(如城市/区县/桥名)
    factors: List[str] = field(default_factory=list)
    # 环境因子列表(如 ['沿海盐雾','冻融剥落','大温差应力'])
    factor_weights: Dict[str, float] = field(default_factory=dict)
    # 各因子强度权重,默认为 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SensitivityCoefficient:
    """某 observation 对某 environment factor 的敏感度"""
    observation_key: str         # 观察类型(如"龟裂"/"诉讼时效")
    factor: str                  # 环境因子
    k: float                     # 系数 ∈ [0,1]
                                 #   0.0=几乎不影响 / 0.3=轻度 / 0.6=中度 / 0.9=强烈
    rationale: str = ""          # 工程逻辑


@dataclass
class TreatmentWindow:
    """环境因子的处治时间窗 + 紧急度修正"""
    factor: str
    risk_season: str = ""        # "梅雨季+夏季"
    treatment_window: str = ""   # "干燥季(10月-次年3月)"
    urgency_bump: int = 0        # 0=不修正 / 1=升一档 / 2=升两档
    rationale: str = ""


@dataclass
class AmplificationResult:
    """对某 observation 在某 entity 上的环境修正结果"""
    observation_key: str
    base_severity: Optional[str] = None  # 修正前严重度
    amplify_factor: float = 1.0          # 放大系数 (1.0=无修正)
    matched_factors: List[str] = field(default_factory=list)
    risk_score: float = 0.0              # 单条风险评分
    urgency_bump: int = 0                # 累计紧急度跳档数
    suggested_window: str = ""           # 建议处治时间窗
    rationale: List[str] = field(default_factory=list)


@dataclass
class RiskAssessment:
    """整体风险评估(整 entity 或一组 obs)"""
    entity_key: Optional[str] = None
    total_risk_score: float = 0.0
    risk_level: str = ""                  # "低风险"/"中风险"/"高风险"/"紧急风险"
    amplifications: List[AmplificationResult] = field(default_factory=list)
    summary: str = ""


# ══════════════════════════════════════════════════════════════
# 抽象契约
# ══════════════════════════════════════════════════════════════

class EnvironmentMatrixSchema(ABC):
    """
    环境修正契约 - 任何领域要支持环境修正都要实现

    最小实现:
      class MyDomainEnvMatrix(EnvironmentMatrixSchema):
          def get_environment_profile(self, entity_key): ...
          def get_sensitivity(self, observation_key, factor): ...
          def get_treatment_window(self, factor): ...

    其他高阶方法都有默认实现。
    """

    # ────────────────────────────────────────────────────
    # 必须实现 (3 个核心查询)
    # ────────────────────────────────────────────────────

    @abstractmethod
    def get_environment_profile(self, entity_key: str) -> Optional[EnvironmentProfile]:
        """取实体的环境画像 (如某城市/某区域有哪些环境因子)"""
        ...

    @abstractmethod
    def get_sensitivity(
        self, observation_key: str, factor: str,
    ) -> float:
        """取某 observation 对某环境因子的敏感系数 ∈ [0,1]"""
        ...

    @abstractmethod
    def get_treatment_window(self, factor: str) -> Optional[TreatmentWindow]:
        """取某环境因子的处治时间窗"""
        ...

    # ────────────────────────────────────────────────────
    # 默认实现 (高阶推导,领域可覆盖)
    # ────────────────────────────────────────────────────

    def amplify(
        self,
        observation_key: str,
        entity_key: str,
        base_severity: Optional[str] = None,
    ) -> AmplificationResult:
        """
        计算某 observation 在某 entity 下的环境修正

        公式:  amplify = 1 + Σ (k_i × w_i)
        紧急度: 取所有命中 factor 的 max(urgency_bump)
        """
        result = AmplificationResult(
            observation_key=observation_key,
            base_severity=base_severity,
        )

        profile = self.get_environment_profile(entity_key)
        if not profile or not profile.factors:
            return result  # 无环境信息 = 无修正(保持中性)

        accum_amplify = 0.0
        max_bump = 0
        for factor in profile.factors:
            try:
                k = self.get_sensitivity(observation_key, factor)
            except Exception:
                k = 0.0
            if k <= 0:
                continue

            w = profile.factor_weights.get(factor, 1.0)
            contrib = k * w
            accum_amplify += contrib

            result.matched_factors.append(factor)
            result.rationale.append(
                f"{factor}(k={k:.2f}×w={w:.2f}=+{contrib:.2f})"
            )

            # 时间窗 / 紧急度修正
            tw = self.get_treatment_window(factor)
            if tw:
                if tw.urgency_bump > max_bump:
                    max_bump = tw.urgency_bump
                if not result.suggested_window and tw.treatment_window:
                    result.suggested_window = tw.treatment_window

        result.amplify_factor = round(1.0 + accum_amplify, 3)
        result.urgency_bump = max_bump

        # 默认 risk_score 基于 amplify(领域可覆盖)
        sev_weight = self.severity_weight(base_severity)
        result.risk_score = round(result.amplify_factor * sev_weight, 2)

        return result

    def assess_risk(
        self,
        observations: List[Dict[str, Any]],
        entity_key: str,
    ) -> RiskAssessment:
        """
        对一组 observations 评估总风险

        observations: [{"key": str, "count": int, "severity": str}, ...]
        """
        amps: List[AmplificationResult] = []
        total = 0.0
        for o in observations:
            obs_key = o.get("key") or o.get("observation_key", "")
            count = o.get("count", 1)
            severity = o.get("severity")

            amp = self.amplify(obs_key, entity_key, base_severity=severity)
            sev_w = self.severity_weight(severity)
            single_score = amp.amplify_factor * count * sev_w
            total += single_score

            amp.risk_score = round(single_score, 2)
            amps.append(amp)

        return RiskAssessment(
            entity_key=entity_key,
            total_risk_score=round(total, 2),
            risk_level=self.score_to_level(total),
            amplifications=amps,
            summary=f"{entity_key}: 总风险 {total:.1f}, "
                    f"{len(amps)} 项 obs",
        )

    # ────────────────────────────────────────────────────
    # 子类可覆盖 (默认值方法)
    # ────────────────────────────────────────────────────

    def severity_weight(self, severity: Optional[str]) -> float:
        """severity → 权重 (默认 minor=1, moderate=2, severe=3, critical=4)"""
        if severity is None:
            return 1.0
        mapping = {
            "minor": 1.0, "moderate": 2.0, "severe": 3.0, "critical": 4.0,
            "1": 1.0, "2": 1.5, "3": 2.0, "4": 3.0, "5": 4.0,
            1: 1.0, 2: 1.5, 3: 2.0, 4: 3.0, 5: 4.0,
        }
        return mapping.get(str(severity).lower(),
                           mapping.get(severity, 1.0))

    def score_to_level(self, score: float) -> str:
        """风险分 → 等级标签"""
        if score < 50: return "低风险"
        if score < 150: return "中风险"
        if score < 300: return "高风险"
        return "紧急风险"

    # ────────────────────────────────────────────────────
    # 元信息
    # ────────────────────────────────────────────────────

    def list_known_factors(self) -> List[str]:
        """子类可覆盖,返回支持的环境因子全集"""
        return []

    def describe(self) -> Dict[str, Any]:
        return {
            "schema": self.__class__.__name__,
            "factors": self.list_known_factors(),
        }
