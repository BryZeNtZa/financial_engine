import os
import logging

from flask import Flask

from financial_engine.config import Config
from financial_engine.extensions import db, migrate


def create_app(test_config=None):
    """Application factory for the Financial Engine."""
    app = Flask(__name__, instance_relative_config=True)

    if test_config is None:
        app.config.from_object(Config)
    else:
        app.config.from_mapping(test_config) if isinstance(test_config, dict) else app.config.from_object(test_config)

    # Ensure instance folder exists
    os.makedirs(app.instance_path, exist_ok=True)

    # Init extensions
    db.init_app(app)
    migrate.init_app(app, db)

    # Register tracing middleware
    from financial_engine.middleware.tracing import init_tracing
    init_tracing(app)

    # Register API blueprint
    from financial_engine.api import blueprint as api_bp
    app.register_blueprint(api_bp)

    # Wire up domain event handlers
    _register_event_handlers(app)

    # Register error handlers
    _register_error_handlers(app)

    # Create tables (for development / SQLite)
    with app.app_context():
        # Import all models so they're registered
        from financial_engine import models
        db.create_all()

    logging.basicConfig(level=logging.INFO)

    return app


def _register_event_handlers(app):
    """Subscribe notification service to domain events."""
    from financial_engine.domain.events import (
        event_bus,
        TRANSFER_COMPLETED,
        TRANSFER_FAILED,
        DEPOSIT_COMPLETED,
    )
    from financial_engine.services.notification_service import NotificationService

    notifier = NotificationService()
    event_bus.subscribe(TRANSFER_COMPLETED, notifier.handle_transfer_completed)
    event_bus.subscribe(TRANSFER_FAILED, notifier.handle_transfer_failed)
    event_bus.subscribe(DEPOSIT_COMPLETED, notifier.handle_deposit_completed)


def _register_error_handlers(app):
    """Register global error handlers."""
    from financial_engine.domain.exceptions import DomainError

    @app.errorhandler(DomainError)
    def handle_domain_error(error):
        return {"error": error.message, "code": error.code}, 400

    @app.errorhandler(404)
    def not_found(error):
        return {"error": "Resource not found"}, 404

    @app.errorhandler(500)
    def internal_error(error):
        return {"error": "Internal server error"}, 500
