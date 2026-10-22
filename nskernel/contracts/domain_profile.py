"""
DomainProfile · 领域档案契约
=============================

每个垂域实现自己的 DomainProfile,告诉 nskernel:
  - 我叫什么(name / display_name)
  - 我只处理什么范畴(scope_description / in_scope_keywords)
  - 我的三档 persona 话术长什么样(personas)
  - 我的身份锁拒绝模板(identity_lock_reply)
  - 我允许哪些计算(supported_computations)

nskernel 里所有需要"本领域特色文案"的地方,都走这个 profile 拿,
不得硬编码任何领域专有词。

设计原则:
  1. 纯数据+配置,不含任何业务逻辑
  2. 可从 YAML/JSON 加载,也可代码构造
  3. nskernel 内部只用 DomainProfile 的公开字段,不做 isinstance 判断
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable, Any


@dataclass
class PersonaProfile:
    """单档 persona 的领域化配置"""
    # persona 档位名,必须是 'rigor' / 'senior' / 'master' 之一
    level: str

    # 领域特色的角色名(显示给用户)
    # 示例 A: "某业务检测工程师" / "领域总工" / "某业务诊断大师"
    # 场景 2: "法律助理" / "资深律师" / "金牌大律师"
    role_label: str

    # 口吻风格提示(注入到 sys_prompt 里)
    tone_hint: str = ""

    # 本档允许使用的案例库(仅 master 档用)
    # 示例 A: 港珠澳/苏通/明石海峡
    # 场景 2: 最高法指导性案例库
    # 场景 3: 人教版例题库
    case_library: List[str] = field(default_factory=list)

    # 本档允许的修辞手法("比喻""类比""抒情")
    rhetoric_allowed: List[str] = field(default_factory=list)


@dataclass
class IdentityProfile:
    """身份锁配置"""
    # 我是谁(一句话)
    # "某业务观察诊断助手" / "法律咨询助手" / "K12 数学辅导助手"
    role_declaration: str

    # 我只做什么范畴
    # "某业务观察诊断和规范咨询" / "法律咨询和法规解读" / "K12 数学讲解和答疑"
    scope_description: str

    # 跑题时的礼貌拒绝模板
    # 占位符 {role} 会被 role_declaration 替换
    off_topic_reply: str = (
        "不好意思,我只负责{scope},不做其他领域的帮助。"
        "如有{role}相关问题欢迎继续描述。"
    )

    # 创作类拒绝模板(写小说/写诗等)
    creative_reply: str = (
        "我是{role},不提供创作类内容(小说/诗歌/散文/剧本等)。"
        "如需{scope}问题,请描述具体情况。"
    )

    # 领域内关键词(出现这些词的问题不算跑题)
    in_scope_keywords: List[str] = field(default_factory=list)

    # 明确出域的关键词(一出现就拦)
    # 注意:跨领域通用的创作词/闲聊词 nskernel 会自带,
    # 这里只放本域需要额外拦的
    out_of_scope_keywords: List[str] = field(default_factory=list)


@dataclass
class DomainProfile:
    """
    领域档案 - 跨行业通用 AI 内核对"领域"的唯一认识

    领域实现举例(伪代码):
      bridge_profile = DomainProfile(
          name="bridge_doctor",
          display_name="原型系统",
          language="zh-CN",
          identity=IdentityProfile(
              role_declaration="某业务观察诊断助手",
              scope_description="某业务观察诊断和规范咨询",
              in_scope_keywords=["领域词1", "领域词2", ...]
          ),
          personas=[
              PersonaProfile(level="rigor", role_label="某业务检测工程师", ...),
              PersonaProfile(level="senior", role_label="领域总工", ...),
              PersonaProfile(level="master", role_label="某业务诊断大师",
                             case_library=["港珠澳", "苏通", ...]),
          ],
      )
    """
    # ─── 身份 ───
    name: str                                      # 内部标识,如 "bridge_doctor"
    display_name: str                              # 用户看到的名字
    language: str = "zh-CN"                        # 主语言
    version: str = "1.0"

    # ─── 身份锁 ───
    identity: IdentityProfile = None

    # ─── 三档 persona ───
    personas: List[PersonaProfile] = field(default_factory=list)

    # ─── 领域扩展点(任意额外元数据)───
    # 例:某业务可以放 "industry_standards": ["规范 A", "规范 B"]
    metadata: Dict[str, Any] = field(default_factory=dict)

    # ─── 自检 ───
    def validate(self) -> List[str]:
        """自检:返回所有配置问题(空列表=通过)"""
        issues = []
        if not self.name or not self.name.isidentifier():
            issues.append(f"name 必须是合法标识符: {self.name!r}")
        if not self.display_name:
            issues.append("display_name 不能为空")
        if self.identity is None:
            issues.append("identity(IdentityProfile) 必须提供")
        else:
            if not self.identity.role_declaration:
                issues.append("identity.role_declaration 不能为空")
            if not self.identity.scope_description:
                issues.append("identity.scope_description 不能为空")

        # persona 档位必须覆盖 rigor/senior/master
        if self.personas:
            levels = {p.level for p in self.personas}
            required = {"rigor", "senior", "master"}
            missing = required - levels
            if missing:
                issues.append(f"缺失 persona 档位: {sorted(missing)}")

        return issues

    def get_persona(self, level: str) -> Optional[PersonaProfile]:
        """取某一档 persona 的配置"""
        for p in self.personas:
            if p.level == level:
                return p
        return None

    def get_off_topic_reply(self) -> str:
        """拿跑题拒绝回复"""
        if not self.identity:
            return "抱歉,这不在我的服务范围内。"
        return self.identity.off_topic_reply.format(
            role=self.identity.role_declaration,
            scope=self.identity.scope_description,
        )

    def get_creative_reply(self) -> str:
        """拿创作类拒绝回复"""
        if not self.identity:
            return "抱歉,我不提供创作类内容。"
        return self.identity.creative_reply.format(
            role=self.identity.role_declaration,
            scope=self.identity.scope_description,
        )
