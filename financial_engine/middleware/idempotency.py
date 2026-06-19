import hashlib
import json
from functools import wraps

from flask import request, jsonify, make_response

from financial_engine.extensions import db
from financial_engine.models.idempotency import IdempotencyRecord


def _compute_request_hash(req) -> str:
    """Create a hash of the request body for duplicate detection."""
    body = req.get_data(as_text=True) or ""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def idempotent(f):
    """Decorator that enforces idempotency on POST endpoints.

    Expects an 'Idempotency-Key' header. If the key was seen before
    with the same request body hash, the stored response is returned.
    """

    @wraps(f)
    def wrapper(*args, **kwargs):
        idem_key = request.headers.get("Idempotency-Key")
        if not idem_key:
            return f(*args, **kwargs)

        request_hash = _compute_request_hash(request)

        existing = IdempotencyRecord.query.filter_by(key=idem_key).first()
        if existing:
            if existing.request_hash != request_hash:
                return make_response(
                    jsonify({
                        "error": "Idempotency key reused with different request body"
                    }),
                    409,
                )
            return make_response(
                json.loads(existing.response_body), existing.response_code
            )

        # Execute the handler
        response = f(*args, **kwargs)

        # Extract response data
        if isinstance(response, tuple):
            body, code = response[0], response[1]
        else:
            body = response
            code = 200

        # Serialize response body
        if hasattr(body, "get_json"):
            resp_body = json.dumps(body.get_json())
        elif isinstance(body, dict):
            resp_body = json.dumps(body)
        else:
            resp_body = json.dumps(body)

        record = IdempotencyRecord(
            key=idem_key,
            request_hash=request_hash,
            response_code=code,
            response_body=resp_body,
        )
        db.session.add(record)
        db.session.commit()

        return response

    return wrapper
