from flask import Blueprint
from flask_restx import Api

from financial_engine.api.accounts import api as accounts_ns
from financial_engine.api.transfers import api as transfers_ns
from financial_engine.api.deposits import api as deposits_ns
from financial_engine.api.webhooks import api as webhooks_ns
from financial_engine.api.fx import api as fx_ns

blueprint = Blueprint("api", __name__, url_prefix="/api/v1")

api = Api(
    blueprint,
    title="Financial Engine API",
    version="1.0",
    description="FinTech Ledger Infrastructure — Double-entry accounting system",
    doc="/docs",
)

api.add_namespace(accounts_ns, path="/accounts")
api.add_namespace(transfers_ns, path="/transfers")
api.add_namespace(deposits_ns, path="/deposits")
api.add_namespace(webhooks_ns, path="/payments")
api.add_namespace(fx_ns, path="/fx")
