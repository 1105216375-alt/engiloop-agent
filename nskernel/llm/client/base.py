"""
LLMClient · LLM 抽象基类
=========================

定义 LLM 调用的最小接口,领域/实现方实现 complete() 即可对接任何 provider。

内置:
  - LLMClientBase (抽象)
  - MockLLMClient  (测试/开发用,返回可预测的 mock 回复)
  - EchoLLMClient  (回显 prompt + user 的前 N 字,调试用)

生产 provider(智谱/OpenAI/Claude)另外实现,见 nskernel.llm.providers/
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class LLMResponse:
    """标准 LLM 响应"""
    content: str
    finish_reason: str = "stop"
    usage: Dict[str, int] = field(default_factory=dict)
                                              # {"prompt_tokens","completion_tokens"}
    provider: str = ""
    model: str = ""
    raw: Optional[Dict] = None                # 原始 provider 响应(调试用)


class LLMClientBase(ABC):
    """
    最小 LLM 客户端接口

    Orchestrator 只依赖 complete(sys_prompt, user_content, temperature, top_p) -> str
    provider 可按需返回 LLMResponse 或 str
    """

    provider_name: str = "base"
    model_name: str = ""

    @abstractmethod
    def complete(
        self,
        sys_prompt: str,
        user_content: str,
        temperature: float = 0.5,
        top_p: float = 0.85,
        **extra,
    ) -> str:
        """
        同步补全调用
        返回纯文本(不含角色/包装)
        """
        ...

    def complete_detailed(
        self,
        sys_prompt: str,
        user_content: str,
        temperature: float = 0.5,
        top_p: float = 0.85,
        **extra,
    ) -> LLMResponse:
        """
        详细版调用(默认 wrap complete() 返回 LLMResponse)
        provider 可重写以返回真正的 usage/metadata
        """
        content = self.complete(
            sys_prompt=sys_prompt,
            user_content=user_content,
            temperature=temperature,
            top_p=top_p,
            **extra,
        )
        return LLMResponse(
            content=content,
            provider=self.provider_name,
            model=self.model_name,
        )

    def describe(self) -> Dict[str, Any]:
        return {
            "provider": self.provider_name,
            "model": self.model_name,
        }


# ══════════════════════════════════════════════════════════════
# MockLLMClient - 测试专用
# ══════════════════════════════════════════════════════════════
class MockLLMClient(LLMClientBase):
    """
    确定性 Mock LLM - 用于单元测试

    按关键字返回预设回复,不命中则返回默认 echo
    """

    provider_name = "mock"
    model_name = "mock-v1"

    def __init__(
        self,
        rules: Optional[Dict[str, str]] = None,
        default_reply: str = "[mock] 已收到问题,正常响应。",
    ):
        self.rules = rules or {}
        self.default_reply = default_reply
        self.call_history: List[Dict[str, Any]] = []

    def complete(
        self,
        sys_prompt: str,
        user_content: str,
        temperature: float = 0.5,
        top_p: float = 0.85,
        **extra,
    ) -> str:
        self.call_history.append({
            "sys_prompt": sys_prompt,
            "user_content": user_content,
            "temperature": temperature,
            "top_p": top_p,
        })
        for key, reply in self.rules.items():
            if key in user_content:
                return reply
        return self.default_reply


class EchoLLMClient(LLMClientBase):
    """回显型 - 调试用,把接到的 prompt/user 前 N 字发回去"""

    provider_name = "echo"
    model_name = "echo-debug"

    def __init__(self, max_chars: int = 200):
        self.max_chars = max_chars

    def complete(self, sys_prompt, user_content, temperature=0.5, top_p=0.85, **extra):
        return (
            f"[ECHO] sys_prompt({len(sys_prompt)}字): "
            f"{sys_prompt[:self.max_chars]}...\n"
            f"user_content({len(user_content)}字): "
            f"{user_content[:self.max_chars]}...\n"
            f"(temperature={temperature}, top_p={top_p})"
        )
