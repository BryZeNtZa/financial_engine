import uuid

from flask import request, g


def init_tracing(app):
    """Register before/after request hooks for distributed tracing."""

    @app.before_request
    def set_correlation_id():
        corr_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
        g.correlation_id = corr_id

    @app.after_request
    def add_correlation_header(response):
        corr_id = getattr(g, "correlation_id", None)
        if corr_id:
            response.headers["X-Correlation-ID"] = corr_id
        return response
