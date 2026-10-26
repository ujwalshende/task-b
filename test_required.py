"""Root entry point: uv run pytest test_required.py.

The tests live in tests/test_required.py. Default discovery uses tests/ so this
entry point does not cause duplicate execution when running uv run pytest.
"""

from tests import test_required as cases

TestRequired = cases.TestRequired
TestAdditional = cases.TestAdditional
