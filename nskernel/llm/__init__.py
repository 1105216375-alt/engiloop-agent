"""nskernel.llm · LLM 抽象层"""
from .client import LLMClientBase, LLMResponse, MockLLMClient, EchoLLMClient

__all__ = ["LLMClientBase", "LLMResponse", "MockLLMClient", "EchoLLMClient"]
