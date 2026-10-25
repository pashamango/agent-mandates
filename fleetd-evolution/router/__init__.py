"""
fleetd-evolution router package
"""
from .canonical_gate import validate_canonical_rules, CanonicalVerdict
from .smart_router import SmartRouter, LeaseRecord
from .ledger import RoutingLedger, run_historical_regression_suite
from .health_pilot import run_codex_health_check, execute_pilot_task_min_221
