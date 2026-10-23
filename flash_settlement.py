#!/usr/bin/env python3
import os
import sys

target = os.path.join(os.path.dirname(__file__), "hackt-mandate", "flash_settlement.py")
os.execv(sys.executable, [sys.executable, target] + sys.argv[1:])
