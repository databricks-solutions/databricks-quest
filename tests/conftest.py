"""Pytest configuration for Databricks Quest.

Puts the ``app/`` directory on ``sys.path`` so the federation modules import the
same way they do at runtime (``import config``, ``import db``,
``from repositories import ...``).
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(REPO_ROOT, "app")

for path in (APP_DIR, REPO_ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)
