import uuid
from decimal import Decimal

from financial_engine.extensions import db
from financial_engine.models.account import Account
from financial_engine.models.transaction import Transaction
from financial_engine.models.ledger_entry import LedgerEntry
from financial_engine.services.balance_service import BalanceService
from financial_engine.services.fx_rate_provider import fx_rate_provider
from financial_engine.domain.value_objects import Money
from financial_engine.domain.exceptions import (
    AccountNotFoundError,
    InsufficientFundsError,
)
from financial_engine.domain.events import DomainEvent, event_bus, TRANSFER_COMPLETED


class FXService:
    """Foreign exchange service backed by a third-party rate API."""

    @classmethod
    def get_rate(cls, from_currency: str, to_currency: str) -> Decimal:
        return fx_rate_provider.get_rate(from_currency, to_currency)

    @classmethod
    def convert(cls, amount: Decimal, from_currency: str, to_currency: str) -> Decimal:
        rate = cls.get_rate(from_currency, to_currency)
        return (amount * rate).quantize(Decimal("0.0001"))

    @classmethod
    def convert_money(cls, money: Money, to_currency: str) -> Money:
        """Convert a Money value object to another currency."""
        rate = cls.get_rate(money.currency, to_currency)
        converted_amount = (money.amount * rate).quantize(Decimal("0.0001"))
        return Money(converted_amount, to_currency)

    @classmethod
    def get_or_create_fx_pool(cls, currency: str) -> Account:
        pool = Account.query.filter_by(user_id="FX_POOL", currency=currency).first()
        if not pool:
            pool = Account(user_id="FX_POOL", currency=currency)
            db.session.add(pool)
            db.session.flush()
        return pool

    @classmethod
    def execute_fx_transfer(
        cls,
        sender_account_id: str,
        receiver_account_id: str,
        amount: Decimal,
        correlation_id: str | None = None,
    ) -> Transaction:
        """Cross-currency transfer through FX pool. Ledger still balances to zero."""
        sender = db.session.query(Account).filter_by(id=sender_account_id).with_for_update().first()
        if not sender:
            raise AccountNotFoundError(sender_account_id)

        receiver = db.session.get(Account, receiver_account_id)
        if not receiver:
            raise AccountNotFoundError(receiver_account_id)

        if sender.currency == receiver.currency:
            raise ValueError("Use regular transfer for same-currency transfers")

        send_money = Money(amount, sender.currency)
        if not send_money.is_positive():
            raise ValueError("Transfer amount must be positive")

        available = BalanceService.get_available_balance(sender_account_id)
        if available < send_money:
            raise InsufficientFundsError(
                sender_account_id, str(available.amount), str(send_money.amount)
            )

        receive_money = cls.convert_money(send_money, receiver.currency)

        from_pool = cls.get_or_create_fx_pool(sender.currency)
        to_pool = cls.get_or_create_fx_pool(receiver.currency)

        corr_id = correlation_id or str(uuid.uuid4())

        txn = Transaction(
            type="FX_TRANSFER",
            status="SUCCESS",
            correlation_id=corr_id,
        )
        db.session.add(txn)
        db.session.flush()

        # Sender debited in source currency
        e1 = LedgerEntry(
            account_id=sender_account_id,
            transaction_id=txn.id,
            amount=-send_money.amount,
            entry_type="DEBIT",
            status="SUCCESS",
            currency=send_money.currency,
        )
        # FX pool credited in source currency
        e2 = LedgerEntry(
            account_id=from_pool.id,
            transaction_id=txn.id,
            amount=send_money.amount,
            entry_type="CREDIT",
            status="SUCCESS",
            currency=send_money.currency,
        )
        # FX pool debited in target currency
        e3 = LedgerEntry(
            account_id=to_pool.id,
            transaction_id=txn.id,
            amount=-receive_money.amount,
            entry_type="DEBIT",
            status="SUCCESS",
            currency=receive_money.currency,
        )
        # Receiver credited in target currency
        e4 = LedgerEntry(
            account_id=receiver_account_id,
            transaction_id=txn.id,
            amount=receive_money.amount,
            entry_type="CREDIT",
            status="SUCCESS",
            currency=receive_money.currency,
        )

        db.session.add_all([e1, e2, e3, e4])

        sender.version += 1
        receiver.version += 1

        BalanceService.maybe_create_snapshot(sender_account_id)
        BalanceService.maybe_create_snapshot(receiver_account_id)

        db.session.commit()

        event_bus.publish(
            DomainEvent(
                TRANSFER_COMPLETED,
                {
                    "transaction_id": txn.id,
                    "sender_account_id": sender_account_id,
                    "receiver_account_id": receiver_account_id,
                    "amount": str(send_money.amount),
                    "currency": send_money.currency,
                    "converted_amount": str(receive_money.amount),
                    "target_currency": receive_money.currency,
                },
                correlation_id=corr_id,
            )
        )

        return txn
