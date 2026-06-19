from decimal import Decimal, ROUND_HALF_UP


class Money:
    """
    Value Object representing a monetary amount with currency.
    Uses Decimal to avoid floating-point precision issues.
    """

    def __init__(self, amount: Decimal | int | str, currency: str):
        if isinstance(amount, float):
            raise TypeError("float is not allowed for Money; use Decimal, int, or str")
        self.amount = Decimal(str(amount)).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
        self.currency = currency.upper()

    def __eq__(self, other):
        if not isinstance(other, Money):
            return False
        return self.amount == other.amount and self.currency == other.currency

    def __repr__(self):
        return f"Money({self.amount}, '{self.currency}')"

    def __add__(self, other):
        if not isinstance(other, Money):
            raise TypeError(f"Cannot add Money and {type(other)}")
        if self.currency != other.currency:
            raise ValueError(f"Currency mismatch: {self.currency} vs {other.currency}")
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other):
        if not isinstance(other, Money):
            raise TypeError(f"Cannot subtract Money and {type(other)}")
        if self.currency != other.currency:
            raise ValueError(f"Currency mismatch: {self.currency} vs {other.currency}")
        return Money(self.amount - other.amount, self.currency)

    def __neg__(self):
        return Money(-self.amount, self.currency)

    def __lt__(self, other):
        if not isinstance(other, Money):
            raise TypeError(f"Cannot compare Money and {type(other)}")
        if self.currency != other.currency:
            raise ValueError(f"Currency mismatch: {self.currency} vs {other.currency}")
        return self.amount < other.amount

    def __le__(self, other):
        return self == other or self < other

    def __gt__(self, other):
        return not self <= other

    def __ge__(self, other):
        return not self < other

    def is_negative(self) -> bool:
        return self.amount < 0

    def is_zero(self) -> bool:
        return self.amount == 0

    def is_positive(self) -> bool:
        return self.amount > 0

    def to_dict(self) -> dict:
        return {"amount": str(self.amount), "currency": self.currency}
