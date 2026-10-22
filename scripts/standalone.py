#!/usr/bin/env python3
"""Optional legacy CLI; distinct from the main Hermes integration."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'qq-support-bot'))
from qq_support_bot.__main__ import main

if __name__ == '__main__':
    main()
