# MIN-137: Migration from `vestor.in` to `mindrock.capital`

## Overview
This directory contains the canonical migration audit, architectural specification, verification tooling, and automated test suite for Linear ticket **MIN-137** (`[CRITICAL] Migration vestor.in to mindrock.capital`).

## Directory Structure
- `report/MIN-137-migration-report.md`: Comprehensive audit report, service inventory matrix, DNS architecture, and Darwin Apple ID migration runbook.
- `migration_verifier.py`: Core verification engine checking codebase purity, DNS record syntax, and migration inventory matrix completeness.
- `test_migration.py`: Automated unit test suite verifying all migration invariants.

## Running Tests
Run tests using Python 3 unittest:
```bash
python3 -m unittest min137/test_migration.py -v
```
Or via root test discovery:
```bash
python3 -m unittest discover tests -v
```
