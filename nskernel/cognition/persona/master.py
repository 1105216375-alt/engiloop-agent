"""
MasterPersona · 大师手笔档(通用)
==================================

高温度,激进重写原 prompt,注入本领域的大师口吻+案例库。
这是最激进、最有"灵魂"的档。

提纯说明(对比原型系统旧版 master_persona.py):
  旧版在 prompt 里硬编码了:
    - 本领域的权威经典案例(由 DomainProfile 注入)
    - "明石海峡 DAI 干燥空气注入" 等国际某业务案例
    - 具体的"虚构示范"段(连云港0.25mm裂缝)

  新版完全从 DomainProfile.PersonaProfile.case_library 动态注入:
    - 某业务 master: case_library=["港珠澳", "苏通", "明石海峡", ...]
    - 法律 master: case_library=["指导性案例 12 号", "红牛商标争议", ...]
    - 教学 master: case_library=["奥数经典:鸡兔同笼变式", ...]

  所以 nskernel 的 MasterPersona 完全不含领域词。

本档 prompt 结构:
  [身份锁(必保留)]
  + [通用大师口吻骨架]
  + [本领域 role_label + tone_hint 注入]
  + [本领域 case_library 注入]
  + [通用 floor rules + 领域 domain_floor_rules]

关键设计:
  master 档"打破结构"是指打破写作格式的 shell,
  但绝对不打破硬规则(floor_rules 和身份锁)。
  这是大师和瞎说的边界。
"""
from typing import Optional, Dict, Any
from .base import BasePersona, extract_identity_lock
from .shared_guards import extract_hard_rules, build_floor_rules


# ══════════════════════════════════════════════════════════════
# 大师档的通用骨架 —— 绝对不含领域词
# ══════════════════════════════════════════════════════════════
_MASTER_HEAD_TEMPLATE = """你是一位在本领域达到顶尖水平的{role_label}。
用户咨询了一个专业问题,系统已经通过符号推理引擎完成了事实核查:
权威引用、关键参数、诊断结论等硬数据已经备好,请你调用自己的全部经验
和洞察力,给出一份真正有深度、有灵魂的大师级分析。

═══════ 大师的行文特征 ═══════

洞察深度:
  - 透过表象看本质。一个现象背后是什么规律、什么力学/法理/认知机制,
    讲得清清楚楚。不止"是什么",更要"为什么会这样"。
  - 不和稀泥,下判断要斩钉截铁 —— "这就是...原因","建议...而不是..."

类比与案例:
  - 适当调用类似的经典案例,让用户瞬间明白问题的性质和应对思路
  - 可以做合理的专业类比,但类比必须与事实一致,不得硬凑

经验质感:
  - 有"讲给同行听"的平等感,不做"给AI用户说明"的居高临下
  - 该警告就警告("这里不能大意,我见过..."),该鼓励就鼓励

═══════ 行文自由度 ═══════
- 打破所有结构锁 —— 不套用"先X、然后Y"的固定格式
- 像在现场做一次 30 分钟的专家讲座那样自然流动
- 但必要信息(核心结论/关键数据/行动建议)不能漏

{role_tone_block}
{case_library_block}

═══════ 不变的大师底线 ═══════
大师不瞎说。自由度只体现在"如何讲",不体现在"讲什么"。
所有数据、引用、结论一字不得偏离系统下方的硬数据,
下面的硬规则/铁律也必须严守。
"""


def _build_role_tone_block(role_label: str, tone_hint: str) -> str:
    """构造"本领域口吻"注入块"""
    if not tone_hint:
        return f"※ 按资深 {role_label} 的口吻自然讲述。"
    return f"""═══════ 本领域 {role_label} 的口吻特征 ═══════
{tone_hint}"""


def _build_case_library_block(case_list: list, role_label: str) -> str:
    """构造"本领域案例库"注入块"""
    if not case_list:
        return ""
    case_lines = "\n  ".join(f"- {c}" for c in case_list)
    return f"""═══════ 可调用的经典案例(仅 {role_label} 档可引用) ═══════
引用时请结合当前问题的具体情境,不要生硬罗列。
  {case_lines}

引用要点:
  - 案例必须与当前问题有**实质相似性**,不是名气大就拿来说
  - 引用时点到为止,重点是把本案例的教训/方法映射到用户的问题上
  - 不得编造案例细节;只能引用这些案例"作为对照角度",
    不能假装知道这些案例的具体数字
"""


class MasterPersona(BasePersona):
    level = "master"
    temperature = 0.9
    top_p = 0.95

    def transform_sys_prompt(
        self,
        sys_prompt: str,
        domain_profile=None,
        task: str = "generic",
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        # 1. 识别身份锁(最优先保留)
        identity_lock = extract_identity_lock(sys_prompt or "")

        # 2. 从 DomainProfile 取领域配置
        role_label = self.get_role_label(domain_profile)
        tone_hint = self.get_tone_hint(domain_profile)
        case_library = self.get_case_library(domain_profile)

        # 3. 构造大师骨架
        role_tone_block = _build_role_tone_block(role_label, tone_hint)
        case_library_block = _build_case_library_block(case_library, role_label)

        master_head = _MASTER_HEAD_TEMPLATE.format(
            role_label=role_label,
            role_tone_block=role_tone_block,
            case_library_block=case_library_block,
        )

        # 4. 抽出原 prompt 里的硬规则(过滤掉结构锁)
        extracted = extract_hard_rules(sys_prompt or "")

        # 5. 拼通用底线 + 领域底线
        floor = build_floor_rules(domain_profile)

        # 6. 按优先级组装
        parts = []

        # 身份锁永远在最前
        if identity_lock:
            parts.append(identity_lock)
            parts.append(
                "\n【若用户输入在本领域范畴内】请按以下大师风格回答:\n"
            )

        parts.append(master_head)

        if extracted:
            parts.append(
                "\n═══════ 原系统附加硬规则(绝对严守) ═══════\n\n"
                + extracted
            )

        parts.append("\n" + floor)

        return "\n".join(parts)
