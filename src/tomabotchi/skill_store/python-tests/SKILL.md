---
name: python-tests
description: Write and run pytest tests for Python code. Use when adding a feature, fixing a bug, or when asked to test something.
---

# Python tests

1. Find the existing tests (`tests/`, `test_*.py`) and follow their style and fixtures.
2. For a bug, write a test that fails first, then fix the code until it passes.
3. Name tests for the behaviour: `test_empty_cart_has_zero_total`, not `test_cart_2`.
4. One behaviour per test. Prefer plain asserts. Use `tmp_path` for files and `monkeypatch` for environment.
5. Run the narrowest set first (`pytest tests/test_cart.py -q`), then the whole suite before you finish.

If a test fails for a reason you don't understand after two tries, stop and report the output instead of
repeating the same command.
