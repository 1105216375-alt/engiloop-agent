"""Bounded arithmetic AST interpreter added for the public-source release.

No Python expression is executed. The strict entry point accepts only numeric
literals and arithmetic. The formula entry point additionally permits explicit
numeric bindings and a closed set of mathematical functions. Rational arithmetic
is exact internally; transcendental operations are finite floating-point math.
"""
from __future__ import annotations

import ast
import math
import operator
import re
from fractions import Fraction
from typing import Any

MAX_SOURCE_LENGTH = 2048
MAX_AST_NODES = 256
MAX_AST_DEPTH = 32
MAX_LITERAL_DIGITS = 128
MAX_NUMBER_BITS = 2048
MAX_ABSOLUTE_VALUE = 10 ** 100
MAX_POWER = 100
MAX_BINDINGS = 64
MAX_SEQUENCE_LENGTH = 128
MAX_BOUND_VALUES = 512
MAX_CALL_ARGUMENTS = 32
MAX_ROUND_DIGITS = 12

_LITERAL = re.compile(r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE]([+-]?\d+))?\Z")
_FUNCTIONS = frozenset({
    "abs", "round", "min", "max", "sum", "len", "int", "float",
    "sqrt", "log", "log10", "exp", "sin", "cos", "tan", "floor", "ceil",
})
_CONSTANTS = {"pi": math.pi, "e": math.e}
_MATH = {name: getattr(math, name) for name in (
    "sqrt", "log", "log10", "exp", "sin", "cos", "tan", "floor", "ceil",
)}
_ALLOWED_NODES = {
    ast.Expression, ast.Constant, ast.BinOp, ast.UnaryOp,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.UAdd, ast.USub,
}


class SafeArithmeticError(ValueError):
    """Unsupported expression, invalid numeric input, or resource-limit breach."""


def _checked(value: Fraction | float) -> Fraction | float:
    if type(value) is Fraction:
        # Fraction normally guarantees this, but a hostile in-process caller
        # can mutate its private slots. Do not invoke methods on such payloads.
        if (type(value.numerator) is not int or type(value.denominator) is not int
                or value.denominator <= 0):
            raise SafeArithmeticError("Invalid rational-number representation")
        if (value.numerator.bit_length() > MAX_NUMBER_BITS
                or value.denominator.bit_length() > MAX_NUMBER_BITS):
            raise SafeArithmeticError("Numeric representation exceeds the bit limit")
        if abs(value.numerator) > MAX_ABSOLUTE_VALUE * value.denominator:
            raise SafeArithmeticError("Numeric magnitude exceeds the limit")
    elif type(value) is float:
        if not math.isfinite(value) or abs(value) > MAX_ABSOLUTE_VALUE:
            raise SafeArithmeticError("A finite, bounded real number is required")
    else:
        raise SafeArithmeticError("Only real scalar numbers are allowed")
    return value


def _number(value: Any) -> Fraction:
    # Exact type checks intentionally reject bool and objects with overloaded
    # numeric conversion/arithmetic methods, even if they subclass int/float.
    if type(value) is int:
        if value.bit_length() > MAX_NUMBER_BITS:
            raise SafeArithmeticError("Integer input exceeds the bit limit")
        result = Fraction(value)
    elif type(value) is float:
        _checked(value)
        result = Fraction(str(value))
    elif type(value) is Fraction:
        result = value
    else:
        raise SafeArithmeticError("Bindings must be plain finite numbers")
    return _checked(result)


def _scalar(value: Any) -> Fraction | float:
    if type(value) not in (Fraction, float):
        raise SafeArithmeticError("A scalar is required here; sequences are aggregate-only")
    return _checked(value)


def _float(value: Fraction | float) -> float:
    result = float(value)
    if not math.isfinite(result) or (value != 0 and result == 0):
        raise SafeArithmeticError("Float conversion overflowed or underflowed")
    return result


def _export(value: Any, *, exact: bool) -> int | float | Fraction:
    value = _scalar(value)
    if exact:
        return value
    if type(value) is Fraction and value.denominator == 1:
        return value.numerator
    return _float(value)


class _ArithmeticInterpreter:
    def __init__(self, *, formula: bool, variables: dict[str, Any] | None = None):
        self.formula = formula
        self.bindings: dict[str, Fraction | tuple[Fraction, ...]] = {}
        if variables is None:
            variables = {}
        if type(variables) is not dict or len(variables) > MAX_BINDINGS:
            raise SafeArithmeticError("Provide a bounded plain dictionary of bindings")
        value_count = 0
        for name, value in variables.items():
            if (type(name) is not str or not name.isidentifier() or name.startswith("_")
                    or len(name) > 64 or name in _FUNCTIONS or name in _CONSTANTS):
                raise SafeArithmeticError("Invalid or reserved binding name")
            if type(value) in (list, tuple):
                if len(value) > MAX_SEQUENCE_LENGTH:
                    raise SafeArithmeticError("Numeric sequence exceeds the length limit")
                value_count += len(value)
                if value_count > MAX_BOUND_VALUES:
                    raise SafeArithmeticError("Too many bound numeric values")
                self.bindings[name] = tuple(_number(item) for item in value)
            else:
                value_count += 1
                if value_count > MAX_BOUND_VALUES:
                    raise SafeArithmeticError("Too many bound numeric values")
                self.bindings[name] = _number(value)
        self.source = ""

    def run(self, expression: str) -> Fraction | float:
        if type(expression) is not str or not expression.strip():
            raise SafeArithmeticError("A non-empty expression string is required")
        if len(expression) > MAX_SOURCE_LENGTH:
            raise SafeArithmeticError("Expression exceeds the source-length limit")
        self.source = expression.strip()
        try:
            tree = ast.parse(self.source, mode="eval")
        except (SyntaxError, ValueError, RecursionError) as error:
            raise SafeArithmeticError("Invalid arithmetic syntax") from error
        allowed = _ALLOWED_NODES | ({ast.Name, ast.Load, ast.Call} if self.formula else set())
        stack = [(tree, 0)]
        nodes = 0
        while stack:
            node, depth = stack.pop()
            nodes += 1
            if nodes > MAX_AST_NODES or depth > MAX_AST_DEPTH:
                raise SafeArithmeticError("Expression exceeds AST size/depth limits")
            if type(node) not in allowed:
                raise SafeArithmeticError("Unsupported expression syntax")
            stack.extend((child, depth + 1) for child in ast.iter_child_nodes(node))
        try:
            return _scalar(self._visit(tree.body))
        except SafeArithmeticError:
            raise
        except (ArithmeticError, ValueError, TypeError) as error:
            raise SafeArithmeticError("Invalid or out-of-range numeric operation") from error

    def _literal(self, node: ast.Constant) -> Fraction:
        if type(node.value) not in (int, float):
            raise SafeArithmeticError("Only decimal real numeric literals are permitted")
        token = ast.get_source_segment(self.source, node) or ""
        token = token.replace("_", "")
        match = _LITERAL.fullmatch(token)
        if not match or sum(char.isdigit() for char in token) > MAX_LITERAL_DIGITS:
            raise SafeArithmeticError("Invalid or oversized numeric literal")
        if match.group(1) and abs(int(match.group(1))) > 300:
            raise SafeArithmeticError("Scientific-notation exponent exceeds the limit")
        return _checked(Fraction(token))

    def _visit(self, node: ast.AST) -> Any:
        if type(node) is ast.Constant:
            return self._literal(node)
        if type(node) is ast.Name:
            if node.id in self.bindings:
                return self.bindings[node.id]
            if node.id in _CONSTANTS:
                return _CONSTANTS[node.id]
            raise SafeArithmeticError("Unknown numeric binding")
        if type(node) is ast.UnaryOp:
            value = _scalar(self._visit(node.operand))
            return _checked(value if type(node.op) is ast.UAdd else -value)
        if type(node) is ast.BinOp:
            left = _scalar(self._visit(node.left))
            right = _scalar(self._visit(node.right))
            if type(node.op) is ast.Pow:
                return self._power(left, right)
            fn = {ast.Add: operator.add, ast.Sub: operator.sub,
                  ast.Mult: operator.mul, ast.Div: operator.truediv}[type(node.op)]
            value = fn(left, right)
            if (type(value) is float and value == 0 and left != 0
                    and right != 0 and type(node.op) in (ast.Mult, ast.Div)):
                raise SafeArithmeticError("Floating-point operation underflowed")
            return _checked(value)
        if type(node) is ast.Call:
            if (type(node.func) is not ast.Name or node.func.id not in _FUNCTIONS
                    or node.keywords or len(node.args) > MAX_CALL_ARGUMENTS):
                raise SafeArithmeticError("Only explicitly allowlisted numeric calls are permitted")
            values = [self._visit(argument) for argument in node.args]
            return _checked(self._call(node.func.id, values))
        raise SafeArithmeticError("Unsupported arithmetic node")

    def _power(self, base: Fraction | float, exponent: Fraction | float) -> Fraction | float:
        if abs(exponent) > MAX_POWER:
            raise SafeArithmeticError("Power exponent exceeds the limit")
        integral = (exponent.denominator == 1 if type(exponent) is Fraction
                    else exponent.is_integer())
        if integral and type(base) is Fraction:
            power = int(exponent)
            predicted_bits = max(base.numerator.bit_length(), base.denominator.bit_length()) * abs(power)
            if predicted_bits > MAX_NUMBER_BITS:
                raise SafeArithmeticError("Power result would exceed the representation limit")
            return _checked(base ** power)
        value = math.pow(_float(base), _float(exponent))
        if value == 0 and base != 0:
            raise SafeArithmeticError("Power operation underflowed")
        return _checked(value)

    @staticmethod
    def _arity(values: list[Any], minimum: int, maximum: int | None = None) -> None:
        if not minimum <= len(values) <= (minimum if maximum is None else maximum):
            raise SafeArithmeticError("Invalid numeric-function argument count")

    def _call(self, name: str, values: list[Any]) -> Fraction | float:
        if name in {"sum", "len"}:
            self._arity(values, 1, 2 if name == "sum" else 1)
            if type(values[0]) is not tuple:
                raise SafeArithmeticError("This function requires an explicit bound numeric sequence")
            if name == "len":
                return Fraction(len(values[0]))
            total = _scalar(values[1]) if len(values) == 2 else Fraction(0)
            for item in values[0]:
                total = _checked(total + item)
            return total
        if name in {"min", "max"}:
            self._arity(values, 1, MAX_CALL_ARGUMENTS)
            rows = list(values[0]) if len(values) == 1 and type(values[0]) is tuple else values
            if not rows:
                raise SafeArithmeticError("An empty sequence has no minimum or maximum")
            for value in rows:
                _scalar(value)
            return min(rows) if name == "min" else max(rows)
        if name == "round":
            self._arity(values, 1, 2)
            value = _scalar(values[0])
            digits = _scalar(values[1]) if len(values) == 2 else Fraction(0)
            if digits != int(digits) or abs(digits) > MAX_ROUND_DIGITS:
                raise SafeArithmeticError("Rounding precision is not an allowed small integer")
            result = round(value, int(digits))
            return Fraction(result) if type(result) is int else result
        self._arity(values, 1, 2 if name == "log" else 1)
        scalars = [_scalar(value) for value in values]
        if name == "abs":
            return abs(scalars[0])
        if name == "int":
            return Fraction(int(scalars[0]))
        if name == "float":
            return _float(scalars[0])
        result = _MATH[name](*(_float(value) for value in scalars))
        if name == "exp" and result == 0:
            raise SafeArithmeticError("Exponential operation underflowed")
        return Fraction(result) if type(result) is int else result


def evaluate_arithmetic(expression: str, *, exact: bool = False) -> int | float | Fraction:
    """Numbers, parentheses, + - * / ** and unary +/-; no names or calls.

    Decimal literals and rational operations are exact internally. ``exact=True``
    returns a Fraction when the expression stayed rational. Nonintegral powers
    use bounded finite real floating-point math, even in exact-output mode.
    """
    return _export(_ArithmeticInterpreter(formula=False).run(expression), exact=exact)


def evaluate_formula(expression: str, variables: dict[str, Any] | None = None,
                     *, exact: bool = False) -> int | float | Fraction:
    """Evaluate a registered numeric formula with explicit safe bindings.

    The function namespace is fixed; bindings cannot supply functions or objects.
    Numeric sequences are accepted only as bound inputs to aggregate functions.
    """
    return _export(_ArithmeticInterpreter(formula=True, variables=variables).run(expression), exact=exact)


def evaluate_condition(value: Any, condition: str) -> bool:
    """Evaluate ONE comparison against an arithmetic threshold, without eval.

    For example ``evaluate_condition(85, '>= 80')``. Chained comparisons, boolean
    expressions, names and calls are deliberately unsupported in thresholds.
    """
    left = _number(value)
    if type(condition) is not str or len(condition) > MAX_SOURCE_LENGTH:
        raise SafeArithmeticError("Invalid or oversized piecewise condition")
    match = re.fullmatch(r"\s*(<=|>=|==|!=|<|>)\s*(.+?)\s*", condition, re.DOTALL)
    if not match:
        raise SafeArithmeticError("A single explicit comparison is required")
    right = evaluate_arithmetic(match.group(2), exact=True)
    comparison = {"<=": operator.le, ">=": operator.ge, "==": operator.eq,
                  "!=": operator.ne, "<": operator.lt, ">": operator.gt}[match.group(1)]
    return bool(comparison(left, right))
