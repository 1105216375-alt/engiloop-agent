"""
EntityProjection · 多实体作用域投影 ⭐ 核心创新
================================================

原型系统 里解决的原始问题:
  用户先问实体 A,再问实体 B,然后追问"实体 A 相关问题"
  → 系统默认会把两个实体的观察数据都喂给 LLM
  → LLM 容易把海口的数据混入泉州的回答(严重幻觉)

解决方案:
  "Entity-Scoped Context Projection"
  ——从数据源头就按实体隔离,而不是在 prompt 里喊"只看X"让 LLM 自己过滤。

  1. detect_active_entity: 从问题推断激活的实体 key
  2. project_observations: 把累积观察按 key 过滤成单实体视图
  3. render_context: 把单实体视图渲染成 LLM 可读的上下文

提纯成果(对比原型系统旧版):
  - 旧版 memory.py 里 _bridge_key_of / list_bridges / detect_active_bridge /
    render_bridge_context 全部硬编码某业务词
  - 新版完全依赖 EntityScheme 契约,零领域词
  - 任何实现了 EntityScheme 的领域自动获得多实体投影能力

工作流:
     用户问"某实体 A 的相关问题"
           │
           ▼
  ┌─────────────────────┐
  │ EntityScheme        │ ← 领域实现(例如:按城市为 key、法律用案号...)
  │   .detect_active    │
  └─────────────────────┘
           │
           ▼ entity_key = "泉州"
  ┌─────────────────────┐
  │ ContextProjector    │ ← 通用算法
  │   .project          │ 过滤 observations[city==泉州]
  └─────────────────────┘
           │
           ▼ 只含"实体 A"的观察列表
  ┌─────────────────────┐
  │ ContextRenderer     │ ← 通用算法
  │   .render           │ 渲染成带"作用域标记"的 LLM context
  └─────────────────────┘
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from nskernel.contracts import (
        EntityScheme, EntityRef, DetectionResult,
        ObservationSchema, Observation,
    )


# ══════════════════════════════════════════════════════════════
# 投影结果数据结构
# ══════════════════════════════════════════════════════════════
@dataclass
class ProjectionResult:
    """投影结果 - 按一个实体 key 过滤后的视图"""
    entity_key: Optional[str]                    # 投影的实体 key,None=未投影(全量)
    observations: List["Observation"] = field(default_factory=list)
                                                 # 属于该实体的观察
    other_entity_keys: List[str] = field(default_factory=list)
                                                 # 累积里其他实体的 key(给 LLM 看,提醒不要引用)
    is_scoped: bool = False                      # True=按实体隔离了, False=全量未投影
    detection_source: str = ""                   # 'explicit'/'reference'/'unique'/'none'/'not_found'


# ══════════════════════════════════════════════════════════════
# ContextProjector - 通用投影算法
# ══════════════════════════════════════════════════════════════
class ContextProjector:
    """
    通用多实体作用域投影器

    依赖注入:
      - entity_scheme: 领域实现的 EntityScheme 契约
      - observation_schema: 领域实现的 ObservationSchema 契约
        (可选,只在 render 时用到领域特定的摘要格式)
    """

    def __init__(
        self,
        entity_scheme: "EntityScheme",
        observation_schema: Optional["ObservationSchema"] = None,
    ):
        self._entity_scheme = entity_scheme
        self._observation_schema = observation_schema

    # -----------------------------------------------------------------
    # 核心能力 1:列出累积观察里涉及的所有实体
    # -----------------------------------------------------------------
    def list_entities(
        self,
        observations: List["Observation"],
    ) -> List["EntityRef"]:
        """
        从观察列表里提取所有涉及的实体(去重 + 按最近出现排序)

        返回:按"最近出现倒序"的 EntityRef 列表
            (最新观察到的实体在前面,便于 detect_active_entity 里"取最近")
        """
        from nskernel.contracts import EntityRef

        # 把每个 entity_key 对应的观察分组
        groups: Dict[str, Dict[str, Any]] = {}
        for idx, obs in enumerate(observations):
            key = obs.entity_key or self._entity_scheme.extract_entity_key(obs.raw_data)
            if not key:
                continue
            key = self._entity_scheme.normalize_entity_key(key)
            if key not in groups:
                groups[key] = {
                    "key": key,
                    "display_name": obs.entity_display_name or key,
                    "first_idx": idx,
                    "last_idx": idx,
                    "observations": [],
                }
            # 如果后续 obs 有 display_name 但之前没捕到,补上
            if obs.entity_display_name and groups[key]["display_name"] == key:
                groups[key]["display_name"] = obs.entity_display_name
            groups[key]["last_idx"] = idx
            groups[key]["observations"].append(obs)

        refs = []
        for g in groups.values():
            refs.append(EntityRef(
                key=g["key"],
                display_name=g["display_name"],
                metadata={
                    "observation_count": len(g["observations"]),
                    "first_seen_idx": g["first_idx"],
                    "last_seen_idx": g["last_idx"],
                },
            ))
        # 按 last_seen_idx 倒序 — 最近的实体排在前面
        refs.sort(key=lambda r: -r.metadata.get("last_seen_idx", 0))
        return refs

    # -----------------------------------------------------------------
    # 核心能力 2:从问题推断激活的实体
    # -----------------------------------------------------------------
    def detect_active(
        self,
        question: str,
        observations: List["Observation"],
    ) -> "DetectionResult":
        """
        根据问题+累积观察,推断用户这次追问指向哪个实体。
        这是 EntityScheme.detect_active_entity 的直接调用。
        """
        available = self.list_entities(observations)
        return self._entity_scheme.detect_active_entity(question, available)

    # -----------------------------------------------------------------
    # 核心能力 3:按实体 key 投影观察列表
    # -----------------------------------------------------------------
    def project(
        self,
        observations: List["Observation"],
        entity_key: Optional[str],
    ) -> ProjectionResult:
        """
        把累积观察按实体 key 过滤成"单实体视图"

        Args:
          observations: 累积的全部观察
          entity_key: 要投影到的实体 key。
                      None → 不投影,返回全部

        Returns:
          ProjectionResult{
              entity_key: 当前作用域的实体 key
              observations: 属于该实体的观察
              other_entity_keys: 累积里其他实体的 key(提醒 LLM 不要引用)
              is_scoped: 是否真的做了投影
          }
        """
        if entity_key is None:
            return ProjectionResult(
                entity_key=None,
                observations=list(observations),
                other_entity_keys=[],
                is_scoped=False,
            )

        # 归一化 target key
        target = self._entity_scheme.normalize_entity_key(entity_key)

        # 按实体 key 过滤
        matched: List["Observation"] = []
        other_keys_set = set()
        for obs in observations:
            key = obs.entity_key or self._entity_scheme.extract_entity_key(obs.raw_data)
            if not key:
                continue
            key = self._entity_scheme.normalize_entity_key(key)
            if key == target:
                matched.append(obs)
            else:
                other_keys_set.add(key)

        return ProjectionResult(
            entity_key=target,
            observations=matched,
            other_entity_keys=sorted(other_keys_set),
            is_scoped=True,
        )

    # -----------------------------------------------------------------
    # 便捷方法:一步完成 detect + project
    # -----------------------------------------------------------------
    def detect_and_project(
        self,
        question: str,
        observations: List["Observation"],
    ) -> ProjectionResult:
        """
        一步到位:从问题推断实体 + 按实体投影
        detect 失败时返回全量(is_scoped=False)
        """
        detection = self.detect_active(question, observations)
        result = self.project(observations, detection.entity_key)
        result.detection_source = detection.source
        # 透传"用户提了个不存在的实体"的信号
        if detection.not_found_mentioned:
            result.other_entity_keys.append(
                f"_not_found_:{detection.not_found_mentioned}"
            )
        return result


# ══════════════════════════════════════════════════════════════
# ContextRenderer - 把投影结果渲染成 LLM 可读的上下文文本
# ══════════════════════════════════════════════════════════════
class ContextRenderer:
    """
    把 ProjectionResult 渲染成 LLM 可读的 context 字符串

    输出结构(领域无关):
      ═══════ 【当前作用域】═══════
      用户这次追问针对的是 [entity_label]: [entity_key]。
      你只能基于下方该[entity_label]的硬数据作答,不得引用其他[entity_label]的数据。

      [本 entity 的观察列表]

      【其他[entity_label](已屏蔽,不得引用)】
      用户累积的对话里还涉及过: [other_keys]。
      严禁把它们的数据混入本次回答。
    """

    def __init__(
        self,
        entity_scheme: "EntityScheme",
        observation_schema: Optional["ObservationSchema"] = None,
    ):
        self._entity_scheme = entity_scheme
        self._observation_schema = observation_schema

    def render(self, projection: ProjectionResult) -> str:
        """渲染投影结果成 LLM context 文本"""
        parts = []

        entity_label = self._entity_scheme.entity_label

        # 1) 作用域声明
        if projection.is_scoped and projection.entity_key:
            parts.append(
                f"═══════ 【当前作用域】═══════\n"
                f"用户这次追问针对的是【{projection.entity_key}】这个{entity_label}。\n"
                f"你只能基于下方该{entity_label}的硬数据作答,"
                f"不得引用其他{entity_label}的数据。\n"
            )
        else:
            parts.append(
                f"═══════ 【累积观察(全部{entity_label})】═══════\n"
                f"用户还没明确具体指向,下面是所有累积的硬数据。\n"
            )

        # 2) 本实体的观察列表
        obs_list = projection.observations
        if obs_list:
            if projection.is_scoped:
                label_title = f"{projection.entity_key}·{entity_label}观察"
            else:
                label_title = f"{entity_label}·观察"
            parts.append(f"【{label_title}】(共 {len(obs_list)} 条)")
            if self._observation_schema:
                parts.append(self._observation_schema.format_observation_list(obs_list))
            else:
                # 默认编号列表
                for i, obs in enumerate(obs_list, 1):
                    parts.append(f"  {i}. {obs.to_summary_line()}")
        else:
            parts.append(f"(该{entity_label}下暂无观察数据)")

        # 3) 其他实体提醒(防串扰)
        not_found = [k for k in projection.other_entity_keys if k.startswith("_not_found_:")]
        actual_others = [k for k in projection.other_entity_keys
                        if not k.startswith("_not_found_:")]

        if actual_others and projection.is_scoped:
            parts.append(
                f"\n【其他{entity_label}(已屏蔽,不得引用)】\n"
                f"用户累积的对话里还涉及过这些{entity_label}: "
                f"{'、'.join(actual_others)}。\n"
                f"严禁把它们的数据混入本次回答。"
            )

        # 4) 用户提了不存在的实体时的提醒
        for nf in not_found:
            missing = nf.split(":", 1)[1]
            parts.append(
                f"\n⚠️ 用户提到了【{missing}】,"
                f"但系统累积里没有这个{entity_label}的数据。"
                f"请向用户说明,不要默默回答其他{entity_label}的内容。"
            )

        return "\n".join(parts)
