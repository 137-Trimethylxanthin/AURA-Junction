"""Landing-page facts are truncated, never rounded up."""

import importlib.util

from tests.conftest import BACKEND


def _facts_module():
    spec = importlib.util.spec_from_file_location("export_facts", BACKEND / "eval" / "export_facts.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_dollar_figures_truncate():
    usd = _facts_module().usd
    assert usd(0.0040825) == "$0.0040"
    assert usd(0.0448607) == "$0.0448"
    assert usd(0.0012) == "$0.0012"
