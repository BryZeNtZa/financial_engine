class DomainError(Exception):
    """Base exception for domain errors."""

    def __init__(self, message: str, code: str = "DOMAIN_ERROR"):
        super().__init__(message)
        self.message = message
        self.code = code


class InsufficientFundsError(DomainError):
    def __init__(self, account_id: str, available: str, requested: str):
        super().__init__(
            f"Insufficient funds in account {account_id}: "
            f"available={available}, requested={requested}",
            code="INSUFFICIENT_FUNDS",
        )


class AccountNotFoundError(DomainError):
    def __init__(self, account_id: str):
        super().__init__(
            f"Account not found: {account_id}",
            code="ACCOUNT_NOT_FOUND",
        )


class TransactionNotFoundError(DomainError):
    def __init__(self, transaction_id: str):
        super().__init__(
            f"Transaction not found: {transaction_id}",
            code="TRANSACTION_NOT_FOUND",
        )


class CurrencyMismatchError(DomainError):
    def __init__(self, expected: str, actual: str):
        super().__init__(
            f"Currency mismatch: expected {expected}, got {actual}",
            code="CURRENCY_MISMATCH",
        )


class InvalidTransactionStateError(DomainError):
    def __init__(self, transaction_id: str, current_state: str, target_state: str):
        super().__init__(
            f"Cannot transition transaction {transaction_id} "
            f"from {current_state} to {target_state}",
            code="INVALID_STATE_TRANSITION",
        )


class DuplicateAccountError(DomainError):
    def __init__(self, user_id: str, currency: str):
        super().__init__(
            f"Account already exists for user {user_id} with currency {currency}",
            code="DUPLICATE_ACCOUNT",
        )
