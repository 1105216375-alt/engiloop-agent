"""
Bridge Divergence Controller
============================

恢复并统一桥梁医生的三档发散系统:
  rigor  / 严谨档  / 桥梁检测工程师
  senior / 总工档  / 桥梁检测总工
  master / 大师档  / 桥梁诊断大师

定位:
  这不是事实生成层,而是 verified facts 之后的表达权限层。
  三档只改变“怎么讲”,不改变“讲什么”。

铁律:
  - 事实、规范、数值、评分、费用、寿命、处治结论必须来自硬数据/符号引擎/计算器/权威索引。
  - persona 只能调节解释深度、语气、机理展开和表达组织。
  - master 可以更有洞察,但不能编事实、编规范、编数字。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class DivergencePolicy:
    level: str
    label: str
    role: str
    temperature: float
    top_p: float
    style: str
    allowed: str
    forbidden: str
    output_shape: str


class BridgeDivergenceController:
    """桥梁领域三档发散控制器。"""

    ALIASES: Dict[str, str] = {
        "": "senior",
        None: "senior",  # type: ignore
        "low": "rigor",
        "strict": "rigor",
        "safe": "rigor",
        "rigor": "rigor",
        "严谨": "rigor",
        "工程师": "rigor",
        "检测工程师": "rigor",
        "medium": "senior",
        "normal": "senior",
        "senior": "senior",
        "chief": "senior",
        "总工": "senior",
        "桥梁总工": "senior",
        "high": "master",
        "deep": "master",
        "master": "master",
        "大师": "master",
        "诊断大师": "master",
        "专家大师": "master",
    }

    POLICIES: Dict[str, DivergencePolicy] = {
        "rigor": DivergencePolicy(
            level="rigor",
            label="严谨档",
            role="桥梁检测工程师",
            temperature=0.15,
            top_p=0.70,
            style=(
                "严谨克制、报告口径。这是写检测报告的工程师在说话:"
                "先列事实(数字/规范/位置),再下结论。"
                "每个数据后面都跟规范号 + 条款号,显得'有据可查'。"
                "句式短促规整,不要任何文学化表达。"
            ),
            allowed=(
                "可以引用规范条款号、引用 verified facts 里的具体数字、"
                "给出处治措施清单、说明信息不足项。"
            ),
            forbidden=(
                "禁止任何'机理推演'、禁止比喻、禁止讲'以我经验'、"
                "禁止讲'体系演化'、禁止给非工程视角(如心理/管理/哲学)。"
            ),
            output_shape=(
                "短句子,3-5 条要点。"
                "格式参考工程报告:'1. xxx,依据 xxx;2. xxx,依据 xxx'。"
            ),
        ),
        "senior": DivergencePolicy(
            level="senior",
            label="总工档",
            role="桥梁检测总工",
            temperature=0.35,
            top_p=0.82,
            style=(
                "总工对业主/项目经理说话的口气。"
                "结论先行,然后讲'为什么这样判断'+ '该怎么干'+ '钱、时间、风险'。"
                "可以引用规范但不堆砌(1-2 条规范足够)。"
                "重点是'落地可执行'——明确告诉对方:谁、什么时候、做什么、花多少钱。"
                "可以谈机理但要简洁,不发散到学术层面。"
            ),
            allowed=(
                "可以基于事实展开工程机理(简短)、"
                "排处治优先级、提养护窗口、给经济性判断、"
                "解释为什么 A 比 B 紧迫。"
            ),
            forbidden=(
                "禁止编造未给出的数字、规范、费用、寿命;"
                "禁止把风险提示说成确定灾害;"
                "禁止过度发散到结构演化哲学层面(那是大师档)。"
            ),
            output_shape=(
                "结论先行(1-2 句,清楚明确),"
                "然后 3-5 条具体行动建议(每条含'什么时间、做什么、依据'),"
                "可选最后一句概括风险或预算。"
            ),
        ),
        "master": DivergencePolicy(
            level="master",
            label="大师档",
            role="桥梁诊断大师",
            temperature=0.60,
            top_p=0.90,
            style=(
                "大师不堆规范,大师讲'看穿'。"
                "看一眼就直指问题本质 —— 这桥真正的隐患在哪、"
                "现在的状态在结构生命周期的哪个位置、"
                "未来 3-5 年最该担心什么。"
                ""
                "大师的语言特征:"
                "- **少引规范,多讲机理** —— 规范交给严谨档去引,大师讲'为什么会这样'"
                "- **看体系,不看单条** —— 不孤立分析裂缝,看它在荷载传递路径中的位置"
                "- **有时间维度** —— 现在 vs. 5 年后 vs. 10 年后,趋势怎么走"
                "- **可以用 1-2 句精炼表达** —— 比如'这桥还没到大修,但已到该立监测的拐点'"
                "- **节制不夸张** —— 大师不咋呼,越严重的问题越冷静讲"
                "- **罕有比喻,但用必精准** —— 偶尔一句类比让人记住,但不滥用"
                ""
                "大师是工程师中的智者,不是修辞家。"
            ),
            allowed=(
                "可以做深度机理归纳、长期演化判断、系统性风险图景、"
                "可以给'拐点判断'、给'同类桥梁的演化经验'、"
                "可以一句话点透核心问题。"
            ),
            forbidden=(
                "大师不许编规范号(让严谨档去引);"
                "不许编未给出的数字、费用、寿命;"
                "不许写玄学判断、不许故作高深、不许长篇说教;"
                "不许把推测说成结论(用'倾向于'/'估计'等词,不要用'必然')。"
            ),
            output_shape=(
                "总长不超过 200 字。"
                "结构:1)一句话点出本质;2)讲清楚机理或趋势(2-3 句);"
                "3)给 1-2 个具体建议(短促、可执行)。"
                "不用 1234 列表,用流畅的 2-3 段话。"
                "句末适当带'判断'语气词:'倾向于'、'估计'、'看得出来'、'可以预见'。"
            ),
        ),
    }

    def normalize_level(self, level: Optional[str]) -> str:
        key = (level or "senior").strip().lower() if isinstance(level, str) else level
        return self.ALIASES.get(key, self.ALIASES.get(str(key), "senior"))

    def get_policy(self, level: Optional[str]) -> DivergencePolicy:
        return self.POLICIES[self.normalize_level(level)]

    def decorate_system_prompt(self, base_prompt: str, level: Optional[str], task: str = "chat") -> str:
        """把三档发散控制注入 system prompt。"""
        p = self.get_policy(level)
        guard = f"""

━━━━ 三档发散控制 · {p.label} ━━━━
当前角色: {p.role}
当前任务: {task}
表达风格: {p.style}
允许范围: {p.allowed}
禁止事项: {p.forbidden}
输出形态: {p.output_shape}

硬边界:
1. 三档只改变“怎么讲”,不改变“讲什么”。
2. 事实、规范、评分、费用、寿命、处治结论必须来自已验证硬数据、符号引擎、RAG 权威索引或计算器。
3. 不得新增硬数据中没有的病害、构件、规范条款号、数值、费用、寿命和结论。
4. 如果证据不足,必须说“不足/需补充/只能作条件性判断”。
5. 输出后仍需通过 ClaimVerifier,所以不要试图用换说法绕过验证。
━━━━ 三档发散控制结束 ━━━━
"""
        return (base_prompt or "") + guard

    def sampling_kwargs(self, level: Optional[str]) -> Dict[str, float]:
        p = self.get_policy(level)
        return {"temperature": p.temperature, "top_p": p.top_p}

    def label(self, level: Optional[str]) -> str:
        p = self.get_policy(level)
        return f"{p.label} / {p.role}"


_DEFAULT = BridgeDivergenceController()


def normalize_persona_level(level: Optional[str]) -> str:
    return _DEFAULT.normalize_level(level)


def get_divergence_policy(level: Optional[str]) -> DivergencePolicy:
    return _DEFAULT.get_policy(level)


def decorate_prompt(base_prompt: str, level: Optional[str], task: str = "chat") -> str:
    return _DEFAULT.decorate_system_prompt(base_prompt, level, task=task)


def sampling_kwargs(level: Optional[str]) -> Dict[str, float]:
    return _DEFAULT.sampling_kwargs(level)


# ══════════════════════════════════════════════════════════════
# v0.6.6: LLM 输出后处理 - 删编造的规范号
# ══════════════════════════════════════════════════════════════
import re as _re

# 中国现行 JTG / JTJ 规范白名单 (2024-2025 现役)
_KNOWN_REG_PATTERNS = [
    # 桥梁
    r"JTG\s*5120[-\s]?2021",      # 公路桥涵养护规范
    r"JTG[/T]?\s*H21[-\s]?2011",   # 旧评定规范
    r"JTG[/T]?\s*5210[-\s]?\d+",   # 新评定规范
    r"JTG[/T]?\s*52XX[-\s]?\d+",   # 新规范报批稿
    r"JTG\s*D60[-\s]?(2015|2004)", # 设计通用规范
    r"JTG\s*D62[-\s]?(2004|2018)", # 钢混设计
    r"JTG\s*D63[-\s]?2007",        # 桥台
    r"JTG[/T]?\s*3310[-\s]?2019",  # 耐久性
    r"JTG[/T]?\s*3651[-\s]?2011",  # 桥面防水
    r"JTG[/T]?\s*J22[-\s]?2008",   # 加固设计
    r"JTG\s*B05[-\s]?2015",        # 交安
    # 路面 (路相关)
    r"JTG\s*5142[-\s]?2019",       # 路面养护
    r"JTG\s*F40[-\s]?2004",        # 沥青路面施工
    # 隧道
    r"JTG\s*H12[-\s]?2015",        # 隧道养护
    # 通用
    r"JTG\s*F80",                   # 验收
]

# 已知规范"编号-字母"前缀(用于精确控制白名单边界)
_KNOWN_REG_PREFIXES = {
    "5120-2021", "H21-2011", "52XX", "5210", "D60-2015", "D60-2004",
    "D62-2004", "D62-2018", "D63-2007", "3310-2019", "3651-2011",
    "J22-2008", "J23-2008", "B05-2015", "5142-2019", "F40-2004", "H12-2015",
    "F80", "5210-2020",
}


def is_known_regulation(spec_text: str) -> bool:
    """判断一个规范文字是否在白名单"""
    if not spec_text:
        return False
    # 完全归一化:全去 /T 空格-
    s = spec_text.replace(" ", "").replace("/", "").replace("-", "").replace("T", "").upper()
    for prefix in _KNOWN_REG_PREFIXES:
        clean_p = prefix.replace("-", "").replace(" ", "").replace("/","").replace("T","").upper()
        if clean_p in s:
            return True
    return False


def sanitize_regulations(text: str, allowed_extra: list = None) -> tuple:
    """
    扫描 LLM 输出,把编造的规范号替换为"该规范暂未引用"

    Returns: (cleaned_text, removed_specs_list)
    """
    if not text:
        return text, []

    allowed_extra = allowed_extra or []
    removed = []

    # 匹配所有 JTG 规范号(含年份/不含年份)
    pattern = _re.compile(
        r"((?:JTG|JTJ)[/T]*[\s/]*[A-Z]?\s*\d{2,4}[-\s]?\d{0,4}(?:第\d+(?:\.\d+)*条)?)"
    )

    def replace_func(m):
        spec = m.group(1)
        # 在白名单 或 在 allowed_extra
        if is_known_regulation(spec):
            return spec
        for allowed in allowed_extra:
            if allowed.replace(" ","") in spec.replace(" ",""):
                return spec
        # 不在 → 移除
        removed.append(spec)
        return "[规范引用待核查]"

    cleaned = pattern.sub(replace_func, text)
    return cleaned, removed
