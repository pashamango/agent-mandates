# Mindrock Agent Fleet Repository — MIN-137

This branch (`minutta/min-137-critical-migration-vestorin-to-mindrockcapital`) implements and verifies the complete decommission and abandonment of `vestor.in` and migration of primary enterprise identity and infrastructure to `mindrock.capital`.

## Components
- `AGENTS.md`: Canonical Mindrock Constitution v1.3 with purged `vestor.in` principal references.
- `min137/`: Complete migration suite, audit reports, and verification engine:
  - `min137/report/MIN-137-migration-report.md`: Technical audit, service inventory matrix, DNS configuration, and Apple ID runbook.
  - `min137/migration_verifier.py`: Verification utility for governance purity and DNS specifications.
  - `min137/test_migration.py`: Automated test suite.
- `tests/`: Discovery entry points for CI / pytest / unittest.
- `evidence/`: Verification run logs, cryptographic manifest, and evidence metadata.

## Verification
```bash
python3 -m unittest discover tests -v
```
