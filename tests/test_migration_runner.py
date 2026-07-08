"""Migration runner apply-once / no-op-on-rerun (PR-pilot G).

``app/migrations/run_migrations.py`` was previously untested. These tests drive
its ``run()`` orchestration against a fake backend (no Postgres, no psql), and
also exercise ``discover_migrations`` against the real ``*.sql`` files so a
malformed/renamed migration is caught.
"""

import importlib.util
import os
import types

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNNER_PATH = os.path.join(REPO_ROOT, "app", "migrations", "run_migrations.py")


def _load_runner():
    spec = importlib.util.spec_from_file_location("run_migrations_under_test", RUNNER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeBackend:
    """In-memory schema_migrations: records applied versions, counts applies."""

    def __init__(self):
        self.versions = set()
        self.apply_calls = 0
        self.ensure_calls = 0

    def ensure_table(self):
        self.ensure_calls += 1

    def applied_versions(self):
        return set(self.versions)

    def apply(self, version, description, path):
        self.apply_calls += 1
        self.versions.add(version)

    def close(self):
        pass


@pytest.fixture()
def runner():
    return _load_runner()


def _args():
    # run() only reads .migrations_dir; build_backend is stubbed away.
    return types.SimpleNamespace(migrations_dir=None)


def test_discover_finds_real_migrations(runner):
    migs = runner.discover_migrations(os.path.join(REPO_ROOT, "app", "migrations"))
    assert migs, "expected at least one .sql migration"
    # Ordered by filename, each carries (version, description, path).
    versions = [v for (v, _d, _p) in migs]
    assert versions == sorted(versions)
    for _v, _d, path in migs:
        assert path.endswith(".sql")


def test_apply_once_then_noop_on_rerun(runner, monkeypatch):
    backend = FakeBackend()
    monkeypatch.setattr(runner, "build_backend", lambda args: backend)

    total = len(runner.discover_migrations(os.path.join(REPO_ROOT, "app", "migrations")))
    assert total > 0

    # First run applies everything.
    rc1 = runner.run(_args())
    assert rc1 == 0
    assert backend.apply_calls == total
    assert len(backend.versions) == total

    # Second run is a complete no-op: nothing re-applied.
    rc2 = runner.run(_args())
    assert rc2 == 0
    assert backend.apply_calls == total  # unchanged


def test_partial_apply_only_runs_missing(runner, monkeypatch):
    backend = FakeBackend()
    migs = runner.discover_migrations(os.path.join(REPO_ROOT, "app", "migrations"))
    # Pretend the first migration is already applied.
    backend.versions.add(migs[0][0])
    monkeypatch.setattr(runner, "build_backend", lambda args: backend)

    runner.run(_args())
    # Everything except the pre-applied one runs exactly once.
    assert backend.apply_calls == len(migs) - 1
    assert len(backend.versions) == len(migs)


def test_apply_failure_propagates(runner, monkeypatch):
    class BoomBackend(FakeBackend):
        def apply(self, version, description, path):
            raise RuntimeError("migration body failed")

    monkeypatch.setattr(runner, "build_backend", lambda args: BoomBackend())
    with pytest.raises(RuntimeError):
        runner.run(_args())
