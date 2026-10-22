"""
ComputationSpec · 符号计算规范契约 ⭐ 核心创新
================================================

回答一个问题:**在本领域,哪些数值问题必须走符号计算,不能交给 LLM?**

LLM 在"确定性计算"上是极其危险的:
  - 场景 2: 诉讼时效算错一天 → 败诉
  - 场景 4: 剂量算错 10% → 可能死人
  - 场景 5: 复利算错一位 → 客户投诉
  - 场景 3: 数学题算错 → 误导学生
  - 场景 1: Dr 值算错 → 评级出错

所以 nskernel 的铁律:
  **所有领域里有确定答案的数值计算,必须走 Calculator,
    LLM 只能用自然语言描述,不能自己算出结果。**

工作流:
  1. 意图识别发现"这是个计算问题"(比如"这个案子的诉讼时效是多少天")
  2. LLM 不直接回答,而是生成 CalculationRequest 调用 Calculator
  3. Calculator 查 ComputationSpec 里注册的公式,执行符号计算
  4. 把计算结果作为"硬数据" → LLM 只负责用自然语言包装

ComputationSpec 的内容:
  - 本领域支持哪些公式(formula_id → 公式定义)
  - 本领域的单位换算表(日/月/年, 克/千克, mm/cm/m)
  - 本领域禁止 LLM 自行计算的场景列表
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Union


@dataclass
class FormulaDef:
    """
    公式定义 - 一个可符号计算的事项

    示例:
      某业务 Dr 公式:
        FormulaDef(
            formula_id="bridge.dr_calc",
            name="Dr 值计算",
            description="业务对象评分 Dr = 100 - Σ(w_i × DP_i)",
            inputs=["components", "deduction_points"],
            output_type="number",
            output_unit="分",
            expression_or_callable=calc_dr_fn,
            authority_ref="《示例规范》第 X.X 条",
        )

      法律诉讼时效:
        FormulaDef(
            formula_id="law.statute_of_limitation",
            name="诉讼时效剩余天数",
            description="从知道权利受损之日起 N 年",
            inputs=["start_date", "limit_years"],
            output_type="integer",
            output_unit="天",
            expression_or_callable=calc_sol_days_fn,
            regulation_ref="《民法典》第 188 条",
        )

      教学数学求解:
        FormulaDef(
            formula_id="math.quadratic_root",
            name="一元二次方程根",
            description="ax² + bx + c = 0 的解",
            inputs=["a", "b", "c"],
            output_type="list",
            output_unit="",
            expression_or_callable="...sympy solve...",
            regulation_ref="人教版必修一 3.4",
        )
    """
    formula_id: str                                  # 命名空间化 id, 如 "bridge.dr_calc"
    name: str                                        # 人类可读名
    description: str = ""                            # 详细说明
    inputs: List[str] = field(default_factory=list)  # 输入参数名
    output_type: str = "number"                      # number/integer/date/list/dict
    output_unit: str = ""                            # 分/天/kg/...

    # 执行体:可以是 Python 函数,也可以是 sympy 表达式字符串,也可以是 JSON 规则
    expression_or_callable: Union[Callable, str, Dict] = None

    # 权威依据(规范号/法条/教材章节)
    authority_ref: str = ""

    # 触发关键词(意图识别用)
    # 场景 1: ["指标1", "指标2"]
    # 场景 2: ["诉讼时效", "时效多久", "还能告"]
    trigger_keywords: List[str] = field(default_factory=list)

    # 是否严禁 LLM 自行计算(默认 True)
    forbid_llm_calc: bool = True


@dataclass
class UnitSpec:
    """单位换算规范"""
    category: str                                    # length/weight/time/currency
    canonical_unit: str                              # 本类别的规范单位("m", "kg", "day")
    conversions: Dict[str, float] = field(default_factory=dict)
                                                     # 其他单位 → canonical 的倍数
                                                     # 例:{"mm": 0.001, "cm": 0.01, "km": 1000}


@dataclass
class CalculationRequest:
    """
    LLM 发起的计算请求(符号计算触发点)

    工作流:
      LLM 遇到"算一下 Dr 值" → 不自己算 → 返回 CalculationRequest:
        CalculationRequest(formula_id="bridge.dr_calc", args={...})
      orchestrator 捕获 → 交给 Calculator 执行 → 把结果喂回 LLM
    """
    formula_id: str
    args: Dict[str, Any] = field(default_factory=dict)
    caller_context: str = ""                         # LLM 的自然语言问题原文


@dataclass
class CalculationResult:
    """符号计算的结果"""
    formula_id: str
    success: bool
    value: Any = None                                # 主结果
    unit: str = ""
    display: str = ""                                # 人类可读("剩余 245 天")
    steps: List[str] = field(default_factory=list)   # 计算步骤(解释用)
    error: str = ""                                  # 失败原因
    authority_ref: str = ""                          # 带出权威依据


class ComputationSpec(ABC):
    """
    计算规范抽象基类

    领域实现要做三件事:
      1. 注册所有本域的 FormulaDef
      2. 定义本域的 UnitSpec
      3. 告诉意图识别"哪些用户问题是计算意图"
    """

    @abstractmethod
    def list_formulas(self) -> List[FormulaDef]:
        """列出本领域支持的全部公式"""
        ...

    @abstractmethod
    def list_units(self) -> List[UnitSpec]:
        """列出本领域的单位换算规范"""
        ...

    def get_formula(self, formula_id: str) -> Optional[FormulaDef]:
        """按 id 查公式"""
        for f in self.list_formulas():
            if f.formula_id == formula_id:
                return f
        return None

    def detect_computation_intent(self, question: str) -> Optional[str]:
        """
        检测用户问题是不是"计算意图",如果是返回对应 formula_id。

        默认实现:遍历所有 formula 的 trigger_keywords 找匹配。
        领域可重写(用更 sophisticated 的 NLU)。
        """
        q_lower = question.lower()
        for f in self.list_formulas():
            for kw in f.trigger_keywords:
                if kw.lower() in q_lower:
                    return f.formula_id
        return None

    def is_llm_calc_forbidden(self, formula_id: str) -> bool:
        """
        LLM 是否禁止自行计算该项。
        默认 True(所有注册的都禁 LLM 自算)。
        """
        f = self.get_formula(formula_id)
        if f is None:
            return False  # 未注册的不属于本域,不管
        return f.forbid_llm_calc
