"""
Orchestrator · Agent 编排器(通用骨架)
======================================

职责(领域无关):
  1. 接受用户 question + session,完成从输入到最终回复的全流程编排
  2. 按固定管线依次调用:
     [A] 身份锁检查                 IdentityLockGuard
     [B] 计算意图识别+执行          Calculator (可选)
     [C] 实体作用域投影             ContextProjector
     [D] 意图识别 + Agents 分发     IntentDetector + AgentBase[]
     [E] LLM 合成                   LLMClient + PersonaRouter
     [F] 输出符号校验               ClaimVerifier (可选)

  3. 中间任何一步失败时,有明确的降级/兜底策略
     (LLM 不可用 → 拼接 agent_outputs 原文等)

对比原型系统的 orchestrator.py(2000 行):
  旧版把"意图路由、某业务专属路径、大量 if-else"全塞在 handle_followup 里。
  新版是骨架,真正的领域逻辑(哪些 Agent 处理哪个 intent)
  由领域注册表决定,骨架保持简洁。

领域如何使用:
  1. 实例化:orch = Orchestrator(profile, projector, guard, ...)
  2. 注册 Agents:orch.register_agent(my_agent, triggers=["重要度","处治"])
  3. 调用:reply = orch.handle(question, session)
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

from nskernel.dialogue.agent import AgentBase, AgentInput, AgentOutput

if TYPE_CHECKING:
    from nskernel.contracts import DomainProfile, Observation
    from nskernel.cognition.persona import PersonaRouter
    from nskernel.cognition.projection import ContextProjector, ContextRenderer
    from nskernel.cognition.identity import IdentityLockGuard
    from nskernel.computation import Calculator
    from nskernel.dialogue.session import Session


# ══════════════════════════════════════════════════════════════
# 数据结构
# ══════════════════════════════════════════════════════════════
@dataclass
class AgentRegistration:
    """Agent 注册项"""
    agent: AgentBase
    trigger_intents: List[str] = field(default_factory=list)
                                          # 哪些 intent 命中时触发该 Agent
                                          # 空列表 = 每次都触发
    priority: int = 100                   # 优先级(小的先跑)


@dataclass
class OrchestrationResult:
    """
    编排一次请求的完整结果(便于调试和日志)
    """
    question: str
    reply: str                            # 最终给用户的回复
    branch: str = ""                      # 走了哪条分支("identity_lock" / "computation" / "llm_synthesis")
    blocked_by_identity: bool = False
    used_calculator: bool = False
    active_entity: Optional[str] = None
    triggered_agents: List[str] = field(default_factory=list)
    persona_level: str = ""
    debug: Dict[str, Any] = field(default_factory=dict)


# ══════════════════════════════════════════════════════════════
# Orchestrator - 通用编排器
# ══════════════════════════════════════════════════════════════
class Orchestrator:
    """
    通用编排器骨架

    必需依赖:
      - domain_profile: DomainProfile(领域档案)

    可选依赖(不传就跳过相应步骤):
      - identity_guard:  IdentityLockGuard(身份锁)
      - calculator:      Calculator(符号计算)
      - projector:       ContextProjector(实体投影)
      - renderer:        ContextRenderer(context 渲染)
      - persona_router:  PersonaRouter(人格路由)
      - llm_client:      LLMClient 实现(neural side)
      - intent_detector: 一个 callable(question) -> List[str]
      - claim_verifier:  ClaimVerifier 实例(输出校验,后续实现)
    """

    def __init__(
        self,
        domain_profile: "DomainProfile",
        identity_guard: Optional["IdentityLockGuard"] = None,
        calculator: Optional["Calculator"] = None,
        projector: Optional["ContextProjector"] = None,
        renderer: Optional["ContextRenderer"] = None,
        persona_router: Optional["PersonaRouter"] = None,
        llm_client: Optional[Any] = None,
        intent_detector: Optional[Callable[[str], List[str]]] = None,
        claim_verifier: Optional[Any] = None,
    ):
        self.profile = domain_profile
        self.identity_guard = identity_guard
        self.calculator = calculator
        self.projector = projector
        self.renderer = renderer
        self.persona_router = persona_router
        self.llm_client = llm_client
        self.intent_detector = intent_detector
        self.claim_verifier = claim_verifier

        self._agents: List[AgentRegistration] = []

        # 默认 persona 档位(low/medium/high)
        self.default_persona_level = "rigor"

    # -----------------------------------------------------------------
    # Agent 注册
    # -----------------------------------------------------------------
    def register_agent(
        self,
        agent: AgentBase,
        trigger_intents: Optional[List[str]] = None,
        priority: int = 100,
    ) -> None:
        """
        注册一个 Agent
          agent:            Agent 实例
          trigger_intents:  命中这些 intent 之一才触发(None=总是触发)
          priority:         优先级(数字小的先跑)
        """
        self._agents.append(AgentRegistration(
            agent=agent,
            trigger_intents=trigger_intents or [],
            priority=priority,
        ))
        self._agents.sort(key=lambda r: r.priority)

    def clear_agents(self) -> None:
        self._agents.clear()

    def registered_agents(self) -> List[str]:
        return [r.agent.agent_name for r in self._agents]

    # -----------------------------------------------------------------
    # 核心入口
    # -----------------------------------------------------------------
    def handle(
        self,
        question: str,
        session: "Session",
        persona_level: Optional[str] = None,
    ) -> OrchestrationResult:
        """
        处理一次用户请求

        Args:
          question: 用户这一轮的问题
          session: 会话对象(可通过 session_store.get_or_create 取得)
          persona_level: 持续使用的 persona 档位(默认 rigor)

        Returns:
          OrchestrationResult(含 reply 和调试信息)
        """
        level = persona_level or self.default_persona_level
        result = OrchestrationResult(
            question=question,
            reply="",
            persona_level=level,
        )

        # 记录 user 消息
        session.add_user_message(question)

        # ═══ A. 身份锁检查 ═══
        if self.identity_guard:
            decision = self.identity_guard.check(question)
            if decision.should_block:
                result.branch = "identity_lock"
                result.blocked_by_identity = True
                result.reply = decision.reply_text
                result.debug["lock_reason"] = decision.reason
                session.add_assistant_message(result.reply)
                return result

        # ═══ B. 计算意图 ═══
        # v0.4 修:只在 calc 真的成功时才走 computation 分支,
        # 参数不足/失败时继续 LLM 合成,让 LLM 基于 metadata 硬数据回答
        if self.calculator:
            calc_result = self.calculator.try_auto_compute(question)
            if calc_result is not None and calc_result.success:
                result.branch = "computation"
                result.used_calculator = True
                result.reply = self._format_computation_reply(calc_result)
                result.debug["calc_value"] = calc_result.value
                result.debug["calc_steps"] = calc_result.steps
                # 注意:计算意图通常是"一步到位"的,不继续走 LLM 合成
                session.add_assistant_message(result.reply)
                return result
            elif calc_result is not None and not calc_result.success:
                # 记录但不截流,让 LLM 看看能不能基于 metadata 回答
                result.debug["calc_attempted"] = calc_result.formula_id
                result.debug["calc_error"] = calc_result.error

        # ═══ C. 实体作用域投影 ═══
        observations = session.get_all_observations()
        projected_context = ""
        projected_obs = observations

        if self.projector and observations:
            projection = self.projector.detect_and_project(question, observations)
            projected_obs = projection.observations
            result.active_entity = projection.entity_key
            result.debug["projection_source"] = projection.detection_source

            if self.renderer:
                projected_context = self.renderer.render(projection)

        # ═══ D. 意图识别 + Agents 分发 ═══
        intents: List[str] = []
        if self.intent_detector:
            try:
                intents = list(self.intent_detector(question) or [])
            except Exception as e:
                result.debug["intent_error"] = str(e)

        result.debug["intents"] = intents

        agent_outputs: List[AgentOutput] = []
        for reg in self._agents:
            # 按 intent 过滤
            if reg.trigger_intents and not any(i in intents for i in reg.trigger_intents):
                continue

            agent_input = AgentInput(
                question=question,
                observations=projected_obs,
                context={
                    "session_id": session.session_id,
                    "active_entity": result.active_entity,
                    "context_text": projected_context,
                },
                intents=intents,
            )

            if not reg.agent.applies_to(agent_input):
                continue

            try:
                output = reg.agent.run(agent_input)
                agent_outputs.append(output)
                result.triggered_agents.append(reg.agent.agent_name)
            except Exception as e:
                result.debug.setdefault("agent_errors", []).append(
                    f"{reg.agent.agent_name}: {e}"
                )

        # ═══ E. LLM 合成 ═══
        # v0.3.1: 即使没 context 没 agent,只要有 LLM 就让它回答
        llm_error_detail = None
        if self.llm_client:
            result.branch = "llm_synthesis"
            try:
                reply = self._synthesize_with_llm(
                    question=question,
                    context_text=projected_context,
                    agent_outputs=agent_outputs,
                    persona_level=level,
                    session=session,  # v0.4: 传 session 让 LLM 看到 metadata 硬数据
                )
                # 如果 LLM 返回的是"调用失败"字符串(我们的真实客户端会这样),
                # 把它也当成错误
                if reply and reply.startswith("[") and "调用失败" in reply[:50]:
                    llm_error_detail = reply
                    reply = self._fallback_from_agents(
                        agent_outputs, projected_context,
                        llm_error=llm_error_detail,
                    )
            except Exception as e:
                import traceback
                llm_error_detail = f"{type(e).__name__}: {e}"
                result.debug["llm_error"] = llm_error_detail
                result.debug["llm_traceback"] = traceback.format_exc()
                print(f"\n❌ LLM 调用失败: {llm_error_detail}")
                traceback.print_exc()
                reply = self._fallback_from_agents(
                    agent_outputs, projected_context,
                    llm_error=llm_error_detail,
                )
        else:
            # 无 LLM → 降级
            result.branch = "fallback_no_llm"
            reply = self._fallback_from_agents(agent_outputs, projected_context)

        # ═══ F. 输出校验(可选) ═══
        if self.claim_verifier and reply:
            try:
                cleaned = self.claim_verifier.clean_text(
                    reply,
                    context={
                        "question": question,
                        "observations": projected_obs,
                    },
                )
                if cleaned != reply:
                    result.debug["claim_verifier_altered"] = True
                reply = cleaned
            except Exception as e:
                result.debug["verifier_error"] = str(e)

        result.reply = reply or "抱歉,暂时无法给出回复,请稍后重试。"
        session.add_assistant_message(result.reply)
        return result

    # -----------------------------------------------------------------
    # 辅助:计算结果格式化
    # -----------------------------------------------------------------
    def _format_computation_reply(self, calc_result: Any) -> str:
        """把 CalculationResult 格式化成人类可读"""
        parts = []
        if calc_result.display:
            parts.append(calc_result.display)
        else:
            parts.append(f"{calc_result.value} {calc_result.unit}".strip())

        if calc_result.steps:
            parts.append("\n计算过程:")
            for i, step in enumerate(calc_result.steps, 1):
                parts.append(f"  {i}. {step}")

        if calc_result.authority_ref:
            parts.append(f"\n依据: {calc_result.authority_ref}")

        return "\n".join(parts)

    # -----------------------------------------------------------------
    # 辅助:LLM 合成
    # -----------------------------------------------------------------
    def _synthesize_with_llm(
        self,
        question: str,
        context_text: str,
        agent_outputs: List[AgentOutput],
        persona_level: str,
        session: Optional["Session"] = None,
    ) -> str:
        """
        用 LLM 合成最终回复

        默认实现:
          1. 构造 sys_prompt(身份锁段 + 合成要求)
          2. 构造 user_content(问题 + context + agent outputs + session.metadata 硬数据)
          3. 用 PersonaRouter 变换 prompt
          4. 调 LLM client

        v0.4: 把 session.metadata 里领域方放的硬数据也注入上下文
              (比如桥梁场景的 Dr/BCI/结构识别结果)
        """
        # 身份锁段落
        identity_block = ""
        if self.profile.identity:
            identity_block = (
                f"━━━━ 严格身份边界 ━━━━\n"
                f"你是{self.profile.identity.role_declaration}。\n"
                f"只处理{self.profile.identity.scope_description}。\n"
                f"━━━━ 严格身份边界 ━━━━\n"
            )

        # 基础 sys_prompt
        base_sys = (
            identity_block +
            f"\n请基于下方的'硬数据'综合回答用户问题。\n"
            f"绝对不得编造数据、数字、权威引用;"
            f"所有事实性内容必须来自硬数据。\n"
        )

        # 构造 user 消息
        user_parts = [f"用户问题: {question}", ""]

        # v0.4: 从 session.metadata 抽硬数据(领域方放)
        if session is not None and session.metadata:
            meta_lines = self._render_session_metadata(session.metadata)
            if meta_lines:
                user_parts.extend([
                    "【已验证的硬数据 - 必须基于这些回答,数字不得编造】",
                    *meta_lines,
                    "",
                ])

        if context_text:
            user_parts.extend(["【观察上下文 - 实体投影后】", context_text, ""])
        if agent_outputs:
            user_parts.append("【各智能体分析】")
            for o in agent_outputs:
                user_parts.append(f"  [{o.agent_name}] {o.data}")
            user_parts.append("")
        user_parts.extend([
            "",
            "请严格基于上述【已验证的硬数据】和【观察上下文】回答。",
            "不得编造数字、Dr 值、BCI、规范条款号。",
            "如数据不足,直接说'请先上传检测报告'。",
        ])
        user_content = "\n".join(user_parts)

        # Persona 变换
        if self.persona_router:
            persona = self.persona_router.get(persona_level)
            spec = persona.build_spec(
                sys_prompt=base_sys,
                user_content=user_content,
                domain_profile=self.profile,
                task="chat",
            )
            return self.llm_client.complete(
                sys_prompt=spec.sys_prompt,
                user_content=spec.user_content,
                temperature=spec.temperature,
                top_p=spec.top_p,
            )
        else:
            return self.llm_client.complete(
                sys_prompt=base_sys,
                user_content=user_content,
            )

    def _render_session_metadata(self, metadata: Dict[str, Any]) -> List[str]:
        """
        把领域方在 session.metadata 里放的硬数据渲染成 LLM 可读的多行文本

        约定:
          - metadata 里 key 以 'v7_xxx' 或 '_xxx' 开头的视为内部状态,跳过
          - 值是 dict 的展开关键字段;是 str/num/bool 的直接展示;list 取前几个

        设计哲学:通用做法,不仅限桥梁 - 任何领域都可以往 metadata 填硬数据
        """
        lines = []
        for k, v in metadata.items():
            if k.startswith("_") or k == "evolved_from":
                continue
            if not v:
                continue

            if isinstance(v, dict):
                # 取前 8 个字段
                inner = []
                for ik, iv in list(v.items())[:8]:
                    if ik.startswith("_"):
                        continue
                    if isinstance(iv, (dict, list)):
                        if isinstance(iv, dict):
                            iv_str = ", ".join(f"{k2}={v2}" for k2, v2 in list(iv.items())[:5] if not str(k2).startswith("_"))
                        else:
                            iv_str = ", ".join(str(x) for x in iv[:5])
                        inner.append(f"{ik}: {iv_str}")
                    else:
                        inner.append(f"{ik}: {iv}")
                if inner:
                    lines.append(f"  ▸ {k}:")
                    for x in inner:
                        lines.append(f"      {x}")
            elif isinstance(v, list):
                if v:
                    lines.append(f"  ▸ {k}: {', '.join(str(x) for x in v[:5])}")
            else:
                lines.append(f"  ▸ {k}: {v}")

        return lines
    def _fallback_from_agents(
        self,
        agent_outputs: List[AgentOutput],
        context_text: str,
        llm_error: Optional[str] = None,
    ) -> str:
        """
        LLM 不可用时:把 agent_outputs 拼起来返回
        保证用户至少能看到符号侧的硬数据
        """
        if not agent_outputs and not context_text:
            # 完全没有硬数据可展示 → 把真实的错误告诉用户
            if llm_error:
                hint = self._explain_llm_error(llm_error)
                return (
                    f"⚠️ 无法调用 LLM 服务\n\n"
                    f"技术原因:{llm_error[:200]}\n\n"
                    f"{hint}"
                )
            return (
                f"抱歉,{self.profile.display_name}暂时无法处理这个问题。"
                f"请检查服务配置或稍后重试。"
            )

        parts = []
        if llm_error:
            parts.append(f"⚠️ LLM 合成失败 ({llm_error[:100]}),以下是符号引擎的原始分析:\n")
        else:
            parts.append("AI 合成服务不可用,以下是符号引擎的原始分析:\n")

        if context_text:
            parts.append(context_text)
            parts.append("")

        for o in agent_outputs:
            parts.append(f"【{o.agent_name}】")
            parts.append(o.data)
            parts.append("")

        return "\n".join(parts)

    def _explain_llm_error(self, err: str) -> str:
        """根据错误信息给友好提示"""
        err_lower = err.lower()
        if "no module" in err_lower or "modulenotfound" in err_lower:
            if "zhipuai" in err_lower:
                return "💡 请执行: pip install zhipuai 安装智谱 SDK"
            if "openai" in err_lower:
                return "💡 请执行: pip install openai"
            return "💡 缺少依赖包,请根据错误信息安装"
        if "api_key" in err_lower or "apikey" in err_lower or "401" in err or "403" in err:
            return "💡 API key 无效或权限不足,请检查 .env 文件里的 key 是否正确"
        if "timeout" in err_lower or "timed out" in err_lower:
            return "💡 请求超时,可能是网络问题,稍后重试"
        if "rate" in err_lower or "429" in err:
            return "💡 触发限流,请稍后重试"
        if "balance" in err_lower or "quota" in err_lower or "insufficient" in err_lower:
            return "💡 余额不足或配额用完,请充值"
        if "connection" in err_lower or "network" in err_lower:
            return "💡 网络连接问题,检查网络或 base_url"
        return "💡 请检查终端详细报错,或 API key/网络/依赖"
