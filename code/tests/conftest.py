"""Shared pytest fixtures.

``code/`` is put on ``sys.path`` explicitly so tests import the ``audit`` package
the same way ``python code/main.py`` does. The tests directory deliberately has
no ``__init__.py``: making it a package would name it ``code.tests`` and shadow
the standard library's ``code`` module.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

CODE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = CODE_DIR.parent

if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from audit.loaders import Dataset, load_dataset  # noqa: E402  (after sys.path setup)


@pytest.fixture(scope="session")
def dataset_dir() -> Path:
    """The participant-facing dataset directory."""
    return REPO_ROOT / "dataset"


@pytest.fixture(scope="session")
def dataset(dataset_dir: Path) -> Dataset:
    """The full evaluation dataset, loaded once per test session."""
    return load_dataset(dataset_dir)
