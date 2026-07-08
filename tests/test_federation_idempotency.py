"""Idempotent single-award across children (ADR_006).

The scoring idempotency key is the single mechanism that guarantees a task is
awarded exactly once even when a child retries or two writers race. These tests
pin its behaviour without touching a database.
"""

from services import federation as fed


def test_same_workspace_task_is_one_key():
    """A retry from the same child for the same task collapses to one key."""
    k1 = fed.deterministic_idempotency_key("ws-01", "evt_1", "task_a")
    k2 = fed.deterministic_idempotency_key("ws-01", "evt_1", "task_a")
    assert k1 == k2


def test_distinct_workspaces_get_distinct_keys():
    """Two different child workspaces awarding the same task do not collide."""
    k1 = fed.deterministic_idempotency_key("ws-01", "evt_1", "task_a")
    k2 = fed.deterministic_idempotency_key("ws-02", "evt_1", "task_a")
    assert k1 != k2


def test_scoring_rule_partitions_the_key():
    """Different scoring rules on the same task are awarded independently."""
    base = fed.deterministic_idempotency_key("ws-01", "evt_1", "task_a", "base")
    bonus = fed.deterministic_idempotency_key("ws-01", "evt_1", "task_a", "bonus")
    assert base != bonus


def test_standalone_keyspace_is_stable():
    """Standalone (no workspace_id) keeps a stable 'local' keyspace."""
    k = fed.deterministic_idempotency_key(None, "evt_1", "task_a")
    assert k == "local:evt_1:task_a:base"


def test_different_tasks_distinct():
    k1 = fed.deterministic_idempotency_key("ws-01", "evt_1", "task_a")
    k2 = fed.deterministic_idempotency_key("ws-01", "evt_1", "task_b")
    assert k1 != k2
