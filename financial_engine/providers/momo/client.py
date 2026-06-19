"""MTN Mobile Money — Collection (Request to Pay) client.

API flow (https://momodeveloper.mtn.com):
  1. OAuth token:  POST /collection/token/        (Basic api_user:api_key)
  2. Request pay:  POST /collection/v1_0/requesttopay   (push prompt to payer phone)
  3. Status:       GET  /collection/v1_0/requesttopay/{X-Reference-Id}
  4. Callback:     MTN calls the configured callback host. MTN's Open API has
                   no signed callback, so the secure pattern is to re-query
                   status (``get_payment_status``) rather than trust the body.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
import uuid

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

# MTN MoMo status -> normalized status
_STATUS_MAP = {
    "PENDING": PaymentStatus.PENDING,
    "SUCCESSFUL": PaymentStatus.SUCCESSFUL,
    "FAILED": PaymentStatus.FAILED,
}

_SANDBOX_BASE_URL = "https://sandbox.momodeveloper.mtn.com"


def _safe_json(resp):
    try:
        return resp.json()
    except ValueError:
        return {"body": getattr(resp, "text", "")}


class MomoClient(PaymentProviderClientInterface):
    """Client for MTN Mobile Money Collections (Request to Pay)."""

    name = "mtn_momo"

    def __init__(
        self,
        *,
        subscription_key: str,
        api_user: str,
        api_key: str,
        target_environment: str = "sandbox",
        base_url: str = _SANDBOX_BASE_URL,
        callback_url: str | None = None,
        webhook_secret: str | None = None,
        session: requests.Session | None = None,
        timeout: int = 15,
    ):
        self._subscription_key = subscription_key
        self._api_user = api_user
        self._api_key = api_key
        self._target_environment = target_environment
        self._base_url = base_url.rstrip("/")
        self._callback_url = callback_url
        self._webhook_secret = webhook_secret
        self._session = session or requests.Session()
        self._timeout = timeout
        self._token: str | None = None
        self._token_expiry: float = 0.0

    @classmethod
    def from_config(cls, config, **overrides) -> "MomoClient":
        """Build a client from a Flask/dict config mapping."""
        params = dict(
            subscription_key=config.get("MOMO_SUBSCRIPTION_KEY"),
            api_user=config.get("MOMO_API_USER"),
            api_key=config.get("MOMO_API_KEY"),
            target_environment=config.get("MOMO_TARGET_ENVIRONMENT", "sandbox"),
            base_url=config.get("MOMO_BASE_URL", _SANDBOX_BASE_URL),
            callback_url=config.get("MOMO_CALLBACK_URL"),
            webhook_secret=config.get("MOMO_WEBHOOK_SECRET"),
        )
        params.update(overrides)
        return cls(**params)

    # ------------------------------------------------------------------ auth
    def _get_token(self) -> str:
        # Reuse the cached token until ~1 min before it expires.
        if self._token and time.time() < self._token_expiry - 60:
            return self._token

        creds = base64.b64encode(
            f"{self._api_user}:{self._api_key}".encode()
        ).decode()
        url = f"{self._base_url}/collection/token/"
        try:
            resp = self._session.post(
                url,
                headers={
                    "Authorization": f"Basic {creds}",
                    "Ocp-Apim-Subscription-Key": self._subscription_key,
                },
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise PaymentProviderError(
                f"MoMo token request failed: {exc}", provider=self.name
            ) from exc

        if resp.status_code != 200:
            raise PaymentProviderError(
                "MoMo token request rejected",
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
        # X-Reference-Id is both the idempotency key and the handle used to
        # poll status later, so we surface it as the provider_reference.
        reference_id = str(uuid.uuid4())

        headers = {
            "Authorization": f"Bearer {token}",
            "Ocp-Apim-Subscription-Key": self._subscription_key,
            "X-Reference-Id": reference_id,
            "X-Target-Environment": self._target_environment,
            "Content-Type": "application/json",
        }
        if self._callback_url:
            headers["X-Callback-Url"] = self._callback_url

        body = {
            "amount": str(request.amount),
            "currency": request.currency,
            "externalId": request.transaction_reference,
            "payer": {
                "partyIdType": "MSISDN",
                "partyId": request.customer_reference,
            },
            "payerMessage": request.description or "Payment",
            "payeeNote": request.description or "Payment",
        }

        url = f"{self._base_url}/collection/v1_0/requesttopay"
        try:
            resp = self._session.post(
                url, json=body, headers=headers, timeout=self._timeout
            )
        except requests.RequestException as exc:
            raise PaymentProviderError(
                f"MoMo requesttopay failed: {exc}", provider=self.name
            ) from exc

        # 202 Accepted with an empty body — the prompt is now on the payer phone.
        if resp.status_code != 202:
            raise PaymentProviderError(
                "MoMo requesttopay rejected",
                provider=self.name,
                status_code=resp.status_code,
                raw=_safe_json(resp),
            )

        return PaymentResult(
            provider=self.name,
            provider_reference=reference_id,
            status=PaymentStatus.PENDING,
            payment_url=None,
            raw={"reference_id": reference_id},
        )

    def get_payment_status(
        self, provider_reference: str, context: dict | None = None
    ) -> PaymentStatus:
        token = self._get_token()
        url = f"{self._base_url}/collection/v1_0/requesttopay/{provider_reference}"
        try:
            resp = self._session.get(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Ocp-Apim-Subscription-Key": self._subscription_key,
                    "X-Target-Environment": self._target_environment,
                },
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise PaymentProviderError(
                f"MoMo status request failed: {exc}", provider=self.name
            ) from exc

        if resp.status_code != 200:
            raise PaymentProviderError(
                "MoMo status request rejected",
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
        # MTN's Open API does not sign callbacks. When a shared secret is
        # configured we verify an HMAC-SHA256 of the raw body (a common pattern
        # when fronting MoMo with a gateway); otherwise the caller MUST treat
        # the callback as untrusted and re-query get_payment_status().
        if not self._webhook_secret:
            logger.warning(
                "MoMo webhook received with no configured secret; "
                "re-query status to confirm before crediting."
            )
            return True

        headers = headers or {}
        provided = headers.get("X-Callback-Signature", "")
        expected = hmac.new(
            self._webhook_secret.encode(), raw_body or b"", hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(provided, expected)

    def parse_webhook(self, payload: dict) -> WebhookEvent:
        status = str(payload.get("status", "")).upper()
        return WebhookEvent(
            provider=self.name,
            provider_reference=payload.get("referenceId")
            or payload.get("externalId", ""),
            transaction_reference=payload.get("externalId"),
            status=_STATUS_MAP.get(status, PaymentStatus.PENDING),
            amount=to_decimal(payload.get("amount")),
            currency=payload.get("currency"),
            raw=payload,
        )
