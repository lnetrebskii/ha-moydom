"""Load the API modules without importing Home Assistant."""

from __future__ import annotations

from pathlib import Path
import sys
import types

PACKAGE = "moydom_core"
_path = Path(__file__).resolve().parents[1] / "custom_components" / "moydom"
_module = types.ModuleType(PACKAGE)
_module.__path__ = [str(_path)]
sys.modules.setdefault(PACKAGE, _module)
