#!/usr/bin/env python3
"""
Root test discovery wrapper for MIN-137.
"""

import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from min137.test_migration import TestMigrationVerification

__all__ = ["TestMigrationVerification"]
