"""
SeniorPersona · 资深风范档(通用)
==================================

中温度,软化原 prompt 的结构锁,注入本领域的资深专家口吻。

提纯说明(对比原型系统的 senior_engineer_persona):
  - 旧版把"本领域资深从业者"硬编码在 prompt 模板里
  - 新版完全从 DomainProfile.PersonaProfile 动态注入:
      * role_label = "领域总工" / "资深律师" / "特级教师"
      * tone_hint = "用总工口吻讲机理" / "用法官视角析法理" / "用教师方法讲思路"
  - 所以 nskernel 的 SeniorPersona 完全不含任何领域词

本档 prompt 结构:
  [身份锁(如有)] + [通用的资深口吻骨架] + [领域注入] + [原硬规则]
"""
from typing import Optional, Dict, Any
from .base import BasePersona, extract_identity_lock


# 资深档的"口吻骨架"(领域无关) — 是通用的行文要求
_SENIOR_SKELETON = """你是一位在本领域深耕多年的资深{role_label}。
用户咨询了一个专业问题,系统已经通过符号推理引擎完成了事实核查和数据准备,
权威引用、关键参数、推理结论等硬数据已经喂给你了。
请以资深专家的口吻给出一份专业分析。

═══════ 资深口吻的质感 ═══════

判断力:
  - 不用"可能/也许/大概"等虚弱词,该下判断就下
  - 基于硬数据做明确结论,不和稀泥

机理深度:
  - 不止描述现象,要讲清背后的机理/因果/规律
  - 结合工程/法理/教育/医学的经验,讲"为什么会这样"

专业经验:
  - 给出可执行的下一步建议,步骤要有先后次序
  - 区分必要动作和补充动作

═══════ 行文规范 ═══════
- 完整度要保住:识别→判定→分析→建议,层次分明,不能跳段
- 每条结论/建议必须基于上方的硬数据,**不得编造新事实**
- 语言像面对面讲解,避免客套开场,直接进入正题
{tone_hint_block}

═══════ 绝对底线 ═══════
- 数据、引用、参数、结论一字不得偏离系统给出的内容
- 严禁编造数字、权威引用、专业术语
- 功力体现在对已有事实的深刻解读,不在于编造新事实
"""


class SeniorPersona(BasePersona):
    level = "senior"
    temperature = 0.75
    top_p = 0.92

    def transform_sys_prompt(
        self,
        sys_prompt: str,
        domain_profile=None,
        task: str = "generic",
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        # 1. 识别身份锁(来自上层)
        identity_lock = extract_identity_lock(sys_prompt or "")

        # 2. 从 DomainProfile 拿本领域的资深档配置
        role_label = self.get_role_label(domain_profile)
        tone_hint = self.get_tone_hint(domain_profile)
        tone_hint_block = f"\n- 口吻提示: {tone_hint}\n" if tone_hint else ""

        # 3. 填充骨架
        senior_head = _SENIOR_SKELETON.format(
            role_label=role_label,
            tone_hint_block=tone_hint_block,
        )

        # 4. 保留原 prompt 里的硬规则段
        # nskernel 约定:上层把"必须保留的硬约束"用身份锁标记包起来
        # 或者用 <hard_rules>...</hard_rules> 之类的标签
        # 这里默认用 extract_hard_rules 工具(后续实现)
        from .shared_guards import extract_hard_rules
        extracted = extract_hard_rules(sys_prompt or "")

        parts = []
        if identity_lock:
            parts.append(identity_lock)
            parts.append("\n【若用户输入属于本领域范畴】请按以下风格回答:\n")

        parts.append(senior_head)

        if extracted:
            parts.append(
                "\n═══════ 原系统附加硬规则(以下约束必须严守) ═══════\n\n"
                + extracted
            )

        return "\n".join(parts)
