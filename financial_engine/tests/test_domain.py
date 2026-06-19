from decimal import Decimal
import pytest

from financial_engine.domain.value_objects import Money


class TestMoney:
    """Tests for the Money value object."""

    def test_money_creation(self):
        m = Money("100.50", "USD")
        assert m.amount == Decimal("100.5000")
        assert m.currency == "USD"

    def test_money_equality(self):
        assert Money("10", "USD") == Money("10.0000", "USD")

    def test_money_inequality_currency(self):
        assert Money("10", "USD") != Money("10", "EUR")

    def test_money_addition(self):
        result = Money("10", "USD") + Money("20", "USD")
        assert result == Money("30", "USD")

    def test_money_addition_currency_mismatch(self):
        with pytest.raises(ValueError, match="Currency mismatch"):
            Money("10", "USD") + Money("20", "EUR")

    def test_money_subtraction(self):
        result = Money("50", "USD") - Money("20", "USD")
        assert result == Money("30", "USD")

    def test_money_negation(self):
        m = -Money("50", "USD")
        assert m.amount == Decimal("-50.0000")

    def test_money_comparison(self):
        assert Money("10", "USD") < Money("20", "USD")
        assert Money("20", "USD") > Money("10", "USD")
        assert Money("10", "USD") <= Money("10", "USD")
        assert Money("10", "USD") >= Money("10", "USD")

    def test_money_rejects_float(self):
        with pytest.raises(TypeError, match="float is not allowed"):
            Money(10.5, "USD")

    def test_money_is_positive_negative_zero(self):
        assert Money("10", "USD").is_positive()
        assert Money("-10", "USD").is_negative()
        assert Money("0", "USD").is_zero()

    def test_money_to_dict(self):
        d = Money("42.50", "EUR").to_dict()
        assert d == {"amount": "42.5000", "currency": "EUR"}
