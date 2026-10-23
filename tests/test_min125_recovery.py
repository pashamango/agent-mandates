#!/usr/bin/env python3
"""Convenience test entrypoint for root test discovery."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from min125.test_recovery import *

if __name__ == "__main__":
    import unittest
    unittest.main()
