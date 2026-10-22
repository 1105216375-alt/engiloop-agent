"""
Shared Guards · 跨 Persona 共享的底线守卫
========================================

跨档位通用的工具,主要作用:

  1. extract_hard_rules(sys_prompt)
     从原始 sys_prompt 里抽出"硬禁令段落",供 senior/master 档在重写
     prompt 时作为必须保留的部分拼接到末尾。

  2. CORE_FLOOR_RULES_TEMPLATE
     通用的"绝对底线"文案模板,领域可以往里填补充项。
     nskernel 内置的是通用条款(严禁编造数据/循环引用/markdown堆砌等),
     领域特有的铁律(场景 1: 业务对象归因铁律;场景 2: 不得代替律师发函等)
     从 DomainProfile.metadata.domain_floor_rules 读取,动态拼接。

提纯说明(对比原型系统旧版 shared_guards.py):
  - 旧版的 CORE_FLOOR_RULES 硬编码了领域专有词("类别 X ≠ 类别 Y ≠ 类别 Z")
  - 新版只保留领域无关的底线,领域特色放到 DomainProfile.metadata
"""
from __future__ import annotations
import re
from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from nskernel.contracts import DomainProfile


# ══════════════════════════════════════════════════════════════
# 硬禁令段落识别(领域无关)
# ══════════════════════════════════════════════════════════════
# 任何段落首行出现这些词之一,就被视为"硬禁令段落",
# 所有 persona 必须保留(包括激进重写的 master 档)
_HARD_RULE_START_MARKERS = (
    "【严禁", "【绝对", "【铁律", "【禁令", "【硬规则",
    "【不得", "【必须", "【禁止", "【警告",
)

# 结构化指令词(master 档要打破结构,这些要扔掉)
_STRUCTURE_INDICATORS = (
    "先用一段", "紧接着一段", "然后列出",
    "最后视情况", "按以下顺序", "按以下固定",
    "分以下", "依次输出",
)

# 高密度禁令段判断用的关键词
_FORBIDDEN_KEYWORDS = (
    "严禁", "绝对不", "铁律", "禁令",
    "不得", "不可编造", "不编造", "切勿", "禁止",
)


def extract_hard_rules(sys_prompt: str) -> str:
    """
    从原始 sys_prompt 中抽出"硬底线段落"
    用于 senior/master 档在重写 prompt 时拼回到新 prompt 末尾

    规则(严格保守,宁可多保留):
      A) 段首命中 _HARD_RULE_START_MARKERS → 整段保留
      B) 整段含 ≥2 个禁令词(高密度硬约束) → 整段保留
      C) 含结构化指令词 → 排除(master 档要打破结构)
    """
    if not sys_prompt:
        return ""

    segments = re.split(r"\n\n+", sys_prompt)
    kept: List[str] = []

    for seg in segments:
        s = seg.strip()
        if not s:
            continue

        # C) 排除纯结构指令段
        if any(ind in s for ind in _STRUCTURE_INDICATORS):
            continue

        # A) 段首/前两行含硬禁令标记 → 保留
        first_two = "\n".join(s.split("\n")[:2])
        if any(marker in first_two for marker in _HARD_RULE_START_MARKERS):
            kept.append(s)
            continue

        # B) 高密度禁令段 → 保留
        density = sum(s.count(w) for w in _FORBIDDEN_KEYWORDS)
        if density >= 2:
            kept.append(s)

    return "\n\n".join(kept)


# ══════════════════════════════════════════════════════════════
# 通用绝对底线(领域无关)
# ══════════════════════════════════════════════════════════════
# 每个领域的 master 档在激进重写 prompt 时,
# 必须把下面这段拼到末尾(作为保险的最后一道约束)
UNIVERSAL_FLOOR_RULES = """
════ 绝对底线(所有档位通用,不可越界) ════
1. 严禁编造任何数据、数字、权威引用、专业术语。
   所有事实性内容必须来自上方系统给出的硬数据。
2. 严禁循环引用("同上"、"与X类似"、"参见前文")。
   即使两项结论相同,也要各自完整写一次。
3. 严禁使用"可能/也许/大概"等虚弱词汇代替明确判断。
   基于已有事实下判断,不和稀泥。
4. 不使用 markdown 堆砌(不要 **加粗**、### 标题、excessive 列表)。
5. 功力体现在对"已有事实"的深刻解读,不在于编造新事实。
"""


def build_floor_rules(
    domain_profile: Optional["DomainProfile"] = None,
) -> str:
    """
    构造完整的底线文本 = 通用底线 + 领域专有铁律

    领域专有铁律放在 DomainProfile.metadata["domain_floor_rules"] 里
    (一段字符串,领域自己写什么就是什么)

    某业务示例:
      "6. 严禁违反业务对象归因铁律:类别 X ≠ 类别 Y ≠ 类别 Z;边缘类别破损 ≠ 核心类别破损;
          运营期老设备默认首选劣化成因..."

    法律示例:
      "6. 严禁替代律师发出正式法律意见书;
       7. 涉及具体案件的最终意见必须建议当事人咨询执业律师..."
    """
    text = UNIVERSAL_FLOOR_RULES.rstrip()
    if domain_profile is None:
        return text
    domain_rules = (domain_profile.metadata or {}).get("domain_floor_rules", "")
    if domain_rules:
        text += "\n" + domain_rules.lstrip()
    return text
