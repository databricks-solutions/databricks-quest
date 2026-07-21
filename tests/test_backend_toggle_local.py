"""Local logic tests for the resilient data-backend toggle (no Databricks needed).

Mocks the warehouse (Delta) store and Lakebase so every failure combination of
the two stores can be exercised. Run: python3 tests/test_backend_toggle_local.py
"""

import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

# Fake warehouse_backend module so importing db never needs the SDK.
fake_wh = types.ModuleType("warehouse_backend")
fake_wh.calls = []
fake_wh.fail_writes = False
fake_wh.fail_reads = False
fake_wh.delta_row = None  # value stored in the fake Delta app_settings


def _fake_query(sql, params=()):
    fake_wh.calls.append((sql, params))
    s = sql.strip().upper()
    if s.startswith("SELECT"):
        if fake_wh.fail_reads:
            raise RuntimeError("warehouse read down")
        return [{"value": fake_wh.delta_row}] if fake_wh.delta_row else []
    if s.startswith("CREATE"):
        return []
    if s.startswith("MERGE"):
        if fake_wh.fail_writes:
            raise RuntimeError("PERMISSION_DENIED: no MODIFY on schema")
        fake_wh.delta_row = params[1]
        return []
    raise AssertionError(f"unexpected sql: {sql}")


fake_wh.query = _fake_query
sys.modules["warehouse_backend"] = fake_wh

os.environ["QUEST_SQL_WAREHOUSE_ID"] = "wh123"
os.environ["QUEST_CATALOG"] = "quest"
os.environ["QUEST_DATA_BACKEND"] = "lakebase"

import db  # noqa: E402

# Fake the Lakebase layer.
lakebase = {"up": True, "row": None}


def _fake_lakebase_rows(query, params=()):
    if not lakebase["up"]:
        raise RuntimeError("lakebase down")
    return [{"value": lakebase["row"]}] if lakebase["row"] else []


def _fake_lakebase_write(value):
    if not lakebase["up"]:
        raise RuntimeError("lakebase down / read-only")
    lakebase["row"] = value


db._lakebase_rows = _fake_lakebase_rows
db._lakebase_settings_write = _fake_lakebase_write


def reset(delta=None, lb=None, lb_up=True, wh_fail_writes=False, wh_fail_reads=False):
    fake_wh.delta_row = delta
    fake_wh.fail_writes = wh_fail_writes
    fake_wh.fail_reads = wh_fail_reads
    lakebase["up"] = lb_up
    lakebase["row"] = lb
    db._backend_cache.update(value=None, expiry=0)


passed = 0


def check(name, cond):
    global passed
    assert cond, f"FAIL: {name}"
    passed += 1
    print(f"  ok: {name}")


# 1. THE YOUI CASE: Lakebase dead, switch to warehouse must succeed via Delta.
reset(lb_up=False)
check("switch to warehouse with Lakebase down", db.set_data_backend("warehouse") == "warehouse")
check("delta row persisted", fake_wh.delta_row == "warehouse")
db._backend_cache.update(value=None, expiry=0)
check("readback prefers delta", db.get_data_backend() == "warehouse")

# 2. Switch back to lakebase later (Lakebase recovered): both stores updated.
reset(delta="warehouse", lb="warehouse", lb_up=True)
check("switch back to lakebase", db.set_data_backend("lakebase") == "lakebase")
check("delta updated", fake_wh.delta_row == "lakebase")
check("lakebase mirrored", lakebase["row"] == "lakebase")

# 3. Delta write fails when warehouse configured -> raise, do NOT half-persist.
reset(lb_up=True, wh_fail_writes=True)
try:
    db.set_data_backend("warehouse")
    check("delta write failure raises", False)
except RuntimeError as e:
    check("delta write failure raises", "SQL warehouse" in str(e))
check("lakebase untouched on delta failure", lakebase["row"] is None)

# 4. Read: delta read errors -> fall back to lakebase row.
reset(lb="warehouse", wh_fail_reads=True)
check("delta read failure falls back to lakebase", db.get_data_backend() == "warehouse")

# 5. Read: no rows anywhere -> env default.
reset()
check("no rows -> env default", db.get_data_backend() == "lakebase")

# 6. Read: delta absent but lakebase row present (upgrade from old version).
reset(lb="warehouse")
check("old lakebase-only row honored", db.get_data_backend() == "warehouse")

# 7. Lakebase mirror failure is non-fatal when delta write succeeded.
reset(lb_up=False)
check("mirror failure non-fatal", db.set_data_backend("lakebase") == "lakebase")

# 8. Invalid value rejected.
reset()
try:
    db.set_data_backend("duckdb")
    check("invalid backend rejected", False)
except ValueError:
    check("invalid backend rejected", True)

# 9. Warehouse NOT configured -> pure Lakebase path, raises when down.
os.environ["QUEST_SQL_WAREHOUSE_ID"] = ""
reset(lb_up=False)
try:
    db.set_data_backend("lakebase")
    check("lakebase-only failure raises", False)
except RuntimeError as e:
    check("lakebase-only failure raises", "Lakebase" in str(e))
os.environ["QUEST_SQL_WAREHOUSE_ID"] = "wh123"

print(f"\nALL {passed} CHECKS PASSED")
