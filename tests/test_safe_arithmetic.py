"""Public-copy arithmetic hardening: numeric correctness and hostile syntax."""
from fractions import Fraction
import math
from types import SimpleNamespace

import pytest

from engineering_agent.safe_arithmetic import (
    SafeArithmeticError, evaluate_arithmetic, evaluate_formula, evaluate_condition,
    MAX_SOURCE_LENGTH, MAX_SEQUENCE_LENGTH,
)
from nskernel.computation.calculator.calculator import Calculator
from nskernel.contracts import FormulaDef


@pytest.mark.parametrize("expression, expected", [
    ("0.70*84.33 + 0.18*84.42 + 0.12*84.47", 84.363),
    ("(100-25)*0.7 + 100*0.3", 82.5),
    ("1.5 * .1", .15), ("80/100", .8), ("0.03*10", .3),
    ("0.1 + 0.2", .3), ("-2**2 + (+3)", -1),
    ("2**-3", .125), ("9**0.5", 3), ("1_000 / 4", 250),
    ("1e-3 * 3", .003), ("((3 + 5) / 2)", 4),
])
def test_bounded_real_engineering_arithmetic(expression, expected):
    assert evaluate_arithmetic(expression) == pytest.approx(expected)


def test_rational_output_mode_preserves_exact_decimal_arithmetic():
    assert evaluate_arithmetic("0.1 + 0.2", exact=True) == Fraction(3, 10)
    assert evaluate_arithmetic("1/3 + 2/3", exact=True) == Fraction(1)
    assert evaluate_arithmetic("0.70*84.33 + 0.18*84.42 + 0.12*84.47", exact=True) == Fraction(84363, 1000)
    assert evaluate_arithmetic("1e-300 / 1e100", exact=True) == Fraction(1, 10**400)


@pytest.mark.parametrize("expression", [
    "x+1", "pi", "sqrt(4)", "abs(-1)", "True+1", "None", "1j", "'3'",
    "[1,2]", "(1,2)", "{1:2}", "{1,2}", "1<2", "1 and 2", "1 or 2",
    "1//2", "3%2", "1<<2", "2^3", "~1", "1 if 1 else 2", "(x:=2)",
    "(lambda: 1)()", "[x for x in (1,2)]", "f'{1+2}'", "0xff",
    "().__class__.__bases__[0].__subclasses__()",
    "__import__('os').system('echo forbidden')", "open('/tmp/forbidden', 'w')",
    "1; import os", "1\n__import__('os')",
])
def test_strict_arithmetic_rejects_non_arithmetic_syntax(expression):
    with pytest.raises(SafeArithmeticError):
        evaluate_arithmetic(expression)


@pytest.mark.parametrize("expression", [
    "2**100000000", "2**(2**100)", "10**1000", "(10**100)**100",
    "1e99999999", "1e-99999999", "1e101", "1/0", "0**-1", "(-1)**0.5",
    "9"*129, "+"*80+"1", "+".join(["1"]*100), "1"*(MAX_SOURCE_LENGTH+1),
    "("*300+"1"+")"*300,
])
def test_invalid_or_resource_exhausting_arithmetic_is_rejected(expression):
    with pytest.raises(SafeArithmeticError):
        evaluate_arithmetic(expression)


def test_float_export_does_not_turn_nonzero_rational_into_zero():
    with pytest.raises(SafeArithmeticError):
        evaluate_arithmetic("1e-300 / 1e100")


@pytest.mark.parametrize("expression, bindings, expected", [
    ("sqrt(L**2+W**2)", {"L": 3, "W": 4}, 5),
    ("log(100,10)+log10(1000)", {}, 5),
    ("exp(0)+sin(0)+cos(0)+tan(0)", {}, 2),
    ("pi*r**2", {"r": 2}, math.pi*4),
    ("log(e)", {}, 1),
    ("round(score, 2)", {"score": 84.363}, 84.36),
    ("min(a,b)+max(a,b)", {"a": 2, "b": 8}, 10),
    ("sum(weights) / len(weights)", {"weights": [.7, .18, .12]}, 1/3),
    ("min(scores)+max(scores)", {"scores": [75, 85, 100]}, 175),
    ("sum(values, 2)", {"values": []}, 2),
    ("abs(-2)+floor(2.9)+ceil(2.1)+int(2.9)+float(2)", {}, 11),
    ("round(1234, -2)", {}, 1200),
    ("荷载 / 面积", {"荷载": 12, "面积": 3}, 4),
])
def test_formula_mode_preserves_explicit_numeric_math_features(expression, bindings, expected):
    assert evaluate_formula(expression, bindings) == pytest.approx(expected)


@pytest.mark.parametrize("expression", [
    "().__class__.__bases__[0].__subclasses__()", "x.__class__", "x[0]",
    "__import__('os')", "open('anything')", "globals()", "eval('1+1')",
    "sqrt.__globals__", "math.sqrt(4)", "sum([1,2])", "sum(x for x in values)",
    "max(*values)", "round(x, ndigits=2)", "f()", "(lambda: 3)()",
    "pow(2, 3)", "factorial(100000000)", "'x'*1000000000",
])
def test_formula_mode_rejects_object_traversal_and_arbitrary_calls(expression):
    with pytest.raises(SafeArithmeticError):
        evaluate_formula(expression, {"x": 2, "values": [1, 2]})


@pytest.mark.parametrize("bindings", [
    {"x": float('inf')}, {"x": float('nan')}, {"x": True}, {"x": "2"},
    {"x": 1j}, {"x": object()}, {"x": {"a": 1}}, {"x": [[1]]},
    {"x": [1]*(MAX_SEQUENCE_LENGTH+1)}, {"sqrt": 1}, {"pi": 3}, {"_x": 1},
    {"f": lambda: 1}, {"x": 2**3000}, {str(i): i for i in range(65)},
])
def test_invalid_or_dangerous_bindings_are_rejected(bindings):
    with pytest.raises(SafeArithmeticError):
        evaluate_formula("1+1", bindings)


def test_no_user_supplied_object_protocol_is_executed():
    calls = []
    class Hostile:
        def __float__(self):
            calls.append('float')
            raise RuntimeError('not allowed')
        def __str__(self):
            calls.append('str')
            raise RuntimeError('not allowed')
        def __add__(self, other):
            calls.append('add')
            raise RuntimeError('not allowed')
    with pytest.raises(SafeArithmeticError):
        evaluate_formula("x+1", {"x": Hostile()})
    with pytest.raises(SafeArithmeticError):
        evaluate_condition(Hostile(), ">= 0")
    assert not calls


def test_tampered_fraction_cannot_smuggle_arbitrary_objects():
    calls = []
    class Hostile:
        def bit_length(self):
            calls.append('bit_length')
            return 1
    damaged = Fraction(1, 2)
    damaged._numerator = Hostile()
    with pytest.raises(SafeArithmeticError):
        evaluate_formula("x+1", {"x": damaged})
    damaged_denominator = Fraction(1, 2)
    damaged_denominator._denominator = 0
    with pytest.raises(SafeArithmeticError):
        evaluate_formula("x+1", {"x": damaged_denominator})
    assert not calls


@pytest.mark.parametrize("expression", [
    "sqrt(-1)", "log(0)", "log(1,1)", "exp(1000)", "exp(-1000)",
    "round(1,10000000)", "round(1,0.5)", "sum(1)", "len(1)",
    "max(values)", "values", "sum(values, values)",
    "sin()", "sqrt(1,2)", "1e-300 * float(1e-100)",
])
def test_math_domain_limits_and_sequence_misuse_are_explicit_failures(expression):
    with pytest.raises(SafeArithmeticError):
        evaluate_formula(expression, {"values": []})


@pytest.mark.parametrize("value, condition, expected", [
    (90, ">=90", True), (85, ">=90", False), (85, ">=80", True),
    (1, "== 0.1+0.9", True), (2, "!=1", True), (3, "<4", True),
    (3, "<=3", True), (3, ">3", False), (-2, ">= -3", True),
])
def test_piecewise_single_comparison(value, condition, expected):
    assert evaluate_condition(value, condition) is expected


@pytest.mark.parametrize("condition", [
    ">=0 or __import__('os')", ">=0 and 1", ">=0 <10", "is None", "in [1,2]",
    ">= ().__class__", ">=abs(1)", ">=x", ">=2**100000", "", ">=float('nan')",
])
def test_piecewise_conditions_never_interpolate_or_execute_python(condition):
    with pytest.raises(SafeArithmeticError):
        evaluate_condition(85, condition)


def _calculator(expression, inputs=None):
    formula = FormulaDef("test", "synthetic", inputs=inputs or [], expression_or_callable=expression)
    return Calculator(SimpleNamespace(get_formula=lambda formula_id: formula))


def test_public_calculator_expression_route_uses_safe_interpreter():
    calc = _calculator("sqrt(L**2+W**2)", ["L", "W"])
    result = calc.compute("test", {"L": "3", "W": "4"})
    assert result.success
    assert result.value == 5


def test_public_calculator_rejects_malicious_expression_as_failed_result():
    calc = _calculator("().__class__.__bases__[0].__subclasses__()")
    result = calc.compute("test")
    assert result.success is False
    assert result.value is None
    assert "SafeArithmeticError" in result.error


def test_public_calculator_piecewise_and_lookup_are_compatible():
    rule = {"type": "piecewise", "on": "score", "cases": [
        {"when": ">=90", "then": "优"}, {"when": ">=80", "then": "良"}], "default": "待复核"}
    calc = _calculator(rule, ["score"])
    assert calc.compute("test", {"score": 85}).value == "良"
    assert calc.compute("test", {"score": 70}).value == "待复核"
    lookup = _calculator({"type": "lookup", "key": "kind", "table": {"A": 1}, "default": 0}, ["kind"])
    assert lookup.compute("test", {"kind": "A"}).value == 1


def test_invalid_piecewise_condition_does_not_silently_fall_through():
    calc = _calculator({"type": "piecewise", "on": "score", "cases": [
        {"when": ">=0", "then": "accepted"},
        {"when": ">=0 or __import__('os')", "then": "malicious"}], "default": "fallback"}, ["score"])
    result = calc.compute("test", {"score": 85})
    assert result.success is False
    assert result.value is None


def test_public_calculator_rejects_oversized_piecewise_rule():
    calc = _calculator({"type": "piecewise", "on": "score", "cases": [
        {"when": ">=90", "then": "high"}]*129, "default": "low"}, ["score"])
    assert calc.compute("test", {"score": 85}).success is False
