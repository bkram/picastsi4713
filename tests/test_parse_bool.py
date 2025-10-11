import importlib
import sys
import types
from pathlib import Path

import pytest


_def_cache = {}


def import_module():
    module = _def_cache.get("module")
    if module is None:
        root = Path(__file__).resolve().parents[1]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        dummy = types.ModuleType("si4713")
        dummy.SI4713 = object  # type: ignore[attr-defined]
        sys.modules.setdefault("si4713", dummy)
        module = importlib.import_module("picast4713")
        _def_cache["module"] = module
    return module


def test_parse_bool_accepts_numeric_truthy():
    mod = import_module()
    assert mod._parse_bool(1, False) is True
    assert mod._parse_bool(42, False) is True
    assert mod._parse_bool(-1, False) is True


def test_parse_bool_accepts_numeric_falsy():
    mod = import_module()
    assert mod._parse_bool(0, True) is False
    assert mod._parse_bool(0.0, True) is False


def test_parse_bool_falls_back_to_default_for_other_values():
    mod = import_module()
    sentinel = object()
    # str is handled specially, ensure unexpected strings still fall back
    assert mod._parse_bool("maybe", True) is True
    assert mod._parse_bool(sentinel, False) is False


@pytest.mark.parametrize(
    "value",
    ["True", "true", "YES", "On", "1"],
)
def test_parse_bool_string_truthy(value):
    mod = import_module()
    assert mod._parse_bool(value, False) is True


@pytest.mark.parametrize(
    "value",
    ["False", "no", "OFF", "0", "  false  "],
)
def test_parse_bool_string_falsy(value):
    mod = import_module()
    assert mod._parse_bool(value, True) is False
