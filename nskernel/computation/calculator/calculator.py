"""
Calculator · 符号计算引擎 ⭐ 核心创新
=====================================

解决 LLM 在数值计算上的致命问题:

  场景 2: 诉讼时效算错一天 → 败诉
  场景 4: 剂量算错 10% → 可能死人
  场景 5: 复利算错一位 → 客户损失
  场景 3: 数学题算错 → 误导学生
  场景 N: Dr/PCI/PQI 算错 → 评级错误

nskernel 的铁律:
  ★ 所有领域里有确定答案的数值计算,必须走 Calculator,
    LLM 只能用自然语言描述过程,绝不能自己给出计算结果。

工作流:
  1. 意图识别发现"计算问题" (ComputationSpec.detect_computation_intent)
  2. LLM 不直接回答,而是返回 CalculationRequest
  3. Calculator 按 formula_id 执行符号计算
  4. CalculationResult 作为硬数据喂回 LLM,LLM 只负责包装成自然语言

职责:
  - 执行已注册的 FormulaDef
  - 支持 4 种公式实现形式:
     * Python callable (自由度最高)
     * 受限数值 AST 表达式字符串（公开版加固，不执行 Python 表达式）
     * JSON 规则 (分段函数/查表)
     * lambda + 常量字典 (简单直接)
  - 对 LLM"自算的数值"做审计:如果检测到 LLM 输出了应由 Calculator
    算的数字,而 Calculator 没被调用,打警告(或接管重算)
"""
from __future__ import annotations
from typing import Any, Callable, Dict, List, Optional, Union, TYPE_CHECKING

if TYPE_CHECKING:
    from nskernel.contracts import (
        ComputationSpec, FormulaDef,
        CalculationRequest, CalculationResult,
    )


class CalculationError(Exception):
    """计算执行失败"""


class Calculator:
    """
    通用符号计算器

    构造:
      calc = Calculator(computation_spec)

    执行:
      result = calc.compute(
          formula_id="law.statute_of_limitation",
          args={"start_date": "2022-01-15", "limit_years": 3},
      )
      # result.value = 245
      # result.display = "诉讼时效剩余 245 天"
      # result.authority_ref = "《民法典》第 188 条"
    """

    def __init__(self, computation_spec: "ComputationSpec"):
        self._spec = computation_spec

    # -----------------------------------------------------------------
    # 核心入口
    # -----------------------------------------------------------------
    def compute(
        self,
        formula_id: str,
        args: Optional[Dict[str, Any]] = None,
    ) -> "CalculationResult":
        """
        按 formula_id 执行计算

        Args:
          formula_id: 已注册的公式 ID
          args: 输入参数字典
        Returns:
          CalculationResult(success / value / unit / display / steps / error)

        v0.2 加强:前置参数清洗
          - None 参数 → 明确错误
          - 字符串数字 → 自动转换
          - 明显非法日期 → 友好提示
        """
        from nskernel.contracts import CalculationResult

        formula = self._spec.get_formula(formula_id)
        if formula is None:
            return CalculationResult(
                formula_id=formula_id,
                success=False,
                error=f"未注册的公式 ID: {formula_id}",
            )

        args = dict(args or {})  # 复制避免污染调用方

        # ─── v0.2:前置参数校验 ───
        # 1) None 参数直接报错
        none_params = [k for k, v in args.items() if v is None]
        if none_params:
            return CalculationResult(
                formula_id=formula_id,
                success=False,
                error=f"参数 {none_params} 为 None,请提供有效值",
                authority_ref=formula.authority_ref,
            )

        # 2) 字符串→数字自动转换(容错)
        for k, v in list(args.items()):
            if isinstance(v, str) and v.strip():
                # 试着当数字
                try:
                    if "." in v or "e" in v.lower():
                        args[k] = float(v)
                    elif v.lstrip("-").isdigit():
                        args[k] = int(v)
                    # 其他字符串保持原样(可能是日期/类别)
                except (ValueError, AttributeError):
                    pass

        # 3) 校验必填参数(已存在)
        missing = [k for k in formula.inputs if k not in args]
        if missing:
            return CalculationResult(
                formula_id=formula_id,
                success=False,
                error=f"缺少参数: {missing}",
                authority_ref=formula.authority_ref,
            )

        # 执行
        try:
            raw_output = self._execute(formula, args)
        except (ValueError, TypeError, ZeroDivisionError, OverflowError) as e:
            # v0.2:这些业务异常友好处理
            return CalculationResult(
                formula_id=formula_id,
                success=False,
                error=f"参数错误({type(e).__name__}): {e}",
                authority_ref=formula.authority_ref,
            )
        except Exception as e:
            return CalculationResult(
                formula_id=formula_id,
                success=False,
                error=f"{type(e).__name__}: {e}",
                authority_ref=formula.authority_ref,
            )

        # 规范化输出
        if isinstance(raw_output, dict):
            # callable 返回详细 dict: {value, display, steps, ...}
            value = raw_output.get("value")
            display = raw_output.get("display", "")
            steps = raw_output.get("steps", [])
        else:
            value = raw_output
            display = self._format_display(value, formula)
            steps = []

        return CalculationResult(
            formula_id=formula_id,
            success=True,
            value=value,
            unit=formula.output_unit,
            display=display,
            steps=steps,
            authority_ref=formula.authority_ref,
        )

    def compute_request(
        self,
        request: "CalculationRequest",
    ) -> "CalculationResult":
        """按 CalculationRequest 对象执行(便于 LLM 端构造请求)"""
        return self.compute(request.formula_id, request.args)

    # -----------------------------------------------------------------
    # 意图路由:从用户问题决定走哪个公式
    # -----------------------------------------------------------------
    def try_auto_compute(
        self,
        question: str,
        extracted_args: Optional[Dict[str, Any]] = None,
    ) -> Optional["CalculationResult"]:
        """
        尝试从问题自动路由到某个公式并计算
        返回 None 表示问题不是计算意图
        """
        formula_id = self._spec.detect_computation_intent(question)
        if formula_id is None:
            return None
        return self.compute(formula_id, extracted_args or {})

    # -----------------------------------------------------------------
    # 内部:执行 4 种公式形式
    # -----------------------------------------------------------------
    def _execute(self, formula: "FormulaDef", args: Dict[str, Any]) -> Any:
        """分发执行"""
        impl = formula.expression_or_callable

        if callable(impl):
            # 形式 1: Python callable
            return impl(**args)

        if isinstance(impl, str):
            # 公开版加固：限定数值 AST，不执行 Python 表达式。
            return self._eval_expression(impl, args)

        if isinstance(impl, dict):
            # 形式 3: JSON 规则(简易版 - 分段函数示例)
            return self._eval_json_rule(impl, args)

        raise CalculationError(
            f"不支持的公式实现类型: {type(impl).__name__}"
        )

    def _eval_expression(self, expr: str, args: Dict[str, Any]) -> Any:
        """
        公开版安全修复：AST 数值解释器替代历史受限 globals eval。
        变量只接收普通数值/有限数值序列；函数为固定数学白名单。
        不支持属性访问、下标、任意调用、推导式或字符串计算。
        """
        from engineering_agent.safe_arithmetic import evaluate_formula
        return evaluate_formula(expr, args)

    def _eval_json_rule(self, rule: Dict, args: Dict[str, Any]) -> Any:
        """
        JSON 规则求值

        规则格式(示例):
          {
            "type": "piecewise",              # 分段函数
            "on": "score",                    # 基于哪个参数分段
            "cases": [
              {"when": ">= 90", "then": "优"},
              {"when": ">= 80", "then": "良"},
              {"when": ">= 60", "then": "中"},
            ],
            "default": "差"
          }
        """
        rule_type = rule.get("type")
        if rule_type == "piecewise":
            from engineering_agent.safe_arithmetic import evaluate_condition, SafeArithmeticError
            on_var = rule["on"]
            val = args.get(on_var)
            cases = rule.get("cases", [])
            if type(cases) not in (list, tuple) or len(cases) > 128:
                raise SafeArithmeticError("分段规则必须是最多 128 项的显式列表")
            # 先检查所有条件；非法条件不再被悄悄跳过后落入 default。
            evaluated = []
            for case in cases:
                if type(case) is not dict or "when" not in case or "then" not in case:
                    raise SafeArithmeticError("分段规则缺少 when/then")
                evaluated.append((evaluate_condition(val, case["when"]), case["then"]))
            for matched, output in evaluated:
                if matched:
                    return output
            return rule.get("default")

        if rule_type == "lookup":
            # 查表型:{type:'lookup', key:'category', table:{'A':1, 'B':2}}
            key_var = rule["key"]
            val = args.get(key_var)
            return rule["table"].get(val, rule.get("default"))

        raise CalculationError(f"不支持的 JSON 规则类型: {rule_type}")

    # -----------------------------------------------------------------
    # 输出格式化
    # -----------------------------------------------------------------
    def _format_display(self, value: Any, formula: "FormulaDef") -> str:
        """
        把值格式化成人类可读
        例: 245 + "天" → "245 天"
        """
        unit = formula.output_unit
        if unit:
            return f"{value} {unit}"
        return str(value)


# ══════════════════════════════════════════════════════════════
# LLMComputationDispatcher - LLM端的计算调用器
# ══════════════════════════════════════════════════════════════
class LLMComputationDispatcher:
    """
    LLM 侧的计算调用辅助器

    在 prompt 里引导 LLM:
      - 遇到"计算问题",不要自己算,输出 <calc formula_id="..." args="..."/> 标签
      - 系统拦截这个标签,走 Calculator,把结果喂回 LLM

    这个类负责生成 prompt 片段 + 解析 LLM 输出里的计算标签。
    """

    def build_prompt_injection(
        self,
        computation_spec: "ComputationSpec",
    ) -> str:
        """
        生成要注入到 sys_prompt 里的"计算规则"提示文字
        让 LLM 知道本域有哪些可用公式以及如何调用
        """
        formulas = computation_spec.list_formulas()
        if not formulas:
            return ""

        lines = [
            "═══════ 可用符号计算公式 ═══════",
            "当遇到下列计算问题时,你**绝对不得自己算**,必须返回计算请求让符号引擎执行:",
            "",
        ]
        for f in formulas:
            trigger_str = "、".join(f.trigger_keywords) if f.trigger_keywords else "(无触发词)"
            lines.append(f"  · {f.formula_id}: {f.name}")
            lines.append(f"    触发: {trigger_str}")
            lines.append(f"    输入: {f.inputs}")
            if f.authority_ref:
                lines.append(f"    依据: {f.authority_ref}")
            lines.append("")

        lines.extend([
            "调用格式(返回在回复中):",
            '  <calc formula_id="xxx" args=\'{"k1":v1,"k2":v2}\'/>',
            "",
            "系统会拦截此标签,执行符号计算,把结果代入你的回复。",
            "你绝不能自己算数字 — 一个小数点的错误就是专业事故。",
        ])
        return "\n".join(lines)

    def extract_calc_requests(
        self, llm_output: str,
    ) -> List["CalculationRequest"]:
        """
        从 LLM 输出里解析所有 <calc.../> 标签
        返回 CalculationRequest 列表
        """
        import re
        import json
        from nskernel.contracts import CalculationRequest

        requests = []
        # <calc formula_id="..." args='{...}'/>
        pattern = re.compile(
            r'<calc\s+formula_id="([^"]+)"\s+args=\'({[^\']*})\'\s*/?>'
        )
        for m in pattern.finditer(llm_output):
            fid = m.group(1)
            try:
                args = json.loads(m.group(2))
            except json.JSONDecodeError:
                args = {}
            requests.append(CalculationRequest(
                formula_id=fid, args=args, caller_context=m.group(0),
            ))
        return requests
