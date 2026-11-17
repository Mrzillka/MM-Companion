"""A pytest plugin: run testmon's selection in collection order, as plain pytest does.

testmon sorts the tests it runs by their recorded durations, module by module and
then test by test inside each module. That puts tests in an order neither a plain
``pytest`` nor CI ever uses, and a test that leans on what an earlier test in its
file left behind can then fail here and nowhere else — which is what happened to
three geometry tests on 2026-10-29. This keeps testmon's choice of *which* tests
and undoes its choice of *when*.
"""

from __future__ import annotations

import pytest


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_collection_modifyitems(session, config, items):
    # The outermost wrapper: this half runs before testmon touches the list, and
    # the half after the yield runs once every other implementation is done.
    collected = {id(item): index for index, item in enumerate(items)}
    yield
    items.sort(key=lambda item: collected.get(id(item), len(collected)))
