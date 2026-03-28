"""Unit tests for the safe math parser used by indicator formulas."""

from __future__ import annotations

import pytest

from app.indicators.calculator import CalculationError, parse_math_expression


def test_parse_basic_arithmetic() -> None:
    assert parse_math_expression("2 + 3") == 5.0
    assert parse_math_expression("10 - 4") == 6.0
    assert parse_math_expression("3 * 7") == 21.0
    assert parse_math_expression("20 / 4") == 5.0


def test_parse_operator_precedence() -> None:
    assert parse_math_expression("2 + 3 * 4") == 14.0
    assert parse_math_expression("10 - 6 / 2") == 7.0


def test_parse_parentheses() -> None:
    assert parse_math_expression("(2 + 3) * 4") == 20.0
    assert parse_math_expression("10 / (2 + 3)") == 2.0
    assert parse_math_expression("((1 + 2) * (3 + 4))") == 21.0


def test_parse_unary_minus() -> None:
    assert parse_math_expression("-5") == -5.0
    assert parse_math_expression("-5 + 3") == -2.0
    assert parse_math_expression("3 * -2") == -6.0


def test_parse_floats() -> None:
    assert parse_math_expression("1.5 + 2.5") == 4.0
    assert parse_math_expression("0.1 * 10") == pytest.approx(1.0)


def test_division_by_zero_raises() -> None:
    with pytest.raises(CalculationError):
        parse_math_expression("10 / 0")


@pytest.mark.parametrize("expression", ["abc", "2 + + 3", ""])
def test_invalid_input_raises(expression: str) -> None:
    with pytest.raises(CalculationError):
        parse_math_expression(expression)


def test_parse_realistic_indicator_formulas() -> None:
    assert parse_math_expression("150.0 / 200.0 * 100") == 75.0
    assert parse_math_expression("50.0 + 30.0 + 20.0") == 100.0
    assert parse_math_expression("(45.0 + 12.0) / 200.0 * 100") == pytest.approx(28.5)
