import json
import uuid
from decimal import Decimal

from financial_engine.extensions import db
from financial_engine.models.account import Account
from financial_engine.models.transaction import Transaction
from financial_engine.domain.aggregates import TransactionAggregate, AccountAggregate
from financial_engine.services.balance_service import BalanceService
from financial_engine.services.balance_cache import balance_cache
from financial_engine.services.payment_gateway import PaymentGateway
from financial_engine.providers.base import PaymentProviderError, PaymentRequest
from financial_engine.domain.value_objects import Money
from financial_engine.domain.events import (
    DomainEvent,
    event_bus,
    DEPOSIT_COMPLETED,
    DEPOSIT_INITIATED,
)
from financial_engine.domain.exceptions import (
    AccountNotFoundError,
    TransactionNotFoundError,
    InvalidTransactionStateError,
    DepositAmountMismatchError,
)


# The platform clearing account is a special internal account
CLEARING_ACCOUNT_CURRENCY = {}  # currency -> clearing account id (populated at runtime)


class DepositService:
    """Handles deposits from external payment providers."""

    @staticmethod
    def get_or_create_clearing_account(currency: str) -> Account:
        """Get or create the platform clearing account for a currency."""
        clearing = Account.query.filter_by(
            user_id="PLATFORM_CLEARING", currency=currency
        ).first()
        if not clearing:
            clearing = Account(
                user_id="PLATFORM_CLEARING",
                currency=currency,
            )
            db.session.add(clearing)
            db.session.flush()
        return clearing

    @staticmethod
    def initiate_deposit(
        account_id: str,
        amount: Decimal,
        provider: str = "stripe",
        correlation_id: str | None = None,
        payer: str | None = None,
    ) -> Transaction:
        """Create a pending deposit transaction.

        When the named provider is configured, the collection is requested
        from the provider (push prompt for MoMo, redirect URL for Orange) and
        the provider reference / payment URL are persisted on the transaction.
        Otherwise the deposit is created in simulation mode (confirmed later by
        a webhook carrying the transaction id directly).
        """
        account = db.session.get(Account, account_id)
        if not account:
            raise AccountNotFoundError(account_id)

        deposit_money = Money(amount, account.currency)
        if not deposit_money.is_positive():
            raise ValueError("Deposit amount must be positive")

        # Mobile-money providers collect from the customer's wallet and require
        # the payer's phone number to initiate the request-to-pay / web payment.
        if PaymentGateway.requires_payer(provider) and not payer:
            raise ValueError(
                f"payer (MSISDN) is required for mobile-money provider '{provider}'"
            )

        corr_id = correlation_id or str(uuid.uuid4())

        # The initiated amount is persisted so the confirming webhook can be
        # validated against it (the webhook must not be able to alter it).
        meta = {
            "provider": provider,
            "account_id": account_id,
            "amount": str(deposit_money.amount),
            "currency": deposit_money.currency,
            "payer": payer,
        }

        agg = TransactionAggregate.open(
            type="DEPOSIT", correlation_id=corr_id, status="PENDING"
        )
        txn = agg.transaction

        client = PaymentGateway.get_client(provider)
        if client is not None:
            try:
                result = client.initiate_payment(
                    PaymentRequest(
                        amount=deposit_money.amount,
                        currency=deposit_money.currency,
                        customer_reference=payer or "",
                        transaction_reference=txn.id,
                        description="Wallet deposit",
                    )
                )
            except PaymentProviderError:
                db.session.rollback()
                raise

            txn.provider_reference = result.provider_reference
            meta["provider_reference"] = result.provider_reference
            meta["payment_url"] = result.payment_url
            notif_token = (result.raw or {}).get("notif_token")
            if notif_token:
                meta["notif_token"] = notif_token

        txn.metadata_json = json.dumps(meta)
        db.session.commit()

        event_bus.publish(
            DomainEvent(
                DEPOSIT_INITIATED,
                {
                    "transaction_id": txn.id,
                    "account_id": account_id,
                    "amount": str(deposit_money.amount),
                    "currency": deposit_money.currency,
                    "provider": provider,
                },
                correlation_id=corr_id,
            )
        )

        return txn

    @staticmethod
    def confirm_deposit(
        transaction_id: str,
        amount: Decimal,
    ) -> Transaction:
        """Confirm a deposit (called when a webhook confirms payment).

        The transaction row is locked and its status re-checked under the lock,
        so two concurrent/duplicate webhook deliveries can never both credit the
        account. The confirmed amount is validated against the amount persisted
        at initiation, so a webhook cannot inflate the deposit.
        """
        # Lock the transaction row first, then re-check status under the lock.
        txn = (
            db.session.query(Transaction)
            .filter_by(id=transaction_id)
            .with_for_update()
            .first()
        )
        if not txn:
            raise TransactionNotFoundError(transaction_id)

        if txn.status != "PENDING":
            raise InvalidTransactionStateError(transaction_id, txn.status, "SUCCESS")

        meta = json.loads(txn.metadata_json) if txn.metadata_json else {}
        account_id = meta.get("account_id")

        # Validate the confirmed amount against what was initiated.
        initiated = meta.get("amount")
        if initiated is not None and Decimal(str(amount)) != Decimal(str(initiated)):
            raise DepositAmountMismatchError(
                transaction_id, str(initiated), str(amount)
            )

        account_row = db.session.query(Account).filter_by(id=account_id).with_for_update().first()
        if not account_row:
            raise AccountNotFoundError(account_id)
        account = AccountAggregate(account_row)

        clearing = DepositService.get_or_create_clearing_account(account.currency)
        deposit_money = Money(amount, account.currency)

        # Balanced ledger entries: Platform Clearing -X / User +X.
        agg = TransactionAggregate.load(txn)
        agg.record_double_entry(
            debit_account_id=clearing.id,
            credit_account_id=account_id,
            money=deposit_money,
        )
        agg.mark_success()
        agg.assert_balanced()
        account.touch()

        BalanceService.maybe_create_snapshot(account_id)

        db.session.commit()

        # Settled balances changed — evict cached values.
        balance_cache.invalidate_many(account_id, clearing.id)

        event_bus.publish(
            DomainEvent(
                DEPOSIT_COMPLETED,
                {
                    "transaction_id": txn.id,
                    "account_id": account_id,
                    "amount": str(deposit_money.amount),
                    "currency": deposit_money.currency,
                    "payer": meta.get("payer"),
                },
                correlation_id=txn.correlation_id,
            )
        )

        return txn

    @staticmethod
    def fail_deposit(transaction_id: str) -> Transaction:
        """Mark a pending deposit as FAILED (provider reported failure/expiry)."""
        txn = (
            db.session.query(Transaction)
            .filter_by(id=transaction_id)
            .with_for_update()
            .first()
        )
        if not txn:
            raise TransactionNotFoundError(transaction_id)

        if txn.status != "PENDING":
            raise InvalidTransactionStateError(transaction_id, txn.status, "FAILED")

        txn.status = "FAILED"
        db.session.commit()
        return txn
