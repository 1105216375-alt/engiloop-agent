"""
Agent 9 · 桥梁综合评分智能体 (BridgeScoringAgent)

基于 JTG/T H21-2011《公路桥梁技术状况评定标准》的三级评分体系：
  Level 1: 构件得分 (Component Score)
     根据构件内多个病害的 DP 值，使用递归累积公式计算
     u_x = (DP_j / (100 * √x)) * (100 - Σ前x-1个U)
     构件得分 = 100 - ΣU

  Level 2: 部位得分 (Part Score Aggregate)
     对同一部位的多个构件得分做加权：
     部位得分 = 平均值 - (100 - 最小值) / t
     t 值根据构件数量查表（n=1→8.0, n=2→10.0, n=3→9.7...）

  Level 3: 系统得分 (System Weighted)
     对桥面系/上部结构/下部结构的部位得分加权求和
     权重来自 JTG/T H21-2011 规范

  Level 4: 全桥评分 (Overall Dr)
     三个系统的加权总分 → 桥梁技术状况等级（I~V类）

这个 Agent 的角色是：
  - 从 Agent 2（诊断）得到的单条病害 DP 值聚合成全桥综合评分
  - 输出桥梁技术状况等级和各部位得分
  - 为养护决策提供客观量化依据
"""
import math
import os
import json
from typing import Dict, List, Optional, Any, Tuple

from . import scoring_config as config


# ── JTG/T H21-2011 权重定义 ──
WEIGHTS_CONFIG = {
    "deck": {        # 桥面系
        "pavement": 0.40,  # 桥面铺装
        "joints": 0.25,    # 伸缩缝装置
        "railing": 0.10,   # 人行道/护栏
        "drain": 0.10,     # 排水系统
        "signs": 0.05,     # 标志标线
        "sidewalk": 0.10,  # 人行道
    },
    "upper": {       # 上部结构
        "main": 0.70,      # 主梁/主要承重构件
        "gen": 0.18,       # 一般构件
        "bear": 0.12,      # 支座
    },
    "lower": {       # 下部结构
        "pier": 0.30,      # 桥墩
        "abut": 0.30,      # 桥台
        "found": 0.28,     # 基础
        "wing": 0.02,      # 翼墙、耳墙
        "cone": 0.01,      # 锥坡、护坡
        "river": 0.07,     # 河床
        "reg": 0.02,       # 调治构造物
    },
}

# 桥面系/上部/下部 三大系统权重（JTG/T H21-2011）
SYSTEM_WEIGHTS = {
    "deck": 0.20,    # 桥面系
    "upper": 0.40,   # 上部结构
    "lower": 0.40,   # 下部结构
}

# 构件名称 → 部位映射（用于自动归类）
COMPONENT_TO_PART = {
    # 桥面系
    "桥面铺装": ("deck", "pavement"),
    "铺装": ("deck", "pavement"),
    "伸缩缝": ("deck", "joints"),
    "护栏": ("deck", "railing"),
    "防撞墙": ("deck", "railing"),
    "人行道": ("deck", "sidewalk"),
    "排水": ("deck", "drain"),
    "排水设施": ("deck", "drain"),
    "雨篦": ("deck", "drain"),
    "雨水箅子": ("deck", "drain"),
    "篦子": ("deck", "drain"),
    "格栅": ("deck", "drain"),
    "泄水": ("deck", "drain"),
    "标志": ("deck", "signs"),

    # 上部结构
    "主梁": ("upper", "main"),
    "T梁": ("upper", "main"),
    "箱梁": ("upper", "main"),
    "空心板": ("upper", "main"),
    "横隔板": ("upper", "gen"),
    "湿接缝": ("upper", "gen"),
    "铰缝": ("upper", "gen"),
    "翼板": ("upper", "gen"),
    "支座": ("upper", "bear"),
    "挡块": ("upper", "bear"),
    "支座垫石": ("upper", "bear"),

    # 下部结构 — 盖梁挪到下部(JTG 标准:盖梁是桥墩的盖帽,属于下部结构) Round 31
    "盖梁": ("lower", "pier"),
    "牛腿": ("lower", "pier"),
    "横系梁": ("lower", "pier"),
    "桥墩": ("lower", "pier"),
    "墩柱": ("lower", "pier"),
    "薄壁墩": ("lower", "pier"),
    "空心墩": ("lower", "pier"),
    "双柱墩": ("lower", "pier"),
    "桥台": ("lower", "abut"),
    "台帽": ("lower", "abut"),
    "U型桥台": ("lower", "abut"),
    "基础": ("lower", "found"),
    "桩基": ("lower", "found"),
    "扩大基础": ("lower", "found"),
    "翼墙": ("lower", "wing"),
    "耳墙": ("lower", "wing"),
    "锥坡": ("lower", "cone"),
    "护坡": ("lower", "cone"),
    "河床": ("lower", "river"),
    "调治构造物": ("lower", "reg"),
    # ⚠️ 严禁加 "墩" / "梁" / "台" 等单字条目 — 会误匹中其他构件
    # 例: "梁" 会误匹中"盖梁","台" 会误匹中"台帽"以外的所有"台"字构件
}


def classify_component(component_name: str) -> Optional[tuple]:
    """
    将构件名归类到 (系统, 部位) 元组。
    Round 31: 改为"最长 key 匹配优先",避免短 key 误匹中长 key
    例:'1号盖梁' 优先匹中"盖梁"而不是某个含 "梁" 字的短 key
    """
    if not component_name:
        return None
    # 按 key 长度降序排,长 key 优先匹中
    sorted_keys = sorted(COMPONENT_TO_PART.keys(), key=lambda k: -len(k))
    for key in sorted_keys:
        if key in component_name:
            return COMPONENT_TO_PART[key]
    return None


def score_to_grade(score: float) -> str:
    """
    桥梁技术状况等级（JTG/T H21-2011）
    Dr ≥ 95: I类（完好）
    80 ≤ Dr < 95: II类（较好）
    60 ≤ Dr < 80: III类（较差）
    40 ≤ Dr < 60: IV类（差）
    Dr < 40: V类（危险）
    """
    if score >= 95:
        return "I类（完好）"
    elif score >= 80:
        return "II类（较好）"
    elif score >= 60:
        return "III类（较差）"
    elif score >= 40:
        return "IV类（差）"
    else:
        return "V类（危险）"


def _numeric_grade(score: float) -> int:
    """Dr → 纯数字等级 1-5"""
    if score >= 95: return 1
    if score >= 80: return 2
    if score >= 60: return 3
    if score >= 40: return 4
    return 5


def score_to_grade_with_correction(
    dr: float,
    spci: Optional[float] = None,
    sbci: Optional[float] = None,
    bdci: Optional[float] = None,
    has_class_5_critical: bool = False,
) -> Tuple[str, str]:
    """
    桥梁技术状况等级(JTG/T H21-2011)— 含 4.1.7 等级修正条款

    返回 (等级字符串, 修正说明)
    修正说明为空字符串表示未触发修正。

    4.1.7 修正规则:
      0. 关键部件(主梁/桥墩等)出现 5 类病害 → 不得高于 4 类(差) [R58 新增]
      1. 关键部件(上部结构 SPCI / 下部结构 SBCI)任一 < 40 → 强制 5 类(危险)
      2. 40 ≤ Dr < 60 且 SPCI 等级=3 且 SBCI 等级=3 且 BDCI 等级=4 → 降级为 3 类(较差)
      3. 其他情况按 Dr 直接映射
    """
    note = ""
    # Rule 1: 关键部件 < 40 → 强制 5 类(优先级最高)
    if spci is not None and sbci is not None:
        if min(spci, sbci) < 40:
            return "V类（危险）", "关键部件<40 强制 5 类"

    # R58 新增 Rule 0: 关键构件出现 5 类 → 不得高于 4 类(差)
    # JTG/T H21-2011 第 4.1.7 条:即使全桥 Dr 高,1 根危险主梁/桥墩也要降级
    if has_class_5_critical:
        # Dr 仍按计算值,但等级最高 IV 类
        natural_grade = score_to_grade(dr)
        # I/II 类 → 强制 IV 类(差)
        if natural_grade in ("I类（完好）", "II类（较好）"):
            return "IV类（差）", "存在 5 类关键构件 强制不高于 4 类"
        # III/IV/V 类按原级别
        return natural_grade, ""

    # Rule 2: 4.1.7 修正(IV 类向 III 类修正)
    if 40 <= dr < 60 and spci is not None and sbci is not None and bdci is not None:
        if _numeric_grade(spci) == 3 and _numeric_grade(sbci) == 3 and _numeric_grade(bdci) == 4:
            return "III类（较差）", "4.1.7 等级修正"

    # Rule 3: 普通映射
    return score_to_grade(dr), note


class BridgeScoringAgent:
    """
    Agent 9 · 桥梁综合评分智能体

    输入：诊断结果列表（每条有构件、病害、DP值）
    输出：构件得分 / 部位得分 / 系统得分 / 全桥 Dr 值 / 技术状况等级
    """

    def __init__(self):
        self.name = "BridgeScoringAgent"
        self._disease_db = self._load_disease_db()

    def _load_disease_db(self) -> Dict:
        """加载病害评分数据库（366条 DP 值参考）"""
        db_path = os.path.join(config.BASE_DIR, "data", "scoring_disease_db.json")
        if os.path.exists(db_path):
            with open(db_path, encoding="utf-8") as f:
                return json.load(f)
        return {}

    # ══════ JTG/T H21-2011 评分公式 ══════

    # Round 40: JTG/T H21-2011 表 4.1.2 完整 t 值表
    # n=1 特殊(∞,实际 n=1 时不走聚合公式,直接返回构件分)
    # n=2..30 逐点列出,n=40/50/60/70/80/90/100 稀疏点,n≥200=2.3
    # 表中未列出的 t 值采用线性内插法计算(规范 4.1.2 注 2)
    _T_VALUE_TABLE = {
        2: 10.0,  3: 9.7,   4: 9.5,   5: 9.2,
        6: 8.9,   7: 8.7,   8: 8.5,   9: 8.3,   10: 8.1,
        11: 7.9,  12: 7.7,  13: 7.5,  14: 7.3,  15: 7.2,
        16: 7.08, 17: 6.96, 18: 6.84, 19: 6.72, 20: 6.6,
        21: 6.48, 22: 6.36, 23: 6.24, 24: 6.12, 25: 6.00,
        26: 5.88, 27: 5.76, 28: 5.64, 29: 5.52, 30: 5.4,
        40: 4.9,  50: 4.4,  60: 4.0,  70: 3.6,  80: 3.2,
        90: 2.8,  100: 2.5, 200: 2.3,
    }

    @classmethod
    def _get_t_value(cls, n: int) -> float:
        """
        按表 4.1.2 查值；稀疏区间采用线性插值。
        """
        if n <= 1:
            return 8.0
        if n > 200:
            return 2.3
        if n in cls._T_VALUE_TABLE:
            return cls._T_VALUE_TABLE[n]
        points = sorted(cls._T_VALUE_TABLE)
        lower = max(point for point in points if point < n)
        upper = min(point for point in points if point > n)
        lower_value = cls._T_VALUE_TABLE[lower]
        upper_value = cls._T_VALUE_TABLE[upper]
        ratio = (n - lower) / (upper - lower)
        return round(lower_value + (upper_value - lower_value) * ratio, 6)

    @staticmethod
    def calculate_component_score(dp_list: List[float]) -> float:
        """
        构件得分计算（Level 1）
        公式：u_x = (DP_j / (100*√x)) * (100 - Σ前x-1个U)
             构件得分 = 100 - ΣU
        """
        if not dp_list:
            return 100.0
        dp_sorted = sorted(dp_list, reverse=True)
        k = len(dp_sorted)
        U = []
        for x in range(1, k + 1):
            dp_j = dp_sorted[x - 1]
            if dp_j >= 100:
                return 0.0
            if x == 1:
                u_x = dp_j
            else:
                sum_prev = sum(U[:x - 1])
                u_x = (dp_j / (100.0 * math.sqrt(x))) * (100.0 - sum_prev)
            U.append(u_x)
        score = 100.0 - sum(U)
        return max(0.0, min(100.0, score))

    @classmethod
    def calculate_part_score(cls, component_scores: List[float]) -> Optional[float]:
        """
        部位得分聚合（Level 2）
        公式：部位得分 = 平均值 - (100 - 最小值) / t
        """
        valid = [s for s in component_scores if s is not None]
        if not valid:
            return None
        n = len(valid)
        if n == 1:
            return valid[0]
        t = cls._get_t_value(n)
        mean_s = sum(valid) / n
        min_s = min(valid)
        res = mean_s - (100 - min_s) / t
        return max(0.0, min(100.0, res))

    @staticmethod
    def calculate_system_weighted(
        parts_dict: Dict[str, float], weights: Dict[str, float]
    ) -> Optional[float]:
        """
        Round 52: 严格按评分专家的实现 — 用 total_weight 归一化
        (而不是把未出现的部件按 100 补全)
        """
        total_score = 0.0
        total_weight = 0.0
        for key, w in weights.items():
            score = parts_dict.get(key)
            if score is not None:
                total_score += score * w
                total_weight += w
        if total_weight == 0:
            return 100.0
        return total_score / total_weight

    # ══════ 主入口：从诊断结果计算全桥 Dr ══════

    @staticmethod
    def _normalize_part_applicability(raw: Optional[Dict]) -> Tuple[Dict[str, Dict[str, bool]], Dict[str, Dict[str, str]]]:
        normalized = {"deck": {}, "upper": {}, "lower": {}}
        sources = {"deck": {}, "upper": {}, "lower": {}}
        for system, parts in (raw or {}).items():
            if system not in normalized or not isinstance(parts, dict):
                continue
            for part, value in parts.items():
                if isinstance(value, dict):
                    if "applicable" not in value:
                        continue
                    normalized[system][str(part)] = bool(value["applicable"])
                    sources[system][str(part)] = str(value.get("source") or "explicit")
                elif isinstance(value, bool):
                    normalized[system][str(part)] = value
                    sources[system][str(part)] = "explicit"
        return normalized, sources

    @classmethod
    def _resolve_part_applicability(
        cls,
        grouped: Dict[str, Dict[str, Dict[str, List[float]]]],
        structure: Dict,
        explicit: Optional[Dict],
    ) -> Tuple[Dict[str, Dict[str, bool]], Dict[str, Dict[str, str]], List[str]]:
        applicability, sources = cls._normalize_part_applicability(explicit)
        conflicts: List[str] = []

        def present(value: Any) -> bool:
            if value in (None, "", 0, False):
                return False
            return str(value).strip().lower() not in {"无", "--", "-", "/", "不适用", "none"}

        structure_hints = {
            ("upper", "main"): ("总主梁数", "主梁总数", "梁型", "主梁类型"),
            ("upper", "gen"): ("总湿接缝数", "总横隔板数"),
            ("upper", "bear"): ("总支座数", "支座总数", "支座形式"),
            ("lower", "pier"): ("桥墩数",),
            ("lower", "abut"): ("桥台数",),
            ("lower", "found"): ("基础形式", "基础"),
            ("lower", "wing"): ("翼墙", "耳墙"),
            ("lower", "cone"): ("锥坡", "护坡"),
            ("lower", "river"): ("河床",),
            ("lower", "reg"): ("调治构造物",),
            ("deck", "pavement"): ("桥面铺装",),
            ("deck", "joints"): ("伸缩缝数", "伸缩缝形式"),
            ("deck", "railing"): ("护栏",),
            ("deck", "drain"): ("排水系统", "排水设施数"),
            ("deck", "sidewalk"): ("人行道",),
            ("deck", "signs"): ("照明", "标志"),
        }
        for (system, part), keys in structure_hints.items():
            if part in applicability[system]:
                continue
            if any(present(structure.get(key)) for key in keys):
                applicability[system][part] = True
                sources[system][part] = "structure"

        for system, parts in grouped.items():
            for part in parts:
                if applicability[system].get(part) is False:
                    conflicts.append(f"{system}/{part}:报告标记不适用但抽取到病害")
                applicability[system][part] = True
                sources[system][part] = sources[system].get(part, "disease_evidence")

        return applicability, sources, conflicts

    def evaluate_bridge(
        self,
        results: List[Dict],
        structure: Optional[Dict] = None,
        part_applicability: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """
        从诊断结果列表计算全桥综合评分。

        参数:
            results: 病害诊断结果列表
            structure: 桥梁结构信息（来自 BridgeStructureAgent），可选
                      如果提供，会按真实构件实例数评分（更符合 JTG/T H21-2011）
                      如果不提供，按构件类别粗略评分

        返回:
            {
                "全桥评分": 75.3,
                "技术状况等级": "III类（较差）",
                "系统得分": {"deck": 82.1, "upper": 68.5, "lower": 79.2},
                "部位得分": {...},
                "构件得分": {...},
                "未分类病害": [...],
                "结构信息": {...},  # 如果提供了 structure
                "计算依据": "JTG/T H21-2011",
            }
        """
        if not results:
            return {
                "全桥评分": 100.0,
                "技术状况等级": "I类（完好）",
                "系统得分": {},
                "部位得分": {},
                "构件得分": {},
                "未分类病害": [],
                "计算依据": "JTG/T H21-2011",
                "summary": "无病害记录，默认评定为I类（完好）",
            }

        # Step 1: 按 (系统, 部位, 构件实例) 归类病害的 DP 值
        # grouped[system][part][instance_id] = [dp1, dp2, ...]
        # instance_id 区分不同的构件实例
        # Round 40: 下部结构 pier 特殊处理 — 把盖梁单独归到 inst_id 前缀 "cap::"
        #           柱子归到 "col::",后续聚合时分开算然后按 0.3/0.7 合成
        grouped: Dict[str, Dict[str, Dict[str, List[float]]]] = {}
        unclassified = []
        classification_stats = {
            "canonical": 0,
            "display_fallback": 0,
            "unclassified": 0,
        }

        for r in results:
            comp_name = str(r.get("构件", "") or "")
            component_category = str(r.get("构件类别", "") or "")
            dp = float(r.get("DP值", 0))

            # 规范化类别决定评分部位；实例显示名只用于提取编号。
            # 报告解析中显示名可能被位置文字干扰，例如主梁记录显示成“117号墩”。
            classification = classify_component(component_category)
            if classification is not None:
                classification_stats["canonical"] += 1
            else:
                classification = classify_component(comp_name)
                if classification is not None:
                    classification_stats["display_fallback"] += 1
            if classification is None:
                unclassified.append(f"{comp_name}·{r.get('病害', '')}")
                classification_stats["unclassified"] += 1
                continue

            system, part = classification
            instance_ids = self._extract_instance_ids(comp_name)

            # Round 40: 下部结构 pier 特殊处理,分离盖梁(cap)和柱子(col)
            if system == "lower" and part == "pier":
                component_context = f"{component_category} {comp_name}"
                prefix = "cap::" if "盖梁" in component_context else "col::"
                instance_ids = [f"{prefix}{instance_id}" for instance_id in instance_ids]

            for instance_id in instance_ids:
                grouped.setdefault(system, {}).setdefault(part, {}).setdefault(instance_id, []).append(dp)

        provided_structure = dict(structure or {})
        effective_structure = dict(provided_structure)
        structure_adjustments: Dict[str, Dict[str, int]] = {}
        observed_to_structure = (
            ("upper", "main", "总主梁数"),
            ("lower", "pier", "桥墩数"),
        )
        for system, part, field in observed_to_structure:
            instances = grouped.get(system, {}).get(part, {})
            inferred = self._infer_expected_from_instance_ids(instances)
            current = int(effective_structure.get(field) or 0)
            if inferred and inferred > current:
                effective_structure[field] = inferred
                structure_adjustments[field] = {"before": current, "after": inferred}

        applicability, applicability_sources, applicability_conflicts = self._resolve_part_applicability(
            grouped, effective_structure, part_applicability
        )

        missing_structure_fields: List[str] = []
        structure_warnings: List[str] = []

        def _has_positive_structure_value(*keys: str) -> bool:
            for key in keys:
                try:
                    if float(effective_structure.get(key) or 0) > 0:
                        return True
                except (TypeError, ValueError):
                    continue
            return False

        if grouped.get("upper", {}).get("main") and not _has_positive_structure_value("总主梁数", "主梁总数"):
            missing_structure_fields.append("总主梁数")
            structure_warnings.append("缺少主梁总数，无法可靠补全未记录病害的完好主梁")
        if grouped.get("upper", {}).get("bear") and not _has_positive_structure_value("总支座数", "支座总数"):
            missing_structure_fields.append("总支座数")
            structure_warnings.append("缺少支座总数，无法可靠补全未记录病害的完好支座")

        grading_review_items = [
            {
                "component": str(row.get("构件") or ""),
                "disease": str(row.get("病害") or ""),
                "missing": list(row.get("评分缺失条件") or []),
                "basis": str(row.get("评分依据") or ""),
            }
            for row in results
            if row.get("评分待复核")
        ]
        if grading_review_items:
            structure_warnings.append(
                f"{len(grading_review_items)}条病害缺少完整定量标度，重算采用暂定扣分"
            )
        structure_warnings.extend(applicability_conflicts)
        provisional = bool(missing_structure_fields or grading_review_items or applicability_conflicts)
        score_reliability = {
            "level": "review" if provisional else "high",
            "publishable": not provisional,
            "provisional": provisional,
            "missing_structure_fields": missing_structure_fields,
            "uncertain_grading_count": len(grading_review_items),
            "grading_review_items": grading_review_items[:20],
            "applicability_conflicts": applicability_conflicts,
            "warnings": structure_warnings,
            "policy": "关键构件数量或病害定量条件缺失时保留暂定校核值，不得作为确定性发布结论",
        }

        # Step 2: 计算每个构件实例的得分（Level 1）
        # 同时根据 structure 补充未发现病害的"完好"实例（得分=100）
        component_scores: Dict[str, float] = {}
        for system, parts in grouped.items():
            for part, instances in parts.items():
                for inst_id, dp_list in instances.items():
                    score = self.calculate_component_score(dp_list)
                    component_scores[f"{system}/{part}/{inst_id}"] = round(score, 2)

        # Step 3: 计算每个部位的得分（Level 2）
        # Round 40: 下部结构 pier 特殊处理 — 盖梁和柱子分开聚合,
        #           按 pier综合 = 盖梁*0.3 + 柱子*0.7 合成(参考评分专家 main_window L1105)
        part_scores: Dict[str, Dict[str, float]] = {"deck": {}, "upper": {}, "lower": {}}
        structure_completed_parts: List[str] = []
        applicability_completed_parts: List[str] = []

        for system in ("deck", "upper", "lower"):
            parts = grouped.get(system, {})
            candidate_parts = set(parts)
            candidate_parts.update(
                part for part, is_applicable in applicability.get(system, {}).items()
                if is_applicable
            )
            if effective_structure:
                for part in WEIGHTS_CONFIG[system]:
                    expected = self._expected_instance_count(system, part, effective_structure)
                    if (
                        expected
                        and expected > 0
                        and applicability.get(system, {}).get(part) is not False
                    ):
                        candidate_parts.add(part)

            for part in candidate_parts:
                instances = parts.get(part, {})
                if applicability.get(system, {}).get(part) is False and not instances:
                    continue
                expected_total = (
                    self._expected_instance_count(system, part, effective_structure)
                    if effective_structure else None
                )
                if (
                    not instances
                    and not expected_total
                    and applicability.get(system, {}).get(part) is True
                ):
                    part_scores[system][part] = 100.0
                    applicability_completed_parts.append(f"{system}/{part}")
                    continue
                # Round 40: 下部结构 pier 特殊处理
                if system == "lower" and part == "pier":
                    cap_insts = {k: v for k, v in instances.items() if k.startswith("cap::")}
                    col_insts = {k: v for k, v in instances.items() if k.startswith("col::")}

                    # 柱子聚合
                    col_comp_scores = [
                        self.calculate_component_score(dp_list)
                        for dp_list in col_insts.values()
                    ]
                    if expected_total:
                        if expected_total > len(col_comp_scores):
                            missing = expected_total - len(col_comp_scores)
                            col_comp_scores.extend([100.0] * missing)
                    col_part = self.calculate_part_score(col_comp_scores) if col_comp_scores else None

                    # 盖梁聚合(如果有)
                    cap_comp_scores = [
                        self.calculate_component_score(dp_list)
                        for dp_list in cap_insts.values()
                    ]
                    if expected_total:
                        if expected_total > len(cap_comp_scores):
                            missing = expected_total - len(cap_comp_scores)
                            cap_comp_scores.extend([100.0] * missing)
                    cap_part = self.calculate_part_score(cap_comp_scores) if cap_comp_scores else None

                    if not instances and expected_total:
                        structure_completed_parts.append(f"{system}/{part}")

                    # 合成:pier = 盖梁*0.3 + 柱子*0.7(盖梁无病害时 pier = 柱子)
                    if cap_part is not None and col_part is not None:
                        pier_composite = cap_part * 0.3 + col_part * 0.7
                    elif col_part is not None:
                        pier_composite = col_part
                    elif cap_part is not None:
                        pier_composite = cap_part
                    else:
                        continue

                    part_scores[system][part] = round(pier_composite, 2)
                    continue

                # 其他部位:按原逻辑
                comp_scores = [
                    self.calculate_component_score(dp_list)
                    for dp_list in instances.values()
                ]

                # 根据 structure 补全:未发现病害的构件按 100 分计入
                if expected_total:
                    if expected_total > len(comp_scores):
                        missing = expected_total - len(comp_scores)
                        comp_scores.extend([100.0] * missing)
                    if not instances:
                        structure_completed_parts.append(f"{system}/{part}")

                ps = self.calculate_part_score(comp_scores)
                if ps is not None:
                    part_scores[system][part] = round(ps, 2)

        score_reliability["applicability_completed_parts"] = sorted(applicability_completed_parts)
        score_reliability["part_applicability_source"] = applicability_sources

        # Step 4: 计算每个系统的加权得分(Level 3)
        # 仅对已证明适用的部位补完好分；明确不适用的部位参与权重重分配。
        system_scores = {}
        for system in ["deck", "upper", "lower"]:
            parts_with_score = part_scores.get(system, {})
            score = self.calculate_system_weighted(
                parts_with_score, WEIGHTS_CONFIG[system]
            )
            system_scores[system] = round(score, 2) if score is not None else 100.0

        # Step 5: 计算全桥评分(Level 4)
        overall_score = self.calculate_system_weighted(system_scores, SYSTEM_WEIGHTS)
        if overall_score is None:
            overall_score = 100.0

        overall_score = round(overall_score, 2)

        # Round 40: 使用 4.1.7 等级修正
        spci = system_scores.get("upper")
        sbci = system_scores.get("lower")
        bdci = system_scores.get("deck")
        # R58: 检测是否有 5 类关键构件(主梁/桥墩/盖梁/T梁/箱梁等承载构件)
        critical_keywords = ["主梁", "T梁", "箱梁", "空心板", "钢梁",
                              "桥墩", "墩柱", "盖梁", "立柱", "桥台"]
        has_class_5_critical = any(
            int(r.get("标度", 0) or 0) >= 5
            and any(k in str(r.get("构件", "")) for k in critical_keywords)
            for r in results
        )
        grade, correction_note = score_to_grade_with_correction(
            overall_score, spci=spci, sbci=sbci, bdci=bdci,
            has_class_5_critical=has_class_5_critical,
        )

        system_names = {"deck": "桥面系", "upper": "上部结构", "lower": "下部结构"}
        summary_parts = [
            f"全桥技术状况评分 Dr={overall_score}，评定为{grade}"
        ]
        if correction_note:
            summary_parts[0] += f"(触发 {correction_note})"
        for sys_key in ["deck", "upper", "lower"]:
            if sys_key in system_scores:
                summary_parts.append(f"{system_names[sys_key]}={system_scores[sys_key]}分")
        if score_reliability["provisional"]:
            pending = []
            if score_reliability["missing_structure_fields"]:
                pending.append("结构数量:" + "、".join(score_reliability["missing_structure_fields"]))
            if score_reliability["uncertain_grading_count"]:
                pending.append(f"定量标度:{score_reliability['uncertain_grading_count']}条")
            if score_reliability["applicability_conflicts"]:
                pending.append(f"适用性冲突:{len(score_reliability['applicability_conflicts'])}项")
            summary_parts.append("系统重算为暂定校核值，待复核" + "；".join(pending))

        result = {
            "全桥评分": overall_score,
            "技术状况等级": grade,
            "等级修正": correction_note,  # 空字符串表示未触发
            "系统得分": {
                system_names.get(k, k): v for k, v in system_scores.items()
            },
            "部位得分": {
                system_names.get(sys_k, sys_k): {
                    part_k: part_v for part_k, part_v in parts.items()
                }
                for sys_k, parts in part_scores.items()
                if parts
            },
            "构件得分": component_scores,
            "未分类病害": unclassified,
            "评分归类诊断": {
                **classification_stats,
                "structure_completed_parts": sorted(structure_completed_parts),
                "applicability_completed_parts": sorted(applicability_completed_parts),
                "part_applicability": applicability,
                "part_applicability_source": applicability_sources,
                "applicability_conflicts": applicability_conflicts,
                "structure_adjustments": structure_adjustments,
            },
            "评分可靠性": score_reliability,
            "计算依据": "JTG/T H21-2011《公路桥梁技术状况评定标准》",
            "summary": "；".join(summary_parts),
        }
        if effective_structure:
            result["结构信息"] = {
                k: v for k, v in effective_structure.items() if not k.startswith("_")
            }
        return result

    def _extract_instance_id(self, comp_name: str) -> str:
        """从构件名提取实例编号，如 '1-2号T梁' → '1-2'，'主梁' → 'default'"""
        import re
        m = re.search(r"(\d+(?:-\d+)*)\s*[号#]?", comp_name)
        if m:
            return m.group(1)
        return "default"

    def _extract_instance_ids(self, comp_name: str) -> List[str]:
        """Expand explicit numbered ranges without treating disease counts as components."""
        import re

        text = str(comp_name or "")
        # ASCII '-' is reserved for hierarchical ids such as 1-2号支座.
        range_match = re.search(r"(\d+)\s*[~～〜–—]\s*(\d+)\s*[\u53f7#]", text)
        if range_match:
            start, end = int(range_match.group(1)), int(range_match.group(2))
            if 0 <= start <= end and end - start <= 200:
                return [str(value) for value in range(start, end + 1)]
        return [self._extract_instance_id(text)]

    @staticmethod
    def _infer_expected_from_instance_ids(instances: Dict[str, List[float]]) -> Optional[int]:
        """结构卡缺失时，从多个明确编号构件得到保守数量下界。"""
        import re

        numeric_ids = set()
        for raw_id in instances:
            instance_id = raw_id.replace("cap::", "").replace("col::", "")
            if re.fullmatch(r"\d+", instance_id):
                numeric_ids.add(int(instance_id))
        if len(numeric_ids) < 2:
            return None
        inferred = max(len(numeric_ids), max(numeric_ids))
        return inferred if 0 < inferred <= 2000 else None

    def _expected_instance_count(self, system: str, part: str, structure: Dict) -> Optional[int]:
        """根据结构信息推算某部位应有的构件实例总数。
        Round 40: deck 系统的 pavement/railing/drain/sidewalk 也用"跨数"补全
                  这样部位聚合会把"完好实例"也算进去,跟评分专家 UI 录入行为一致。
        """
        if system == "upper":
            if part == "main":
                return structure.get("总主梁数")
            elif part == "gen":
                # 一般构件：湿接缝+横隔板
                jc = structure.get("总湿接缝数", 0)
                hgb = structure.get("总横隔板数", 0)
                return (jc + hgb) if (jc or hgb) else None
            elif part == "bear":
                return structure.get("总支座数")
        elif system == "lower":
            if part == "pier":
                return structure.get("桥墩数")
            elif part == "abut":
                return structure.get("桥台数")
            elif part == "cone":
                return structure.get("总锥坡护坡数")
        elif system == "deck":
            if part == "joints":
                return structure.get("伸缩缝数")
            # Round 40: pavement/railing/drain/sidewalk 按跨数补全
            # (每跨 1 段铺装/护栏/排水/人行道)
            if part in ("pavement", "railing", "drain", "sidewalk"):
                spans = structure.get("跨数")
                return spans if spans else None
        return None

    def format_report(self, eval_result: Dict) -> str:
        """将评分结果格式化为文本报告"""
        lines = []
        lines.append("=" * 50)
        lines.append("桥梁技术状况评定（JTG/T H21-2011）")
        lines.append("=" * 50)
        lines.append(f"全桥评分 Dr = {eval_result['全桥评分']} 分")
        lines.append(f"技术状况等级：{eval_result['技术状况等级']}")
        lines.append("")

        sys_scores = eval_result.get("系统得分", {})
        if sys_scores:
            lines.append("【三大系统得分】")
            for name, score in sys_scores.items():
                lines.append(f"  {name}：{score} 分")
            lines.append("")

        part_scores = eval_result.get("部位得分", {})
        if part_scores:
            lines.append("【部位得分明细】")
            for sys_name, parts in part_scores.items():
                if parts:
                    lines.append(f"  [{sys_name}]")
                    for part_k, part_v in parts.items():
                        lines.append(f"    {part_k}: {part_v} 分")
            lines.append("")

        unclassified = eval_result.get("未分类病害", [])
        if unclassified:
            lines.append(f"【未分类病害】（共{len(unclassified)}条）")
            for u in unclassified[:5]:
                lines.append(f"  · {u}")
            if len(unclassified) > 5:
                lines.append(f"  ... 还有{len(unclassified)-5}条")

        return "\n".join(lines)
