"""Orange Money — Web Payment client.

API flow (https://api.orange.com, see developer.orange.com/apis/om-webpay):
  1. OAuth token:  POST /oauth/v3/token              (Basic authorization header)
  2. Web payment:  POST /orange-money-webpay/{env}/v1/webpayment
                   -> { pay_token, payment_url, notif_token }
  3. Redirect:     customer is sent to payment_url to authorize.
  4. Status:       POST /orange-money-webpay/{env}/v1/transactionstatus
                   body { order_id, amount, pay_token }
  5. Notif:        Orange POSTs the final status to the configured notif_url,
                   echoing the per-payment notif_token (used to verify it).
"""

from __future__ import annotations

import logging
import time
from decimal import Decimal

import requests

from financial_engine.providers.base import (
    PaymentProviderClientInterface,
    PaymentProviderError,
    PaymentRequest,
    PaymentResult,
    PaymentStatus,
    WebhookEvent,
    to_decimal,
)

logger = logging.getLogger(__name__)

# Orange Money status -> normalized status
_STATUS_MAP = {
    "INITIATED": PaymentStatus.PENDING,
    "PENDING": PaymentStatus.PENDING,
    "SUCCESS": PaymentStatus.SUCCESSFUL,
    "SUCCESSFUL": PaymentStatus.SUCCESSFUL,
    "FAILED": PaymentStatus.FAILED,
    "EXPIRED": PaymentStatus.EXPIRED,
    "CANCELLED": PaymentStatus.CANCELLED,
}

_DEFAULT_BASE_URL = "https://api.orange.com"


def _safe_json(resp):
    try:
        return resp.json()
    except ValueError:
        return {"body": getattr(resp, "text", "")}


def _serialize_amount(amount: Decimal):
    """Orange expects a numeric amount; send an int when there's no minor part."""
    if amount == amount.to_integral_value():
        return int(amount)
    return float(amount)


class OmClient(PaymentProviderClientInterface):
    """Client for Orange Money Web Payment."""

    name = "orange_money"

    def __init__(
        self,
        *,
        authorization_header: str,   # "Basic <base64(client_id:client_secret)>"
        merchant_key: str,
        environment: str = "dev",
        base_url: str = _DEFAULT_BASE_URL,
        currency: str = "XOF",
        return_url: str | None = None,
        cancel_url: str | None = None,
        notif_url: str | None = None,
        lang: str = "fr",
        session: requests.Session | None = None,
        timeout: int = 15,
    ):
        self._authorization_header = authorization_header
        self._merchant_key = merchant_key
        self._environment = environment
        self._base_url = base_url.rstrip("/")
        self._currency = currency
        self._return_url = return_url
        self._cancel_url = cancel_url
        self._notif_url = notif_url
        self._lang = lang
        self._session = session or requests.Session()
        self._timeout = timeout
        self._token: str | None = None
        self._token_expiry: float = 0.0

    @classmethod
    def from_config(cls, config, **overrides) -> "OmClient":
        """Build a client from a Flask/dict config mapping."""
        params = dict(
            authorization_header=config.get("OM_AUTHORIZATION_HEADER"),
            merchant_key=config.get("OM_MERCHANT_KEY"),
            environment=config.get("OM_ENVIRONMENT", "dev"),
            base_url=config.get("OM_BASE_URL", _DEFAULT_BASE_URL),
            currency=config.get("OM_CURRENCY", "XOF"),
            return_url=config.get("OM_RETURN_URL"),
            cancel_url=config.get("OM_CANCEL_URL"),
            notif_url=config.get("OM_NOTIF_URL"),
            lang=config.get("OM_LANG", "fr"),
        )
        params.update(overrides)
        return cls(**params)

    def _webpay_url(self, suffix: str) -> str:
        return f"{self._base_url}/orange-money-webpay/{self._environment}/v1/{suffix}"

    # ------------------------------------------------------------------ auth
    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expiry - 60:
            return self._token

        url = f"{self._base_url}/oauth/v3/token"
        try:
            resp = self._session.post(
                url,
                headers={
                    "Authorization": self._authorization_header,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                data={"grant_type": "client_credentials"},
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise PaymentProviderError(
                f"Orange Money token request failed: {exc}", provider=self.name
            ) from exc

        if resp.status_code != 200:
            raise PaymentProviderError(
                "Orange Money token request rejected",
                provider=self.name,
                status_code=resp.status_code,
                raw=_safe_json(resp),
            )

        data = resp.json()
        self._token = data["access_token"]
        self._token_expiry = time.time() + int(data.get("expires_in", 3600))
        return self._token

    # -------------------------------------------------------------- payments
    def initiate_payment(self, request: PaymentRequest) -> PaymentResult:
        token = self._get_token()
        body = {
            "merchant_key": self._merchant_key,
            "currency": request.currency or self._currency,
            "order_id": request.transaction_reference,
            "amount": _serialize_amount(request.amount),
            "return_url": self._return_url,
            "cancel_url": self._cancel_url,
            "notif_url": self._notif_url,
            "lang": self._lang,
            "reference": request.description or request.transaction_reference,
        }

        url = self._webpay_url("webpayment")
        try:
            resp = self._session.post(
                url,
                json=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise PaymentProviderError(
                f"Orange Money webpayment failed: {exc}", provider=self.name
            ) from exc

        if resp.status_code not in (200, 201):
            raise PaymentProviderError(
                "Orange Money webpayment rejected",
                provider=self.name,
                status_code=resp.status_code,
                raw=_safe_json(resp),
            )

        data = resp.json()
        # notif_token is returned per payment and must be persisted by the
        # caller to later verify the asynchronous notification.
        return PaymentResult(
            provider=self.name,
            provider_reference=data.get("pay_token", request.transaction_reference),
            status=PaymentStatus.PENDING,
            payment_url=data.get("payment_url"),
            raw=data,
        )

    def get_payment_status(
        self, provider_reference: str, context: dict | None = None
    ) -> PaymentStatus:
        # Orange's transactionstatus lookup needs order_id and amount in
        # addition to the pay_token (provider_reference).
        context = context or {}
        token = self._get_token()
        body = {
            "order_id": context.get("order_id"),
            "amount": _serialize_amount(Decimal(str(context["amount"])))
            if context.get("amount") is not None
            else None,
            "pay_token": provider_reference,
        }

        url = self._webpay_url("transactionstatus")
        try:
            resp = self._session.post(
                url,
                json=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise PaymentProviderError(
                f"Orange Money status request failed: {exc}", provider=self.name
            ) from exc

        if resp.status_code != 200:
            raise PaymentProviderError(
                "Orange Money status request rejected",
                provider=self.name,
                status_code=resp.status_code,
                raw=_safe_json(resp),
            )

        status = str(resp.json().get("status", "")).upper()
        return _STATUS_MAP.get(status, PaymentStatus.PENDING)

    # --------------------------------------------------------------- webhook
    def verify_webhook(
        self,
        payload: dict,
        *,
        headers: dict | None = None,
        raw_body: bytes | None = None,
        context: dict | None = None,
    ) -> bool:
        # Orange echoes the per-payment notif_token in its notification. Verify
        # it against the value persisted at initiation (passed via context).
        context = context or {}
        expected = context.get("notif_token")
        received = payload.get("notif_token")
        if not expected:
            logger.warning(
                "Orange Money notification verified without an expected "
                "notif_token; persist it at initiation to harden this."
            )
            return received is not None
        return bool(received) and hmac_equal(str(expected), str(received))

    def parse_webhook(self, payload: dict) -> WebhookEvent:
        status = str(payload.get("status", "")).upper()
        return WebhookEvent(
            provider=self.name,
            provider_reference=payload.get("pay_token") or payload.get("txnid", ""),
            transaction_reference=payload.get("order_id"),
            status=_STATUS_MAP.get(status, PaymentStatus.PENDING),
            amount=to_decimal(payload.get("amount")),
            currency=payload.get("currency"),
            raw=payload,
        )


def hmac_equal(a: str, b: str) -> bool:
    """Constant-time string comparison."""
    import hmac

    return hmac.compare_digest(a, b)
