"""
nskernel.cognition.projection · 多实体作用域投影

核心创新:解决"多轮对话涉及多个实体时 LLM 混用数据"的问题。
从数据源头隔离(不是在 prompt 里让 LLM 自己过滤)。
"""
from .projector import ContextProjector, ContextRenderer, ProjectionResult

__all__ = ["ContextProjector", "ContextRenderer", "ProjectionResult"]
