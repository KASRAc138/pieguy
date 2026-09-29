"""PieGuy - a radial pie menu for Windows. Double-click to run (no console)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pieguy.app import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
