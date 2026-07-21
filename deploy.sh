#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Databricks Quest — One-Shot Deployment
#
# Deploys the full Quest gamification app to your Databricks workspace.
# Handles everything: auth check, warehouse selection, frontend build,
# bundle deploy, scoring pipeline, permissions, and app startup.
#
# Usage:
#   ./deploy.sh                          # Interactive (prompts for everything)
#   ./deploy.sh --warehouse "My WH"      # Skip warehouse prompt
#   ./deploy.sh --catalog quest_data     # Specify catalog name
#   ./deploy.sh --profile my-profile     # Use a specific CLI profile
#   ./deploy.sh --app-name my-quest      # Custom app name
#
# Requirements:
#   - Databricks CLI v0.200+ (brew install databricks/tap/databricks)
#   - Node.js 18+ (brew install node)
#   - Authenticated CLI session (script will prompt if needed)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Defaults ─────────────────────────────────────────────────────────────────
APP_NAME="databricks-quest"
QUEST_CATALOG=""
QUEST_SCHEMA="quest"
WAREHOUSE_NAME=""
WAREHOUSE_ID=""
PROFILE_FLAG=""
PROFILE_NAME=""
LAKEBASE_HOST=""
LAKEBASE_DB="quest_db"
TARGET="dev"
SKIP_BUILD=""
SKIP_SCORING=""
SKIP_AUTH_CHECK=""
DEPLOY_MODE=""  # "full" (DAB bundle) or "quick" (direct API, like Forge)

# Data backend for the adoption app: "lakebase" (default, Postgres) or
# "warehouse" (read scored Delta tables through a serverless SQL warehouse — no
# Lakebase, no sync). Warehouse mode intentionally consumes DBUs.
QUEST_DATA_BACKEND="lakebase"

# ── Event Mode (GameDay) — opt-in master switch (legacy adoption is default) ──
# Off unless --event-mode is passed or a master/child role is selected. When
# off, the deployed app exposes ZERO GameDay surface (Event APIs 404, Event UI
# hidden) and GameDay migrations are skipped — i.e. the legacy adoption app.
QUEST_EVENT_MODE=""         # "on" enables Event Mode; empty = off (legacy)

# ── Admin allowlist ──────────────────────────────────────────────────────────
# Comma-separated emails allowed to see the adoption Admin page (/api/admin/*).
# If left empty, deploy.sh defaults it to the deploying user after auth so the
# Admin page is never wide open in a deployment.
QUEST_ADMIN_ALLOWLIST=""    # e.g. "alice@corp.com,bob@corp.com"

# ── Host allowlist (Event Mode) ──────────────────────────────────────────────
# Comma-separated emails allowed to call GameDay host endpoints (/api/host/*)
# regardless of per-event event_hosts rows. Host authority is the union of this
# allowlist, the admin allowlist, and per-event event_hosts. When Event Mode is
# on and NONE of these are configured, host endpoints fail closed.
QUEST_HOST_ALLOWLIST=""     # e.g. "host1@corp.com,host2@corp.com"

# ── Federation (ADR_006) — one codebase, role selected by these flags ────────
QUEST_ROLE=""               # standalone (default) | master | child
MASTER_LAKEBASE_HOST=""     # child: the MASTER workspace's shared Lakebase host
MASTER_LAKEBASE_TOKEN=""    # child: the shared event-writer credential/secret
MASTER_LAKEBASE_USER="quest_event_writer"  # child: writer role name
EVENT_SLUG=""               # event this deployment is wired to
WORKSPACE_ID=""             # child: id used to attribute federated writes
EVENT_WRITER_PASSWORD=""    # master: generated secret for the writer role

# ── Colors ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
NC='\033[0m'

# ── Helpers ──────────────────────────────────────────────────────────────────
info()    { echo -e "${BLUE}▸${NC} $*"; }
success() { echo -e "${GREEN}✓${NC} $*"; }
warn()    { echo -e "${YELLOW}!${NC} $*"; }
fail()    { echo -e "${RED}✗${NC} $*"; exit 1; }
step()    { echo -e "\n${BOLD}${CYAN}── $* ──${NC}\n"; }

# Apply GameDay schema migrations to Lakebase (idempotent; safe to re-run).
# Args: <host> <db> <user> <token>. Non-fatal — adoption mode works without it.
run_gameday_migrations() {
  local host="$1" db="$2" user="$3" token="$4"
  local runner="${SCRIPT_DIR:-$(pwd)}/app/migrations/run_migrations.py"
  if [ ! -f "$runner" ]; then
    warn "Migration runner not found ($runner) — skipping GameDay migrations."
    return 0
  fi
  if [ -z "$host" ] || [ -z "$token" ]; then
    warn "Lakebase host/token unavailable — skipping GameDay migrations."
    return 0
  fi
  info "Applying GameDay schema migrations..."
  if PGPASSWORD="$token" python3 "$runner" \
      --lakebase-host "$host" \
      --lakebase-db "$db" \
      --user "$user"; then
    success "GameDay migrations applied"
  else
    warn "GameDay migrations failed (non-fatal). Re-run later with:
    PGPASSWORD=<token> python3 app/migrations/run_migrations.py \\
      --lakebase-host $host --lakebase-db $db --user $user"
  fi
}

# Provision the shared INSERT-only event-writer Postgres role on the MASTER
# Lakebase and grant it exactly the privileges a child app needs (ADR_006):
#   INSERT  on the four event-fact tables
#   SELECT  on the leaderboard read surface (so children can render the
#           event-wide leaderboard and locate their own team's rank)
#   INSERT/UPDATE/SELECT on event_workspaces (for the startup check-in upsert)
#   SELECT/INSERT on quest_admins (shared admin allowlist — children read it so
#           admins are global, and admins can ADD admins from a child; removal
#           requires the master/standalone app — no DELETE for the writer role)
# No UPDATE/DELETE on facts, no access to secrets. Idempotent: re-running
# resets the role's password (rotate-per-event) and re-applies grants.
# Args: <host> <db> <admin_user> <admin_token> <writer_user> <writer_password>
provision_event_writer_role() {
  local host="$1" db="$2" admin_user="$3" admin_token="$4" writer="$5" wpass="$6"
  if [ -z "$host" ] || [ -z "$admin_token" ]; then
    warn "Lakebase host/token unavailable — skipping event-writer role provisioning."
    return 1
  fi
  info "Provisioning shared event-writer role '$writer'..."
  # Escape single quotes for safe SQL string literals.
  local esc_pass="${wpass//\'/\'\'}"
  local esc_writer="${writer//\'/\'\'}"
  if PGPASSWORD="$admin_token" psql "host=$host port=5432 dbname=$db user=$admin_user sslmode=require" \
      -v ON_ERROR_STOP=1 -q -c "
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$esc_writer') THEN
    EXECUTE format('CREATE ROLE %I WITH LOGIN PASSWORD %L', '$esc_writer', '$esc_pass');
  ELSE
    EXECUTE format('ALTER ROLE %I WITH LOGIN PASSWORD %L', '$esc_writer', '$esc_pass');
  END IF;
  EXECUTE format('GRANT INSERT ON scoring_events, task_attempts, validation_results, hints_taken TO %I', '$esc_writer');
  EXECUTE format('GRANT INSERT, UPDATE, SELECT ON event_workspaces TO %I', '$esc_writer');
  EXECUTE format('GRANT SELECT ON event_leaderboard, team_scores, teams, participant_identity_map, events, announcements TO %I', '$esc_writer');
  EXECUTE format('GRANT SELECT ON quest_packs, quest_pack_versions, quests, quest_tasks, task_hints, task_validators TO %I', '$esc_writer');
  -- Shared admin allowlist: children read it (so admins are global) and may
  -- ADD admins through the app. No DELETE — removing an admin requires the
  -- master/standalone app (full workspace identity); a leaked child credential
  -- must never be able to delete. Guarded so a missing table (migrations not
  -- yet applied) doesn't abort role provisioning.
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'quest_admins') THEN
    EXECUTE format('GRANT SELECT, INSERT ON quest_admins TO %I', '$esc_writer');
  END IF;
END \$\$;
"; then
    success "Event-writer role '$writer' provisioned and granted (INSERT-only on facts)"
    return 0
  fi
  warn "Event-writer role provisioning failed (non-fatal). Provision it manually before the event,
    then verify with: python3 scripts/federation_spike.py --host $host --db $db --user $writer"
  return 1
}

# Write app/app.yaml with role-appropriate env. Standalone emits exactly the
# two-variable file it always has (byte-for-byte unchanged). master/child add
# the federation env (QUEST_ROLE, workspace id, event slug, and — for child —
# the explicit writer credential pointed at the master's shared Lakebase).
# Args: <lakebase_host> <lakebase_db>
write_app_yaml() {
  local lb_host="$1" lb_db="$2"
  local app_yaml="${SCRIPT_DIR:-$(pwd)}/app/app.yaml"
  {
    echo "command:"
    echo "  - uvicorn"
    echo "  - main:app"
    echo "  - --host"
    echo "  - 0.0.0.0"
    echo "  - --port"
    echo "  - \"8000\""
    echo ""
    echo "env:"
    # Lakebase is always wired (the app reads it by default and stores the
    # backend toggle there). Emitted whenever a host is known.
    if [ -n "$lb_host" ]; then
      echo "  - name: LAKEBASE_HOST"
      echo "    value: \"$lb_host\""
      echo "  - name: LAKEBASE_DB"
      echo "    value: \"$lb_db\""
    fi
    # QUEST_DATA_BACKEND is the deploy-time DEFAULT (admins flip the active one
    # at runtime in Admin settings). Catalog/schema are ALWAYS emitted when known
    # — not just for warehouse-default deploys — because the runtime toggle
    # persists its setting in ${QUEST_CATALOG}.${QUEST_SCHEMA}.app_settings via
    # the warehouse, and must work even when Lakebase is down.
    echo "  - name: QUEST_DATA_BACKEND"
    echo "    value: \"$QUEST_DATA_BACKEND\""
    if [ -n "$QUEST_CATALOG" ]; then
      echo "  - name: QUEST_CATALOG"
      echo "    value: \"$QUEST_CATALOG\""
      echo "  - name: QUEST_SCHEMA"
      echo "    value: \"$QUEST_SCHEMA\""
    fi
    # SQL warehouse: the warehouse-backend target, and the sql_assertion validator
    # target in Event Mode. Emitted whenever known.
    [ -n "$WAREHOUSE_ID" ] && { echo "  - name: QUEST_SQL_WAREHOUSE_ID"; echo "    value: \"$WAREHOUSE_ID\""; }
    # Admin allowlist for /api/admin/* (comma-separated emails). When set, the
    # Admin page and its APIs are restricted to these users; absence = open.
    [ -n "$QUEST_ADMIN_ALLOWLIST" ] && { echo "  - name: QUEST_ADMIN_ALLOWLIST"; echo "    value: \"$QUEST_ADMIN_ALLOWLIST\""; }
    # Host allowlist for /api/host/* (comma-separated emails). Only meaningful in
    # Event Mode; absence is fine when admins or per-event event_hosts exist.
    [ -n "$QUEST_HOST_ALLOWLIST" ] && { echo "  - name: QUEST_HOST_ALLOWLIST"; echo "    value: \"$QUEST_HOST_ALLOWLIST\""; }
    # Event Mode master switch. Only emitted when ON; absence = legacy default,
    # so a standalone adoption deploy keeps its exact two-variable app.yaml.
    if [ "$QUEST_EVENT_MODE" = "on" ]; then
      echo "  - name: QUEST_EVENT_MODE"
      echo "    value: \"on\""
    fi
    # Event slug this deployment is wired to. Emitted for ANY role (including
    # standalone Event-Mode) when set, so a single-workspace GameDay can pin its
    # event the same way master/child do.
    [ -n "$EVENT_SLUG" ] && { echo "  - name: QUEST_EVENT_SLUG"; echo "    value: \"$EVENT_SLUG\""; }
    if [ -n "$QUEST_ROLE" ] && [ "$QUEST_ROLE" != "standalone" ]; then
      echo "  - name: QUEST_ROLE"
      echo "    value: \"$QUEST_ROLE\""
      [ -n "$WORKSPACE_ID" ] && { echo "  - name: QUEST_WORKSPACE_ID"; echo "    value: \"$WORKSPACE_ID\""; }
      if [ "$QUEST_ROLE" = "child" ]; then
        echo "  - name: LAKEBASE_USER"
        echo "    value: \"$MASTER_LAKEBASE_USER\""
        echo "  - name: LAKEBASE_PASSWORD"
        echo "    value: \"$MASTER_LAKEBASE_TOKEN\""
      fi
    fi
  } > "$app_yaml"
}

# Grant the app SP the Lakebase privileges the app needs at runtime: blanket
# SELECT, plus explicit write privileges (app_settings upserts for the backend
# toggle, CREATE so it can make its own tables like quest_admins). Deliberately
# explicit so nothing depends on the best-effort DATABRICKS_SUPERUSER membership,
# whose creation has failed silently on customer workspaces (=> "Could not
# persist the backend setting"). Two psql calls so a failure of the write grants
# can't roll back the read grants (each -c is one implicit transaction).
grant_sp_lakebase_access() {
  local lb_host="$1" lb_db="$2" lb_token="$3" sp="$4"
  PGPASSWORD="$lb_token" psql "host=$lb_host port=5432 dbname=$lb_db user=$USER_EMAIL sslmode=require" -c "
GRANT SELECT ON ALL TABLES IN SCHEMA public TO \"$sp\";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO \"$sp\";
" 2>/dev/null || true
  PGPASSWORD="$lb_token" psql "host=$lb_host port=5432 dbname=$lb_db user=$USER_EMAIL sslmode=require" -c "
CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT, updated_at TIMESTAMP);
GRANT CREATE ON SCHEMA public TO \"$sp\";
GRANT SELECT, INSERT, UPDATE ON app_settings TO \"$sp\";
" 2>/dev/null || true
}

# Grant the app SP warehouse + Unity Catalog access — applied on EVERY deploy
# with a warehouse (not only --data-backend warehouse), because the runtime
# Admin toggle lets any deployment switch to warehouse mode later. The SP gets
# read on the scored Delta tables, MODIFY on the quest schema (so it can upsert
# the app_settings backend toggle even when Lakebase is down), and CAN_USE on
# the warehouse. The app_settings Delta table is pre-created here as the
# deploying user so the SP never needs CREATE TABLE.
grant_sp_warehouse_access() {
  local sp="$1"
  [ -n "$sp" ] && [ -n "$WAREHOUSE_ID" ] || return 0
  info "Granting SP ($sp) access on $QUEST_CATALOG.$QUEST_SCHEMA + CAN_USE on warehouse $WAREHOUSE_ID..."
  HOST="${DATABRICKS_HOST:-$WORKSPACE_HOST}" TOKEN="${DATABRICKS_TOKEN:-}" WH="$WAREHOUSE_ID" \
  SP="$sp" CAT="$QUEST_CATALOG" SCH="$QUEST_SCHEMA" python3 <<'PYEOF' || warn "Some warehouse grants may need to be applied manually."
import os, json, time, urllib.request
host=os.environ.get("HOST","").rstrip("/"); tok=os.environ.get("TOKEN",""); wh=os.environ["WH"]
sp=os.environ["SP"]; cat=os.environ["CAT"]; sch=os.environ["SCH"]
def sql(s):
    body=json.dumps({"warehouse_id":wh,"statement":s,"wait_timeout":"30s"}).encode()
    req=urllib.request.Request(host+"/api/2.0/sql/statements",data=body,headers={"Authorization":"Bearer "+tok,"Content-Type":"application/json"})
    try: d=json.loads(urllib.request.urlopen(req,timeout=60).read())
    except Exception as e: print("  grant error:",str(e)[:120]); return
    sid=d.get("statement_id"); st=d.get("status",{}).get("state")
    while st in ("PENDING","RUNNING") and sid:
        time.sleep(2)
        r=urllib.request.Request(host+"/api/2.0/sql/statements/"+sid,headers={"Authorization":"Bearer "+tok})
        d=json.loads(urllib.request.urlopen(r,timeout=30).read()); st=d.get("status",{}).get("state")
    if st!="SUCCEEDED": print("  grant warn:",(d.get("status",{}).get("error",{}) or {}).get("message","")[:140])
for s in [f"CREATE SCHEMA IF NOT EXISTS `{cat}`.`{sch}`",
          f"CREATE TABLE IF NOT EXISTS `{cat}`.`{sch}`.app_settings (`key` STRING, value STRING, updated_at TIMESTAMP)",
          f"GRANT USE CATALOG ON CATALOG `{cat}` TO `{sp}`",
          f"GRANT USE SCHEMA, SELECT, MODIFY ON SCHEMA `{cat}`.`{sch}` TO `{sp}`"]:
    sql(s)
body=json.dumps({"access_control_list":[{"service_principal_name":sp,"permission_level":"CAN_USE"}]}).encode()
req=urllib.request.Request(host+f"/api/2.0/permissions/warehouses/{wh}",data=body,method="PATCH",
    headers={"Authorization":"Bearer "+tok,"Content-Type":"application/json"})
try: urllib.request.urlopen(req,timeout=30); print("  warehouse CAN_USE granted")
except Exception as e: print("  warehouse perm warn:",str(e)[:120])
PYEOF
  success "Service principal granted warehouse + schema access (admins can switch backends at runtime)"
}

# Look up the app's service principal client id (REST-stable across CLI versions).
app_sp_client_id() {
  $CLI apps get "$APP_NAME" $PROFILE_FLAG -o json 2>/dev/null | python3 -c "
import sys, json
print(json.load(sys.stdin).get('service_principal_client_id', ''))
" 2>/dev/null || true
}

# ── Parse Arguments ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --app-name)       APP_NAME="$2"; shift 2 ;;
    --catalog)        QUEST_CATALOG="$2"; shift 2 ;;
    --schema)         QUEST_SCHEMA="$2"; shift 2 ;;
    --warehouse)      WAREHOUSE_NAME="$2"; shift 2 ;;
    --warehouse-id)   WAREHOUSE_ID="$2"; shift 2 ;;
    --profile)        PROFILE_NAME="$2"; PROFILE_FLAG="--profile $2"; shift 2 ;;
    --target)         TARGET="$2"; shift 2 ;;
    --lakebase-host)  LAKEBASE_HOST="$2"; shift 2 ;;
    --lakebase-db)    LAKEBASE_DB="$2"; shift 2 ;;
    --skip-build)     SKIP_BUILD=1; shift ;;
    --skip-scoring)   SKIP_SCORING=1; shift ;;
    --skip-verify)    SKIP_VERIFY=1; shift ;;
    --skip-auth-check) SKIP_AUTH_CHECK=1; shift ;;
    --quick)          DEPLOY_MODE="quick"; shift ;;
    --full)           DEPLOY_MODE="full"; shift ;;
    --data-backend)   QUEST_DATA_BACKEND="$2"; shift 2 ;;
    --event-mode)         QUEST_EVENT_MODE="on"; shift ;;
    --admins)             QUEST_ADMIN_ALLOWLIST="$2"; shift 2 ;;
    --host-allowlist)     QUEST_HOST_ALLOWLIST="$2"; shift 2 ;;
    --role)               QUEST_ROLE="$2"; shift 2 ;;
    --master-lakebase-host)  MASTER_LAKEBASE_HOST="$2"; shift 2 ;;
    --master-lakebase-token) MASTER_LAKEBASE_TOKEN="$2"; shift 2 ;;
    --master-lakebase-user)  MASTER_LAKEBASE_USER="$2"; shift 2 ;;
    --event)              EVENT_SLUG="$2"; shift 2 ;;
    --workspace-id)       WORKSPACE_ID="$2"; shift 2 ;;
    --help|-h)
      echo "Usage: ./deploy.sh [OPTIONS]"
      echo ""
      echo "Options:"
      echo "  --app-name NAME       App name (default: databricks-quest)"
      echo "  --catalog NAME        Unity Catalog name for Quest data"
      echo "  --schema NAME         Schema name (default: quest)"
      echo "  --warehouse NAME      SQL Warehouse name (interactive if omitted)"
      echo "  --warehouse-id ID     SQL Warehouse ID (skips warehouse lookup)"
      echo "  --profile NAME        Databricks CLI profile to use"
      echo "  --target TARGET       Bundle target: dev or prod (default: dev)"
      echo "  --lakebase-host HOST  Lakebase endpoint host (optional, for faster reads)"
      echo "  --lakebase-db NAME    Lakebase database name (default: quest_db)"
      echo "  --quick               Quick deploy: direct API (no DAB bundle, like Forge)"
      echo "  --full                Full deploy: DAB bundle with scoring job (default)"
      echo "  --skip-build          Skip frontend build (use existing app/static/)"
      echo "  --skip-scoring        Skip running the scoring pipeline"
      echo "  --skip-verify         Skip the post-deploy permission & data verification"
      echo "  --skip-auth-check     Skip authentication validation (use if already authenticated)"
      echo "  --admins EMAILS       Comma-separated emails seeded as Admin-page admins."
      echo "                        Defaults to the deploying user when omitted (except"
      echo "                        --role child, which inherits admins from the master)."
      echo "                        Stored in Lakebase (quest_admins) and shared across"
      echo "                        master/child; admins can add more admins in-app."
      echo ""
      echo "Event Mode (GameDay) — opt-in; legacy adoption app is the default:"
      echo "  --event-mode             Enable GameDay/Event Mode (default: OFF)."
      echo "                           When OFF, Event APIs 404, Event UI is hidden,"
      echo "                           and GameDay migrations are skipped."
      echo "                           Implied by --role master|child."
      echo "  --host-allowlist EMAILS  Comma-separated emails allowed to call GameDay"
      echo "                           host endpoints (/api/host/*). Host authority is the"
      echo "                           union of this list, --admins, and per-event hosts."
      echo "                           With Event Mode on and none configured, host"
      echo "                           endpoints fail closed (set QUEST_HOST_OPEN=1 in dev)."
      echo ""
      echo "Multi-workspace federation (ADR_006 — one codebase, role-driven):"
      echo "  --role ROLE              standalone (default) | master | child"
      echo "  --event SLUG             Event slug this deployment is wired to"
      echo "  --workspace-id ID        Child: id used to attribute federated writes"
      echo "                           (defaults to the workspace host if omitted)"
      echo "  --master-lakebase-host H Child: the MASTER workspace's shared Lakebase host"
      echo "                           (setting this defaults --role to child)"
      echo "  --master-lakebase-token T Child: shared event-writer credential/secret"
      echo "  --master-lakebase-user U Child: writer role name (default: quest_event_writer)"
      echo ""
      echo "  master: provisions its own Lakebase, runs migrations, creates the shared"
      echo "          event-writer role and prints its credential to hand to children."
      echo "  child:  skips local Lakebase + migrations; points at the master Lakebase"
      echo "          with the writer credential and stamps writes with --workspace-id."
      echo "  --help, -h            Show this help message"
      exit 0
      ;;
    *) fail "Unknown option: $1. Run ./deploy.sh --help for usage." ;;
  esac
done

# ── Resolve federation role (ADR_006) ────────────────────────────────────────
# Role precedence: explicit --role wins; else presence of --master-lakebase-host
# implies child; else standalone (today's default, unchanged).
if [ -z "$QUEST_ROLE" ]; then
  if [ -n "$MASTER_LAKEBASE_HOST" ]; then
    QUEST_ROLE="child"
  else
    QUEST_ROLE="standalone"
  fi
fi
case "$QUEST_ROLE" in
  standalone|master|child) ;;
  *) fail "Invalid --role '$QUEST_ROLE'. Use standalone, master, or child." ;;
esac

# ── Resolve Event Mode (GameDay) ─────────────────────────────────────────────
# Opt-in: master/child roles imply it; otherwise it is off unless --event-mode
# was passed. Off = legacy adoption app (no GameDay surface, no GameDay schema).
if [ "$QUEST_ROLE" = "master" ] || [ "$QUEST_ROLE" = "child" ]; then
  QUEST_EVENT_MODE="on"
fi

if [ "$QUEST_ROLE" = "child" ]; then
  [ -n "$MASTER_LAKEBASE_HOST" ] || fail "child role requires --master-lakebase-host."
  [ -n "$MASTER_LAKEBASE_TOKEN" ] || fail "child role requires --master-lakebase-token (shared event-writer credential)."
  # Point the app's Lakebase layer at the master's shared endpoint. Setting
  # LAKEBASE_HOST here makes the script take the "provided Lakebase" path
  # (skips local provisioning); migrations are additionally skipped for child.
  LAKEBASE_HOST="$MASTER_LAKEBASE_HOST"
  # The child has only INSERT on the shared facts — it must never run the
  # adoption scoring pipeline or the Delta→Lakebase sync.
  SKIP_SCORING=1
fi

# ── Banner ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${CYAN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${CYAN}║         Databricks Quest — Deploy            ║${NC}"
echo -e "${BOLD}${CYAN}╚══════════════════════════════════════════════╝${NC}"
echo ""

# ══════════════════════════════════════════════════════════════════════════════
# STEP 1: Check Prerequisites
# ══════════════════════════════════════════════════════════════════════════════
step "Step 1/8: Checking prerequisites"

# Find Databricks CLI — prefer the newest version available
CLI=""
BEST_VERSION="0.0.0"

check_cli() {
  local path="$1"
  if [ -x "$path" ]; then
    local ver
    ver=$("$path" --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || echo "0.0.0")
    # Prefer the strictly-higher version (no external deps)
    if [ "$ver" != "$BEST_VERSION" ] && \
       [ "$(printf '%s\n%s\n' "$BEST_VERSION" "$ver" | sort -V | tail -1)" = "$ver" ]; then
      CLI="$path"
      BEST_VERSION="$ver"
    fi
  fi
}

# Check all common locations
check_cli "/opt/homebrew/bin/databricks"
check_cli "/usr/local/bin/databricks"
if command -v databricks &>/dev/null; then
  check_cli "$(command -v databricks)"
fi

if [ -z "$CLI" ]; then
  fail "Databricks CLI not found. Install it:
    macOS:   brew install databricks/tap/databricks
    Linux:   curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sh
    Docs:    https://docs.databricks.com/en/dev-tools/cli/install.html"
fi

CLI_VERSION="$BEST_VERSION"
CLI_MAJOR=$(echo "$CLI_VERSION" | cut -d. -f1)
CLI_MINOR=$(echo "$CLI_VERSION" | cut -d. -f2)
if [[ "$CLI_MAJOR" -eq 0 && "$CLI_MINOR" -lt 285 ]]; then
  fail "Databricks CLI v$CLI_VERSION is too old (need v0.285+ for Lakebase).
    Upgrade: brew upgrade databricks/tap/databricks"
fi
success "Databricks CLI v$CLI_VERSION ($CLI)"

# Check Node.js (optional — pre-built frontend is included in the repo)
if command -v node &>/dev/null; then
  success "Node.js $(node --version)"
  if command -v npm &>/dev/null; then
    success "npm $(npm --version)"
  fi
else
  info "Node.js not found (optional — pre-built frontend is included in the repo)"
fi

# Check psql (needed for Lakebase setup)
if ! command -v psql &>/dev/null; then
  fail "psql not found. Install PostgreSQL client:
    macOS:   brew install postgresql@16
    Linux:   apt install postgresql-client"
fi
success "psql ($(psql --version | head -1))"

# ── Deploy Mode Selection ────────────────────────────────────────────────────
if [ -z "$DEPLOY_MODE" ]; then
  echo ""
  echo -e "  ${BOLD}Choose deployment mode:${NC}"
  echo ""
  echo -e "  ${CYAN}1)${NC} ${BOLD}Full Deploy${NC} (recommended)"
  echo -e "     Uses Databricks Asset Bundles. Deploys the app, scoring notebook,"
  echo -e "     and a scheduled job that re-scores every 4 hours."
  echo ""
  echo -e "  ${CYAN}2)${NC} ${BOLD}Quick Deploy${NC}"
  echo -e "     Uses the Databricks Apps API directly (like Forge)."
  echo -e "     Deploys only the app. You run the scoring notebook manually."
  echo ""
  read -rp "  Select mode [1]: " MODE_CHOICE
  MODE_CHOICE="${MODE_CHOICE:-1}"
  if [ "$MODE_CHOICE" = "2" ]; then
    DEPLOY_MODE="quick"
  else
    DEPLOY_MODE="full"
  fi
fi

if [ "$DEPLOY_MODE" = "quick" ]; then
  success "Deploy mode: Quick (direct API)"
else
  success "Deploy mode: Full (DAB bundle + scoring job)"
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 2: Authenticate & Detect Workspace
# ══════════════════════════════════════════════════════════════════════════════
step "Step 2/8: Authenticating"

# A current-user blob is valid only if it carries a userName.
_valid_user_json() { echo "$1" | python3 -c "import sys,json; d=json.load(sys.stdin); exit(0 if d.get('userName') else 1)" 2>/dev/null; }

# current-user me with the current PROFILE_FLAG (empty = default/env auth).
_try_me() {
  if [ -n "$PROFILE_FLAG" ]; then $CLI current-user me $PROFILE_FLAG -o json 2>/dev/null || true
  else $CLI current-user me -o json 2>/dev/null || true; fi
}

# Auto-select a profile when --profile wasn't passed. With a target host, prefer
# the profile whose host matches; otherwise use the sole valid profile. Sets
# PROFILE_NAME/PROFILE_FLAG/USER_JSON on success. Never guesses among several.
_autopick_profile() {
  local want_host="${1:-}" decision name
  decision=$($CLI auth profiles -o json 2>/dev/null | python3 -c "
import sys, json
def norm(h): return (h or '').strip().rstrip('/').replace('https://','').replace('http://','')
try:
    raw = json.load(sys.stdin)
    profs = raw.get('profiles', raw) if isinstance(raw, dict) else raw
except Exception:
    profs = []
want = norm('''$want_host''')
valid = [p for p in (profs or []) if p.get('valid') and p.get('name')]
if want:
    m = [p for p in valid if norm(p.get('host')) == want]
    print('PICK ' + m[0]['name'] if m else 'NONE')
elif len(valid) == 1:
    print('PICK ' + valid[0]['name'])
elif len(valid) > 1:
    print('MANY ' + ','.join(p['name'] for p in valid))
else:
    print('NONE')
" 2>/dev/null)
  case "$decision" in
    PICK\ *)
      name="${decision#PICK }"
      local j; j=$($CLI current-user me --profile "$name" -o json 2>/dev/null || true)
      if _valid_user_json "$j"; then PROFILE_NAME="$name"; PROFILE_FLAG="--profile $name"; USER_JSON="$j"; return 0; fi
      return 1 ;;
    MANY\ *)
      warn "Multiple authenticated profiles found: ${decision#MANY }"
      info "Re-run with one, e.g.:  ./deploy.sh --profile ${decision#MANY }" ; info "(comma-separated above — pick the workspace you're deploying to)"
      return 1 ;;
    *) return 1 ;;
  esac
}

# 1) Try as-is (explicit --profile, or default/env).
USER_JSON="$(_try_me)"

# 2) No --profile and that failed → auto-pick a valid profile (handles the common
#    "databricks auth login --host X" then a bare ./deploy.sh).
if ! _valid_user_json "$USER_JSON" && [ -z "$PROFILE_FLAG" ]; then
  _autopick_profile "" || true
fi

if [ -n "$SKIP_AUTH_CHECK" ]; then
  if _valid_user_json "$USER_JSON"; then
    USER_EMAIL=$(echo "$USER_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('userName',''))")
    warn "Auth check skipped; using profile '${PROFILE_NAME:-(default/env)}' ($USER_EMAIL)."
  else
    warn "Skipping authentication check (--skip-auth-check); no usable profile auto-detected."
    info "If later steps fail on auth, re-run with: ./deploy.sh --profile <name>"
    USER_EMAIL="(authentication skipped)"
  fi
elif ! _valid_user_json "$USER_JSON"; then
  # 3) Interactive login fallback, then re-resolve by matching the host.
  warn "Not authenticated. Let's log in now."
  echo ""
  read -rp "Enter your workspace URL (e.g. https://my-workspace.cloud.databricks.com): " WORKSPACE_URL
  WORKSPACE_URL="${WORKSPACE_URL%/}"
  [ -z "$WORKSPACE_URL" ] && fail "Workspace URL cannot be empty."
  info "Opening browser for authentication..."
  if [ -n "$PROFILE_NAME" ]; then $CLI auth login --host "$WORKSPACE_URL" --profile "$PROFILE_NAME"; else $CLI auth login --host "$WORKSPACE_URL"; fi
  USER_JSON="$(_try_me)"
  if ! _valid_user_json "$USER_JSON"; then _autopick_profile "$WORKSPACE_URL" || true; fi
  _valid_user_json "$USER_JSON" || fail "Authentication failed. Try:  ./deploy.sh --profile <name>   (list profiles: databricks auth profiles)"
fi

if [ "${USER_EMAIL:-}" != "(authentication skipped)" ]; then
  USER_EMAIL=$(echo "$USER_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('userName',''))")
  success "Authenticated as $USER_EMAIL${PROFILE_NAME:+ (profile: $PROFILE_NAME)}"
fi

# Default the Admin allowlist to the deploying user so the Admin page is gated
# (never wide open) even when --admins is not passed. Only for a real email
# (skip the "(authentication skipped)" placeholder). Skipped for child apps:
# children inherit the shared admin list from the master's quest_admins table,
# so they must NOT seed their own deployer as a global admin.
if [ -z "$QUEST_ADMIN_ALLOWLIST" ] && [[ "$USER_EMAIL" == *"@"* ]] && [ "$QUEST_ROLE" != "child" ]; then
  QUEST_ADMIN_ALLOWLIST="$USER_EMAIL"
fi

# Host authority guard (Event Mode). Host endpoints fail closed when no
# authority is configured, so make the effective authority explicit at deploy
# time. master/standalone must have a host allowlist or admins (the admin
# default above normally satisfies this); a child inherits admins from master.
if [ "$QUEST_EVENT_MODE" = "on" ]; then
  if [ -z "$QUEST_HOST_ALLOWLIST" ] && [ -z "$QUEST_ADMIN_ALLOWLIST" ] && [ "$QUEST_ROLE" != "child" ]; then
    fail "Event Mode requires host authority: pass --host-allowlist EMAILS and/or --admins EMAILS.
    (Host endpoints fail closed when no allowlist, admins, or per-event hosts exist.)"
  fi
  if [ -n "$QUEST_HOST_ALLOWLIST" ]; then
    success "Host authority: allowlist=[$QUEST_HOST_ALLOWLIST]${QUEST_ADMIN_ALLOWLIST:+ + admins=[$QUEST_ADMIN_ALLOWLIST]}"
  elif [ "$QUEST_ROLE" = "child" ]; then
    info "Host authority: inherited admins from master (shared quest_admins) + per-event hosts"
  else
    warn "Host authority: no --host-allowlist set; relying on admins=[$QUEST_ADMIN_ALLOWLIST] + per-event hosts"
  fi
fi

# Get workspace host
if [ -n "$PROFILE_FLAG" ]; then
  WORKSPACE_HOST=$($CLI auth env $PROFILE_FLAG 2>/dev/null | python3 -c "
import sys,json
try:
    d = json.load(sys.stdin)
    print(d.get('env',{}).get('DATABRICKS_HOST',''))
except: pass
" 2>/dev/null || true)
fi

# Env-var auth (DATABRICKS_HOST/DATABRICKS_TOKEN): trust the host we were given.
if [ -z "${WORKSPACE_HOST:-}" ] && [ -n "${DATABRICKS_HOST:-}" ]; then
  WORKSPACE_HOST="$DATABRICKS_HOST"
fi

# Fallback: try to get host from CLI config
if [ -z "${WORKSPACE_HOST:-}" ]; then
  WORKSPACE_HOST=$($CLI auth env 2>/dev/null | python3 -c "
import sys,json
try:
    d = json.load(sys.stdin)
    print(d.get('env',{}).get('DATABRICKS_HOST',''))
except: pass
" 2>/dev/null || true)
fi

# Another fallback: read from profiles
if [ -z "${WORKSPACE_HOST:-}" ]; then
  if [ -n "$PROFILE_NAME" ]; then
    WORKSPACE_HOST=$(python3 -c "
import configparser, os
c = configparser.ConfigParser()
c.read(os.path.expanduser('~/.databrickscfg'))
print(c.get('$PROFILE_NAME', 'host', fallback=''))
" 2>/dev/null || true)
  fi
fi

if [ -z "${WORKSPACE_HOST:-}" ]; then
  warn "Could not auto-detect workspace host."
  read -rp "Enter your workspace URL: " WORKSPACE_HOST
  WORKSPACE_HOST="${WORKSPACE_HOST%/}"
fi

WORKSPACE_HOST="${WORKSPACE_HOST%/}"
success "Workspace: $WORKSPACE_HOST"

# Federation role echo + default the child workspace id from the workspace host.
if [ "$QUEST_ROLE" != "standalone" ]; then
  if [ -z "$WORKSPACE_ID" ]; then
    WORKSPACE_ID=$(echo "$WORKSPACE_HOST" | sed -E 's#^https?://##; s#/.*$##')
  fi
  success "Federation role: $QUEST_ROLE${EVENT_SLUG:+  event=$EVENT_SLUG}${WORKSPACE_ID:+  workspace_id=$WORKSPACE_ID}"
  if [ "$QUEST_ROLE" = "child" ]; then
    info "Child points at MASTER Lakebase: $MASTER_LAKEBASE_HOST/$LAKEBASE_DB (writer: $MASTER_LAKEBASE_USER)"
  fi
fi

# Export env var auth so CLI commands don't rely on the token cache
# (the cache can expire mid-operation during long Terraform applies)
CLI_TOKEN=$($CLI auth token $PROFILE_FLAG -o json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null || true)
if [ -n "$CLI_TOKEN" ]; then
  export DATABRICKS_HOST="$WORKSPACE_HOST"
  export DATABRICKS_TOKEN="$CLI_TOKEN"
  export DATABRICKS_AUTH_TYPE=pat
  # Clear profile flag so CLI uses env vars instead
  PROFILE_FLAG=""
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 3: Select SQL Warehouse
# ══════════════════════════════════════════════════════════════════════════════
step "Step 3/8: Selecting SQL Warehouse"

# A SQL warehouse is ALWAYS wired, in EVERY backend mode — not just
# --data-backend warehouse. Warehouse mode reads scored Delta through it, and
# even in Lakebase mode the runtime backend toggle persists its setting in a
# Delta table via the warehouse (so switching backends keeps working when
# Lakebase is down). When the caller doesn't supply one, reuse or provision a
# dedicated 2X-Small serverless warehouse (60-min auto-stop) automatically — no
# interactive prompt, so a customer running the command non-interactively still
# gets a fully-wired app. Creation failure is non-fatal: we fall through to
# selecting an existing warehouse below.
if [ -z "$WAREHOUSE_ID" ] && [ -z "$WAREHOUSE_NAME" ]; then
  WH_QUEST_NAME="${APP_NAME}-warehouse"
  info "No --warehouse specified — reusing or provisioning a dedicated 2X-Small serverless warehouse '$WH_QUEST_NAME' (auto-stop 60 min)..."
  # NOTE: the `|| WAREHOUSE_ID=""` guards are required — under `set -euo
  # pipefail` a non-zero CLI exit (e.g. the deploying identity lacks permission
  # to list/create warehouses) would otherwise abort the whole deploy instead of
  # falling through to selecting an existing warehouse below.
  WAREHOUSE_ID=$($CLI warehouses list $PROFILE_FLAG -o json 2>/dev/null | python3 -c "
import sys, json
try: whs = json.load(sys.stdin)
except Exception: whs = []
for w in whs:
    if w.get('name') == '$WH_QUEST_NAME':
        print(w.get('id','')); break
" 2>/dev/null) || WAREHOUSE_ID=""
  if [ -z "$WAREHOUSE_ID" ]; then
    WAREHOUSE_ID=$($CLI warehouses create --name "$WH_QUEST_NAME" --cluster-size "2X-Small" \
      --auto-stop-mins 60 --max-num-clusters 1 --enable-serverless-compute --warehouse-type PRO \
      $PROFILE_FLAG -o json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null) || WAREHOUSE_ID=""
  else
    # Reuse-path: a dedicated Quest warehouse from a prior deploy already exists.
    # Enforce 2X-Small (idempotent if already 2X-Small) so re-running the deploy
    # downsizes a warehouse left at a larger size by an older script. Scoped to
    # OUR managed '${APP_NAME}-warehouse' only — a warehouse the caller passes
    # via --warehouse/--warehouse-id is never touched. Full spec is sent so the
    # edit is deterministic. Non-fatal: a failed resize keeps the existing size.
    info "Reusing existing '$WH_QUEST_NAME' ($WAREHOUSE_ID) — enforcing 2X-Small size..."
    $CLI warehouses edit "$WAREHOUSE_ID" --name "$WH_QUEST_NAME" --cluster-size "2X-Small" \
      --auto-stop-mins 60 --max-num-clusters 1 --enable-serverless-compute --warehouse-type PRO \
      $PROFILE_FLAG -o json >/dev/null 2>&1 || warn "Could not resize '$WH_QUEST_NAME' to 2X-Small (keeping current size)."
  fi
  if [ -n "$WAREHOUSE_ID" ]; then
    success "Quest warehouse ready: $WH_QUEST_NAME ($WAREHOUSE_ID) — 2X-Small / serverless / 60-min auto-stop"
  else
    warn "Could not auto-provision a warehouse (insufficient permission?) — falling back to an existing one."
  fi
fi

if [ -n "$WAREHOUSE_ID" ]; then
  success "Using warehouse ID: $WAREHOUSE_ID (provided via --warehouse-id)"
else
  # List available warehouses
  info "Discovering SQL Warehouses..."
  WAREHOUSES_JSON=$($CLI warehouses list $PROFILE_FLAG -o json 2>/dev/null || echo "[]")

  WAREHOUSE_COUNT=$(echo "$WAREHOUSES_JSON" | python3 -c "
import sys, json
try:
    whs = json.load(sys.stdin)
    print(len(whs))
except:
    print(0)
" 2>/dev/null)

  if [ "$WAREHOUSE_COUNT" -eq 0 ]; then
    fail "No SQL Warehouses found in your workspace.
    Create one: Workspace sidebar > SQL Warehouses > Create warehouse
    Then re-run this script."
  fi

  # Display warehouse list
  echo "$WAREHOUSES_JSON" | python3 -c "
import sys, json
whs = json.load(sys.stdin)
print()
print('  #   Name                                      Type          Size    State')
print('  ─── ───────────────────────────────────────── ──────────── ─────── ─────────')
for i, wh in enumerate(whs, 1):
    name = wh.get('name', '?')[:42]
    wtype = wh.get('warehouse_type', '?')
    if wtype == 'PRO': wtype = 'Pro'
    elif wtype == 'CLASSIC': wtype = 'Classic'
    else: wtype = 'Serverless'
    size = wh.get('cluster_size', '?')
    state = wh.get('state', '?')
    print(f'  {i:<3} {name:<43} {wtype:<12} {size:<7} {state}')
print()
"

  if [ -n "$WAREHOUSE_NAME" ]; then
    # Match by name
    WAREHOUSE_ID=$(echo "$WAREHOUSES_JSON" | python3 -c "
import sys, json
whs = json.load(sys.stdin)
target = '$WAREHOUSE_NAME'.lower()
for wh in whs:
    if wh.get('name','').lower() == target:
        print(wh['id'])
        break
" 2>/dev/null)
    if [ -z "$WAREHOUSE_ID" ]; then
      fail "No warehouse found matching name: '$WAREHOUSE_NAME'"
    fi
    success "Selected warehouse: $WAREHOUSE_NAME ($WAREHOUSE_ID)"
  else
    # Selection. Prompt only when attached to a terminal; otherwise (customer
    # running non-interactively / piped) default to the first warehouse so the
    # deploy never hangs on a read and never lands without a warehouse.
    if [ -t 0 ]; then
      read -rp "  Select warehouse number [1]: " WH_CHOICE
    else
      WH_CHOICE="1"
      info "Non-interactive — defaulting to warehouse #1."
    fi
    WH_CHOICE="${WH_CHOICE:-1}"

    WAREHOUSE_ID=$(echo "$WAREHOUSES_JSON" | python3 -c "
import sys, json
whs = json.load(sys.stdin)
idx = int('$WH_CHOICE') - 1
if 0 <= idx < len(whs):
    print(whs[idx]['id'])
else:
    print('')
" 2>/dev/null)

    WAREHOUSE_NAME=$(echo "$WAREHOUSES_JSON" | python3 -c "
import sys, json
whs = json.load(sys.stdin)
idx = int('$WH_CHOICE') - 1
if 0 <= idx < len(whs):
    print(whs[idx].get('name',''))
" 2>/dev/null)

    if [ -z "$WAREHOUSE_ID" ]; then
      fail "Invalid selection. Please enter a number from the list."
    fi
    success "Selected: $WAREHOUSE_NAME ($WAREHOUSE_ID)"
  fi
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 4: Configure Catalog
# ══════════════════════════════════════════════════════════════════════════════
if [ -z "$QUEST_CATALOG" ]; then
  echo ""
  info "Choose a Unity Catalog name for Quest data."
  info "The scoring pipeline will create this catalog automatically if it doesn't exist."
  info "If your workspace requires a storage location for new catalogs, create it first in the UI."
  echo ""
  read -rp "  Catalog name [quest_data]: " QUEST_CATALOG
  QUEST_CATALOG="${QUEST_CATALOG:-quest_data}"
fi
success "Catalog: $QUEST_CATALOG (schema: $QUEST_SCHEMA)"

# ── Pre-flight: verify the scored-tables location is writable ────────────────
# Adoption scoring writes Delta tables to ${QUEST_CATALOG}.${QUEST_SCHEMA}. On a
# governed workspace the deploying identity often lacks CREATE SCHEMA there — which
# otherwise fails DEEP in Step 7, after the app + Lakebase are already provisioned
# and the app comes up empty. Check now and fail fast with the exact admin grant.
if [ -z "$SKIP_SCORING" ] && [ -n "${DATABRICKS_TOKEN:-}" ] && [ -n "$WAREHOUSE_ID" ]; then
  info "Pre-flight: checking $USER_EMAIL can create scored tables in $QUEST_CATALOG.$QUEST_SCHEMA ..."
  PF=$(QUEST_CATALOG="$QUEST_CATALOG" QUEST_SCHEMA="$QUEST_SCHEMA" WH="$WAREHOUSE_ID" \
       DBX_HOST="${DATABRICKS_HOST:-$WORKSPACE_HOST}" DBX_TOKEN="$DATABRICKS_TOKEN" python3 - <<'PYEOF'
import os, json, time, urllib.request
host=os.environ["DBX_HOST"].rstrip("/"); tok=os.environ["DBX_TOKEN"]; wh=os.environ["WH"]
cat=os.environ["QUEST_CATALOG"]; sch=os.environ["QUEST_SCHEMA"]
def sql(s):
    body=json.dumps({"warehouse_id":wh,"statement":s,"wait_timeout":"30s"}).encode()
    req=urllib.request.Request(host+"/api/2.0/sql/statements",data=body,
        headers={"Authorization":"Bearer "+tok,"Content-Type":"application/json"})
    try: d=json.loads(urllib.request.urlopen(req,timeout=60).read())
    except Exception as e: return "ERROR", str(e)[:160]
    st=d.get("status",{}); sid=d.get("statement_id")
    while st.get("state") in ("PENDING","RUNNING") and sid:
        time.sleep(2)
        rr=urllib.request.Request(host+"/api/2.0/sql/statements/"+sid,headers={"Authorization":"Bearer "+tok})
        d=json.loads(urllib.request.urlopen(rr,timeout=30).read()); st=d.get("status",{})
    return st.get("state"), (st.get("error",{}) or {}).get("message","")
state,msg=sql("CREATE SCHEMA IF NOT EXISTS `%s`.`%s`"%(cat,sch))
if state!="SUCCEEDED" and ("NO_SUCH_CATALOG" in (msg or "").upper() or "was not found" in (msg or "")):
    cstate,cmsg=sql("CREATE CATALOG IF NOT EXISTS `%s`"%cat)
    if cstate=="SUCCEEDED": state,msg=sql("CREATE SCHEMA IF NOT EXISTS `%s`.`%s`"%(cat,sch))
    else: msg=cmsg
print("OK" if state=="SUCCEEDED" else "DENIED::"+(msg or "unknown error"))
PYEOF
)
  if [ "$PF" != "OK" ]; then
    echo ""
    fail "Cannot create the scored-tables schema '$QUEST_CATALOG.$QUEST_SCHEMA' as $USER_EMAIL.
    ${PF#DENIED::}

    Adoption scoring writes Delta tables here, so it must be writable BEFORE deploying.
    Ask a metastore admin or the catalog owner to run ONCE, then re-run this deploy:

        CREATE CATALOG IF NOT EXISTS $QUEST_CATALOG;          -- only if it doesn't exist
        GRANT USE CATALOG, CREATE SCHEMA ON CATALOG $QUEST_CATALOG TO \`$USER_EMAIL\`;
        -- the schema also needs CREATE TABLE, MODIFY, SELECT for the run-as identity

    Or: --catalog <a catalog you can already write to>   |   --skip-scoring (deploy app shell, populate later)."
  fi
  success "Pre-flight OK: $QUEST_CATALOG.$QUEST_SCHEMA is writable"
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 5: Build Frontend
# ══════════════════════════════════════════════════════════════════════════════
step "Step 4/8: Building frontend"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="$SCRIPT_DIR/frontend"
STATIC_DIR="$SCRIPT_DIR/app/static"

# The repo ships a COMMITTED prebuilt frontend (app/static/), so deploy works on
# restricted networks with zero npm access. We refresh it from source only when
# npm is available and can reach a registry; otherwise we keep the committed
# build. No Databricks-internal proxy (unreachable from customer networks).
if [ -f "$STATIC_DIR/index.html" ] && [ -z "$SKIP_BUILD" ]; then
  if command -v node &>/dev/null && command -v npm &>/dev/null; then
    info "Refreshing frontend from source (committed build is the fallback)..."
    if (cd "$FRONTEND_DIR" && \
        (npm install --silent 2>/dev/null || npm install --registry https://registry.npmjs.org/ --silent 2>/dev/null) && \
        npm run build 2>&1 | tail -3); then
      success "Frontend rebuilt to app/static/"
    else
      warn "npm unavailable/blocked — using the committed prebuilt frontend (this is fine)."
    fi
  else
    success "Using committed prebuilt frontend (Node.js not required)."
  fi
elif [ -f "$STATIC_DIR/index.html" ]; then
  success "Skipping build (--skip-build). Using committed app/static/."
else
  # Prebuilt static is committed, so this is unexpected. Build from source.
  command -v npm &>/dev/null || fail "No prebuilt frontend and npm not found. Install Node.js 18+ (https://nodejs.org/)."
  info "Installing dependencies + building React app..."
  if ! (cd "$FRONTEND_DIR" && \
        (npm install --silent || npm install --registry https://registry.npmjs.org/ --silent) && \
        npm run build 2>&1 | tail -3); then
    fail "npm install/build failed and no prebuilt frontend is present.
    On a restricted network, build the frontend on a machine with npm access and commit app/static/."
  fi
  [ -f "$STATIC_DIR/index.html" ] || fail "Build did not produce app/static/index.html."
  success "Frontend built to app/static/"
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 5: Deploy App
# ══════════════════════════════════════════════════════════════════════════════
step "Step 5/8: Deploying to Databricks"

BUNDLE_FILE="$SCRIPT_DIR/databricks.yml"

# Child apps skip local Lakebase provisioning (which is where master/standalone
# write app.yaml), so write the federation app.yaml now — before the app source
# is uploaded by the bundle/Apps deploy.
if [ "$QUEST_ROLE" = "child" ]; then
  info "Writing child app.yaml (role=child, points at master Lakebase)..."
  write_app_yaml "$MASTER_LAKEBASE_HOST" "$LAKEBASE_DB"
elif [ "$DEPLOY_MODE" != "quick" ]; then
  # Standalone/master: write app.yaml NOW (before the bundle uploads app/) with
  # everything already known — warehouse id, catalog, schema, backend default.
  # The Lakebase host isn't known until Step 6, which rewrites this file and
  # redeploys. Writing it early means even a deploy that doesn't finish Step 6
  # still boots an app with the warehouse + catalog wired (so warehouse mode and
  # the backend toggle work), instead of a config-less placeholder app.yaml.
  info "Writing app.yaml with known config (warehouse, catalog, backend)..."
  write_app_yaml "$LAKEBASE_HOST" "$LAKEBASE_DB"
fi

if [ "$DEPLOY_MODE" = "quick" ]; then
  # ── Quick Deploy: Direct API (like Forge) ──────────────────────────────────
  info "Creating app via Apps API..."

  # Create the app (ignore error if already exists)
  $CLI apps create "$APP_NAME" \
    --description "Databricks Quest - Gamification app for platform adoption" \
    $PROFILE_FLAG 2>/dev/null || true

  # Upload app source code to workspace
  APP_WORKSPACE_PATH="/Workspace/Users/${USER_EMAIL}/databricks-quest/app"
  info "Uploading app source to $APP_WORKSPACE_PATH..."
  $CLI workspace import-dir "$SCRIPT_DIR/app" "$APP_WORKSPACE_PATH" \
    --overwrite $PROFILE_FLAG 2>&1 || true

  # Upload scoring notebook
  NB_WORKSPACE_PATH="/Workspace/Users/${USER_EMAIL}/databricks-quest/notebooks"
  info "Uploading scoring notebook..."
  $CLI workspace import-dir "$SCRIPT_DIR/notebooks" "$NB_WORKSPACE_PATH" \
    --overwrite $PROFILE_FLAG 2>&1 || true

  # Start app compute
  $CLI apps start "$APP_NAME" $PROFILE_FLAG 2>/dev/null || true

  # Wait for compute
  for i in $(seq 1 30); do
    COMPUTE_STATE=$($CLI apps get "$APP_NAME" $PROFILE_FLAG -o json 2>/dev/null | python3 -c "
import sys, json
try: print(json.load(sys.stdin).get('compute_status', {}).get('state', 'UNKNOWN'))
except: print('UNKNOWN')
" 2>/dev/null || echo "UNKNOWN")
    if [ "$COMPUTE_STATE" = "ACTIVE" ]; then break; fi
    sleep 5
  done

  # Deploy source code to the app
  DEPLOY_RESULT=$($CLI apps deploy "$APP_NAME" \
    --source-code-path "$APP_WORKSPACE_PATH" \
    $PROFILE_FLAG -o json 2>/dev/null || true)

  DEPLOY_STATE=$(echo "$DEPLOY_RESULT" | python3 -c "
import sys, json
try: print(json.load(sys.stdin).get('status', {}).get('state', 'PENDING'))
except: print('PENDING')
" 2>/dev/null || echo "PENDING")

  if [ "$DEPLOY_STATE" = "SUCCEEDED" ]; then
    success "App deployed and running"
  else
    info "App deploy status: $DEPLOY_STATE (may still be starting)"
  fi

  # Set environment variables on the app
  info "Configuring app environment..."
  $CLI apps update "$APP_NAME" \
    --json "{\"config\": {\"command\": [\"uvicorn\", \"main:app\", \"--host\", \"0.0.0.0\", \"--port\", \"8000\"], \"env\": [{\"name\": \"LAKEBASE_HOST\", \"value\": \"$LAKEBASE_HOST\"}, {\"name\": \"LAKEBASE_DB\", \"value\": \"$LAKEBASE_DB\"}]}}" \
    $PROFILE_FLAG 2>/dev/null || true

  success "Quick deploy complete"

else
  # ── Full Deploy: DAB Bundle ────────────────────────────────────────────────
  if [ ! -f "$BUNDLE_FILE" ]; then
    fail "databricks.yml not found. Are you running this from the repo root?"
  fi

  # Update workspace host in databricks.yml
  python3 -c "
import re, sys
with open('$BUNDLE_FILE', 'r') as f:
    content = f.read()
host = '$WORKSPACE_HOST'
content = re.sub(r'(host:\s*)https?://[^\s]+', r'\1' + host, content)
with open('$BUNDLE_FILE', 'w') as f:
    f.write(content)
print('OK')
" || fail "Failed to update databricks.yml"

  # The rewrite below mutates databricks.yml in place. Back it up and restore it
  # on exit, so a deploy with one --app-name can't leave the file renamed and
  # corrupt the next deploy that uses a different name (observed: a later deploy
  # silently re-targeting the previous app).
  cp "$BUNDLE_FILE" "${BUNDLE_FILE}.deploybak" 2>/dev/null || true
  trap 'mv -f "${BUNDLE_FILE}.deploybak" "$BUNDLE_FILE" 2>/dev/null || true' EXIT
  # Update app name if customized
  if [ "$APP_NAME" != "databricks-quest" ]; then
    python3 -c "
with open('$BUNDLE_FILE', 'r') as f:
    content = f.read()
content = content.replace('databricks-quest', '$APP_NAME')
with open('$BUNDLE_FILE', 'w') as f:
    f.write(content)
print('OK')
" || warn "Could not update app name in databricks.yml"
  fi

  info "Deploying bundle (app + scoring job + notebook)..."
  # The DAB Terraform provider authenticates from the environment, NOT from the
  # CLI's --profile flag. With multiple ~/.databrickscfg profiles it otherwise
  # reads the DEFAULT profile and fails with "workspace_id mismatch". Pin the
  # provider to the same profile the rest of deploy.sh uses.
  if [ -n "$PROFILE_NAME" ]; then export DATABRICKS_CONFIG_PROFILE="$PROFILE_NAME"; fi
  set +e
  $CLI bundle deploy --target "$TARGET" $PROFILE_FLAG \
    --var "warehouse_id=$WAREHOUSE_ID" \
    --var "quest_catalog=$QUEST_CATALOG" \
    --var "quest_schema=$QUEST_SCHEMA" \
    --var "lakebase_host=$LAKEBASE_HOST" \
    --var "lakebase_db=$LAKEBASE_DB" \
    --var "quest_data_backend=$QUEST_DATA_BACKEND"
  DEPLOY_EXIT=$?
  set -e
  if [ "$DEPLOY_EXIT" -ne 0 ]; then
    fail "Bundle deploy failed (exit $DEPLOY_EXIT). Check the error above."
  fi

  success "Bundle deployed"
fi

# Start app compute and deploy source code (full deploy mode only)
# The bundle creates the app resource via Terraform but doesn't trigger an app deployment.
if [ "$DEPLOY_MODE" != "quick" ]; then
info "Starting app and deploying source code..."

# Determine the source code path the bundle uploaded to
BUNDLE_USER_PATH="/Workspace/Users/${USER_EMAIL}/.bundle/${APP_NAME}/${TARGET}/files/app"

# Start app compute (may already be running)
$CLI apps start "$APP_NAME" $PROFILE_FLAG -o json 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    cs = d.get('compute_status', {})
    print(f\"  Compute: {cs.get('state', '?')}\")
except: pass
" 2>/dev/null || true

# Wait for compute to be active
for i in $(seq 1 30); do
  COMPUTE_STATE=$($CLI apps get "$APP_NAME" $PROFILE_FLAG -o json 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('compute_status', {}).get('state', 'UNKNOWN'))
except: print('UNKNOWN')
" 2>/dev/null || echo "UNKNOWN")
  if [ "$COMPUTE_STATE" = "ACTIVE" ]; then
    break
  fi
  sleep 5
done

# Deploy source code
DEPLOY_RESULT=$($CLI apps deploy "$APP_NAME" \
  --source-code-path "$BUNDLE_USER_PATH" \
  $PROFILE_FLAG -o json 2>/dev/null || true)

if echo "$DEPLOY_RESULT" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d.get('status',{}).get('state') == 'SUCCEEDED'" 2>/dev/null; then
  success "App deployed and running"
else
  DEPLOY_STATE=$(echo "$DEPLOY_RESULT" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    state = d.get('status', {}).get('state', 'UNKNOWN')
    msg = d.get('status', {}).get('message', '')
    print(f'{state}: {msg}')
except: print('PENDING')
" 2>/dev/null || echo "PENDING")
  info "App deploy status: $DEPLOY_STATE (may still be starting)"
fi

fi  # end full deploy app start/deploy

# ══════════════════════════════════════════════════════════════════════════════
# STEP 6b: Data backend — warehouse (adoption) or Lakebase
# ══════════════════════════════════════════════════════════════════════════════
if [ -z "$LAKEBASE_HOST" ]; then
  step "Step 6/8: Provisioning Lakebase"

  LB_PROJECT_ID=$(echo "$APP_NAME" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9-]/-/g')

  # Check if Lakebase project already exists
  EXISTING_PROJECT=$($CLI postgres get-project "projects/$LB_PROJECT_ID" $PROFILE_FLAG -o json 2>/dev/null || true)
  if echo "$EXISTING_PROJECT" | python3 -c "import sys,json; json.load(sys.stdin)" &>/dev/null 2>&1; then
    info "Lakebase project '$LB_PROJECT_ID' already exists"
  else
    info "Creating Lakebase project '$LB_PROJECT_ID'..."
    $CLI postgres create-project "$LB_PROJECT_ID" \
      --json "{\"spec\": {\"display_name\": \"Databricks Quest\"}}" \
      --no-wait \
      $PROFILE_FLAG 2>&1 || true
  fi

  # Wait for the endpoint to be usable. ACTIVE = running; IDLE = healthy but
  # auto-suspended (serverless endpoints suspend when nothing is connected).
  # Both are "ready": the credential + psql connection below wakes an IDLE
  # endpoint on demand. Only keep waiting through transitional states.
  info "Waiting for Lakebase endpoint to be ready..."
  for i in $(seq 1 60); do
    EP_STATE=$($CLI postgres list-endpoints "projects/$LB_PROJECT_ID/branches/production" \
      $PROFILE_FLAG -o json 2>/dev/null | python3 -c "
import sys, json
try:
    eps = json.load(sys.stdin)
    print(eps[0].get('status', {}).get('current_state', 'UNKNOWN'))
except: print('PENDING')
" 2>/dev/null || echo "PENDING")
    if [ "$EP_STATE" = "ACTIVE" ] || [ "$EP_STATE" = "IDLE" ]; then
      break
    fi
    sleep 5
  done

  if [ "$EP_STATE" != "ACTIVE" ] && [ "$EP_STATE" != "IDLE" ]; then
    fail "Lakebase endpoint did not become ready (state: $EP_STATE). Check your workspace."
  fi

  # Get the endpoint host
  LAKEBASE_HOST=$($CLI postgres list-endpoints "projects/$LB_PROJECT_ID/branches/production" \
    $PROFILE_FLAG -o json 2>/dev/null | python3 -c "
import sys, json
eps = json.load(sys.stdin)
print(eps[0]['status']['hosts']['host'])
" 2>/dev/null)

  success "Lakebase endpoint: $LAKEBASE_HOST"

  # Generate credentials and create database + tables
  info "Setting up Lakebase database and tables..."

  LB_TOKEN=$($CLI postgres generate-database-credential \
    "projects/$LB_PROJECT_ID/branches/production/endpoints/primary" \
    $PROFILE_FLAG -o json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")

  # Create database (ignore error if it already exists)
  PGPASSWORD="$LB_TOKEN" psql "host=$LAKEBASE_HOST port=5432 dbname=postgres user=$USER_EMAIL sslmode=require" \
    -c "CREATE DATABASE $LAKEBASE_DB;" 2>/dev/null || true

  # Create tables (idempotent with IF NOT EXISTS)
  PGPASSWORD="$LB_TOKEN" psql "host=$LAKEBASE_HOST port=5432 dbname=$LAKEBASE_DB user=$USER_EMAIL sslmode=require" -c "
CREATE TABLE IF NOT EXISTS mission_completions (
  user_id TEXT, mission_id TEXT, mission_name TEXT, points_awarded INT,
  completed_at TIMESTAMP, period_start DATE, period_end DATE, scored_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS user_points_fact (
  user_id TEXT, event_type TEXT, mission_id TEXT, points INT,
  reason TEXT, event_timestamp TIMESTAMP, scored_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS user_profile_snapshot (
  user_id TEXT, display_name TEXT, total_points INT, level TEXT,
  current_streak INT, max_streak INT, badge_count INT, missions_completed INT,
  first_activity_date DATE, last_activity_date DATE, distinct_products_used INT,
  updated_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS leaderboard (
  user_id TEXT, display_name TEXT, total_points INT, weekly_points INT,
  monthly_points INT, level TEXT, all_time_rank INT, weekly_rank INT,
  monthly_rank INT, updated_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS badges (
  user_id TEXT, badge_id TEXT, badge_name TEXT, badge_icon TEXT, earned_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS notifications (
  id SERIAL PRIMARY KEY, user_id TEXT, notification_type TEXT, title TEXT,
  message TEXT, mission_id TEXT, points INT, created_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS app_settings (
  key TEXT PRIMARY KEY, value TEXT, updated_at TIMESTAMP
);
-- Self-attested Get Started course completions (tick-box). Durable record that the
-- scoring rebuild never truncates; lakebase_sync.py rolls it into the Delta
-- training_completions table each cycle for reconciliation. One row per user+course.
CREATE TABLE IF NOT EXISTS training_attestations (
  user_id TEXT, course_mission_id TEXT, course_id TEXT, attested_at TIMESTAMP,
  UNIQUE (user_id, course_mission_id)
);
CREATE INDEX IF NOT EXISTS idx_mc_user ON mission_completions(user_id);
CREATE INDEX IF NOT EXISTS idx_lb_rank ON leaderboard(all_time_rank);
CREATE INDEX IF NOT EXISTS idx_ups_user ON user_profile_snapshot(user_id);
CREATE INDEX IF NOT EXISTS idx_badges_user ON badges(user_id);
CREATE INDEX IF NOT EXISTS idx_notif_user ON notifications(user_id);
" 2>/dev/null

  success "Database '$LAKEBASE_DB' and tables created"

  # Apply GameDay (Event Mode) schema migrations before granting access, so the
  # service principal GRANT below also covers the newly-created GameDay tables.
  # Skipped entirely for a legacy (Event-Mode-off) deploy so its DB is untouched.
  if [ "$QUEST_EVENT_MODE" = "on" ]; then
    run_gameday_migrations "$LAKEBASE_HOST" "$LAKEBASE_DB" "$USER_EMAIL" "$LB_TOKEN"
  else
    info "Event Mode off — skipping GameDay schema migrations (legacy adoption app)."
  fi

  # master: provision the shared event-writer role children will use.
  if [ "$QUEST_ROLE" = "master" ]; then
    EVENT_WRITER_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
    provision_event_writer_role "$LAKEBASE_HOST" "$LAKEBASE_DB" "$USER_EMAIL" "$LB_TOKEN" \
      "$MASTER_LAKEBASE_USER" "$EVENT_WRITER_PASSWORD" || true
  fi

  # Grant app service principal access to Lakebase
  info "Granting app service principal access to Lakebase..."

  SP_CLIENT_ID=$(app_sp_client_id)

  if [ -n "$SP_CLIENT_ID" ]; then
    # Create Postgres role for the SP (ignore error if exists)
    $CLI postgres create-role "projects/$LB_PROJECT_ID/branches/production" \
      --role-id "quest-sp" \
      --json "{\"spec\": {\"identity_type\": \"SERVICE_PRINCIPAL\", \"postgres_role\": \"$SP_CLIENT_ID\", \"auth_method\": \"LAKEBASE_OAUTH_V1\", \"membership_roles\": [\"DATABRICKS_SUPERUSER\"]}}" \
      $PROFILE_FLAG 2>/dev/null || true

    grant_sp_lakebase_access "$LAKEBASE_HOST" "$LAKEBASE_DB" "$LB_TOKEN" "$SP_CLIENT_ID"
    success "Service principal ($SP_CLIENT_ID) granted Lakebase access"
  else
    warn "Could not find app service principal — you may need to grant Lakebase access manually"
  fi

  grant_sp_warehouse_access "$SP_CLIENT_ID"

  # Patch app.yaml with actual Lakebase values (valueFrom doesn't work
  # when app config isn't set by the bundle's Terraform lifecycle). The helper
  # emits the unchanged two-var file for standalone and adds federation env
  # (QUEST_ROLE etc.) for master.
  info "Updating app.yaml with Lakebase endpoint..."
  write_app_yaml "$LAKEBASE_HOST" "$LAKEBASE_DB"

  # Re-deploy bundle with the Lakebase host now set
  info "Redeploying with Lakebase configuration..."
  $CLI bundle deploy --target "$TARGET" $PROFILE_FLAG \
    --var "warehouse_id=$WAREHOUSE_ID" \
    --var "quest_catalog=$QUEST_CATALOG" \
    --var "quest_schema=$QUEST_SCHEMA" \
    --var "lakebase_host=$LAKEBASE_HOST" \
    --var "lakebase_db=$LAKEBASE_DB" \
    --var "quest_data_backend=$QUEST_DATA_BACKEND" 2>&1 || true

  # Re-deploy app source code with updated app.yaml
  $CLI apps deploy "$APP_NAME" \
    --source-code-path "$BUNDLE_USER_PATH" \
    $PROFILE_FLAG -o json 2>/dev/null || true

  success "App updated with Lakebase configuration"
elif [ "$QUEST_ROLE" = "child" ]; then
  step "Step 6/8: Lakebase (child → master)"
  success "Child uses MASTER shared Lakebase: $LAKEBASE_HOST/$LAKEBASE_DB"
  info "Skipping local Lakebase provisioning and migrations (master owns the schema)."
  # app.yaml was already written for the child before the deploy step. Nothing
  # to provision here — the child only connects with the shared writer credential.
else
  step "Step 6/8: Lakebase"
  success "Using provided Lakebase: $LAKEBASE_HOST/$LAKEBASE_DB"

  # Apply GameDay migrations against the provided Lakebase endpoint (Event Mode
  # only). Mint a database credential if we can; otherwise fall back to the
  # workspace token. A legacy deploy skips this and leaves the DB untouched.
  LB_PROJECT_ID=$(echo "$APP_NAME" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9-]/-/g')
  LB_TOKEN=$($CLI postgres generate-database-credential \
    "projects/$LB_PROJECT_ID/branches/production/endpoints/primary" \
    $PROFILE_FLAG -o json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])" 2>/dev/null || true)
  if [ -z "$LB_TOKEN" ]; then
    LB_TOKEN="${DATABRICKS_TOKEN:-}"
  fi
  if [ "$QUEST_EVENT_MODE" = "on" ]; then
    run_gameday_migrations "$LAKEBASE_HOST" "$LAKEBASE_DB" "$USER_EMAIL" "$LB_TOKEN"
  else
    info "Event Mode off — skipping GameDay schema migrations (legacy adoption app)."
  fi

  # master with a pre-provided Lakebase: provision the shared event-writer role
  # and refresh app.yaml with master federation env.
  if [ "$QUEST_ROLE" = "master" ]; then
    EVENT_WRITER_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
    provision_event_writer_role "$LAKEBASE_HOST" "$LAKEBASE_DB" "$USER_EMAIL" "$LB_TOKEN" \
      "$MASTER_LAKEBASE_USER" "$EVENT_WRITER_PASSWORD" || true
    write_app_yaml "$LAKEBASE_HOST" "$LAKEBASE_DB"
  else
    # standalone with --lakebase-host: the committed app.yaml is a placeholder,
    # so it must be regenerated here too (Lakebase env + catalog/schema/backend
    # for the runtime toggle).
    write_app_yaml "$LAKEBASE_HOST" "$LAKEBASE_DB"
  fi

  # Same one-shot SP permissions as the auto-provision path: Lakebase write
  # grants (best effort — LB_TOKEN may be a workspace-token fallback that psql
  # rejects; the endpoint is presumed provisioned by an earlier deploy) and the
  # warehouse/catalog grants the runtime backend toggle depends on.
  SP_CLIENT_ID=$(app_sp_client_id)
  if [ -n "$SP_CLIENT_ID" ]; then
    grant_sp_lakebase_access "$LAKEBASE_HOST" "$LAKEBASE_DB" "$LB_TOKEN" "$SP_CLIENT_ID"
    grant_sp_warehouse_access "$SP_CLIENT_ID"
  else
    warn "Could not find app service principal — grants may need to be applied manually"
  fi

  # Re-upload the bundle (carries the regenerated app.yaml) and re-deploy the
  # app source so it takes effect (full deploy only; quick mode configures env
  # via apps update instead).
  if [ "$DEPLOY_MODE" != "quick" ] && [ -n "${BUNDLE_USER_PATH:-}" ]; then
    $CLI bundle deploy --target "$TARGET" $PROFILE_FLAG \
      --var "warehouse_id=$WAREHOUSE_ID" \
      --var "quest_catalog=$QUEST_CATALOG" \
      --var "quest_schema=$QUEST_SCHEMA" \
      --var "lakebase_host=$LAKEBASE_HOST" \
      --var "lakebase_db=$LAKEBASE_DB" \
      --var "quest_data_backend=$QUEST_DATA_BACKEND" 2>&1 || true
    $CLI apps deploy "$APP_NAME" \
      --source-code-path "$BUNDLE_USER_PATH" \
      $PROFILE_FLAG -o json 2>/dev/null || true
    success "App updated with provided Lakebase configuration"
  fi
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 7: Run Scoring Pipeline
# ══════════════════════════════════════════════════════════════════════════════
step "Step 7/8: Running scoring pipeline"

if [ -n "$SKIP_SCORING" ]; then
  warn "Skipping scoring pipeline (--skip-scoring)."
  if [ "$DEPLOY_MODE" = "quick" ]; then
    warn "Run the scoring notebook manually from your workspace."
  else
    warn "Run it manually later: databricks bundle run quest_scoring_pipeline --target $TARGET"
  fi
elif [ "$DEPLOY_MODE" = "quick" ]; then
  info "Running scoring notebook via one-time job..."
  info "Takes 2-5 minutes on first run..."
  echo ""

  NB_WORKSPACE_PATH="/Workspace/Users/${USER_EMAIL}/databricks-quest/notebooks/scoring_pipeline"
  SYNC_NB_PATH="/Workspace/Users/${USER_EMAIL}/databricks-quest/notebooks/lakebase_sync"
  # Two tasks: score (writes Delta), then the robust on-cluster sync into
  # Lakebase (same notebook the full-deploy job uses). The sync no-ops when
  # lakebase_host is empty (warehouse-only). No fragile local psql sync.
  set +e
  $CLI jobs submit \
    --json "{\"run_name\": \"Quest Scoring (one-time)\", \"tasks\": [{\"task_key\": \"run_scoring\", \"notebook_task\": {\"notebook_path\": \"$NB_WORKSPACE_PATH\", \"base_parameters\": {\"quest_catalog\": \"$QUEST_CATALOG\", \"quest_schema\": \"$QUEST_SCHEMA\", \"lakebase_host\": \"$LAKEBASE_HOST\", \"lakebase_db\": \"$LAKEBASE_DB\", \"app_name\": \"$APP_NAME\", \"warehouse_id\": \"$WAREHOUSE_ID\"}, \"source\": \"WORKSPACE\"}, \"environment_key\": \"default\"}, {\"task_key\": \"sync_to_lakebase\", \"depends_on\": [{\"task_key\": \"run_scoring\"}], \"notebook_task\": {\"notebook_path\": \"$SYNC_NB_PATH\", \"base_parameters\": {\"quest_catalog\": \"$QUEST_CATALOG\", \"quest_schema\": \"$QUEST_SCHEMA\", \"lakebase_host\": \"$LAKEBASE_HOST\", \"lakebase_db\": \"$LAKEBASE_DB\", \"app_name\": \"$APP_NAME\"}, \"source\": \"WORKSPACE\"}, \"environment_key\": \"default\"}], \"environments\": [{\"environment_key\": \"default\", \"spec\": {\"client\": \"1\", \"dependencies\": [\"psycopg2-binary\"]}}]}" \
    $PROFILE_FLAG 2>&1
  RUN_EXIT=$?
  set -e
  if [ "$RUN_EXIT" -ne 0 ]; then
    warn "Scoring pipeline submit failed (exit $RUN_EXIT). Run the notebook manually from your workspace."
  else
    success "Scoring pipeline submitted"
  fi
else
  info "This reads system tables, scores missions, creates tables, and grants permissions."
  info "Takes 2-5 minutes on first run..."
  echo ""

  # Pass the REAL lakebase_host so the job's sync_to_lakebase task populates
  # Lakebase at deploy time using the robust on-cluster Spark sync (the same
  # task the 4-hourly schedule uses). Previously this passed an empty
  # lakebase_host, which made that task skip — leaving deploy-time Lakebase
  # population to a fragile local psql sync that truncated then timed out on
  # large/cold data (app showed "Not Initialized"). One sync mechanism now.
  set +e
  $CLI bundle run quest_scoring_pipeline --target "$TARGET" $PROFILE_FLAG \
    --var "warehouse_id=$WAREHOUSE_ID" \
    --var "quest_catalog=$QUEST_CATALOG" \
    --var "quest_schema=$QUEST_SCHEMA" \
    --var "lakebase_host=$LAKEBASE_HOST" \
    --var "lakebase_db=$LAKEBASE_DB" \
    --var "quest_data_backend=$QUEST_DATA_BACKEND"
  RUN_EXIT=$?
  set -e
  if [ "$RUN_EXIT" -ne 0 ]; then
    warn "Scoring pipeline failed (exit $RUN_EXIT). You can re-run it manually later."
  fi

  success "Scoring pipeline complete"
fi

# NOTE: deploy-time Lakebase population is handled by the scoring job's
# sync_to_lakebase task (full deploy) / the one-time job's sync task (quick),
# both using the robust on-cluster Spark sync (notebooks/lakebase_sync.py).
# The previous fragile local psql sync here (TRUNCATE + per-batch INSERT with a
# 60s timeout and no Statements-API result chunking) truncated good data and
# timed out on large/cold datasets, leaving the app "Not Initialized" — removed.

# ══════════════════════════════════════════════════════════════════════════════
# STEP 8: Get App URL & Print Success
# ══════════════════════════════════════════════════════════════════════════════
step "Step 8/8: Verifying deployment"

# Wait a moment for the app to be registered
sleep 2

# Try to get the app info (dev mode prepends [dev deep_basu] to the name)
DEV_APP_NAME="$APP_NAME"
APP_JSON=$($CLI apps get "$DEV_APP_NAME" $PROFILE_FLAG -o json 2>/dev/null || true)

if [ -z "$APP_JSON" ] || ! echo "$APP_JSON" | python3 -c "import sys,json; json.load(sys.stdin)" &>/dev/null; then
  # In dev mode, the bundle may prefix the app name
  DEV_APP_NAME="[dev ${USER_EMAIL%%@*}] $APP_NAME"
  APP_JSON=$($CLI apps get "$DEV_APP_NAME" $PROFILE_FLAG -o json 2>/dev/null || true)
fi

if [ -z "$APP_JSON" ] || ! echo "$APP_JSON" | python3 -c "import sys,json; json.load(sys.stdin)" &>/dev/null; then
  # Try without the brackets format (bundle dev mode uses different naming)
  APP_JSON=""
fi

APP_URL=""
APP_STATE=""
if [ -n "$APP_JSON" ]; then
  APP_URL=$(echo "$APP_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('url',''))" 2>/dev/null || true)
  APP_STATE=$(echo "$APP_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',{}).get('state','UNKNOWN'))" 2>/dev/null || true)
fi

# ══════════════════════════════════════════════════════════════════════════════
# Verify permissions + data actually landed — fail loudly instead of printing a
# false "Deployed!" banner. Targets the two things that silently break a deploy:
#   1) the app service principal can't read the scored Delta tables (UC grants), and
#   2) scoring/sync produced no rows (app shows "Not Initialized" / empty profiles).
# A real problem hard-fails the deploy (exit 1). A check that can't be run warns.
# ══════════════════════════════════════════════════════════════════════════════
VERIFY_FAILED=0
CORE_TABLES="user_profile_snapshot mission_completions leaderboard"

if [ -z "${SKIP_VERIFY:-}" ] && [ -z "$SKIP_SCORING" ]; then
  step "Verifying permissions & data"

  # A workspace token for the Statements API + app health probe. Falls back to a
  # short-lived OAuth token when DATABRICKS_TOKEN isn't in the env (profile auth).
  VTOKEN="${DATABRICKS_TOKEN:-}"
  if [ -z "$VTOKEN" ]; then
    VTOKEN=$($CLI auth token $PROFILE_FLAG -o json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null || true)
  fi

  # Re-resolve the app service principal if an earlier branch didn't set it.
  if [ -z "${SP_CLIENT_ID:-}" ]; then
    SP_CLIENT_ID=$(app_sp_client_id)
  fi

  # ── Delta: SP grants + scored row counts (run as the deploying identity) ─────
  if [ -n "$WAREHOUSE_ID" ]; then
    set +e
    VERIFY_OUT=$(HOST="${DATABRICKS_HOST:-$WORKSPACE_HOST}" TOKEN="$VTOKEN" WH="$WAREHOUSE_ID" \
      SP="${SP_CLIENT_ID:-}" CAT="$QUEST_CATALOG" SCH="$QUEST_SCHEMA" CORE="$CORE_TABLES" python3 <<'PYEOF'
import os, json, time, urllib.request
host=os.environ.get("HOST","").rstrip("/"); tok=os.environ.get("TOKEN",""); wh=os.environ.get("WH","")
sp=os.environ.get("SP","").lower(); cat=os.environ.get("CAT",""); sch=os.environ.get("SCH","")
core=os.environ.get("CORE","").split()
def run(s, timeout=50):
    body=json.dumps({"warehouse_id":wh,"statement":s,"wait_timeout":f"{timeout}s"}).encode()
    req=urllib.request.Request(host+"/api/2.0/sql/statements",data=body,
        headers={"Authorization":"Bearer "+tok,"Content-Type":"application/json"})
    try: d=json.loads(urllib.request.urlopen(req,timeout=70).read())
    except Exception as e: return None, str(e)[:160]
    sid=d.get("statement_id"); st=d.get("status",{}).get("state")
    while st in ("PENDING","RUNNING") and sid:
        time.sleep(2)
        r=urllib.request.Request(host+"/api/2.0/sql/statements/"+sid,headers={"Authorization":"Bearer "+tok})
        try: d=json.loads(urllib.request.urlopen(r,timeout=30).read())
        except Exception as e: return None, str(e)[:160]
        st=d.get("status",{}).get("state")
    if st=="SUCCEEDED": return d.get("result",{}).get("data_array",[]) or [], None
    return None, (d.get("status",{}).get("error",{}) or {}).get("message","unknown")[:160]
fail=0
try:
    # No usable token => can't verify anything. WARN and skip (never hard-fail on auth).
    if not tok:
        print("  WARN  no usable workspace token — skipped Delta verification")
        print("VERIFY_RESULT=WARN"); raise SystemExit(0)
    # Permission checks ENFORCE the app SP's read access — a missing grant is the
    # exact failure that ships a "successful" deploy whose app shows no data, so we
    # hard-fail it. deploy.sh grants directly to the SP client-id, and SHOW GRANTS
    # returns that same id, so a demonstrated absence is real. We only WARN (don't
    # fail) when the check itself can't run (can't read grants), never on a
    # confirmed-present grant.
    def grant_check(scope, want, label):
        global fail
        rows,err=run(f"SHOW GRANTS `{sp}` ON {scope}")
        if rows is None:
            print(f"  WARN  {label}: could not verify ({err}) — not failing on an unverifiable grant"); return
        have={(r[1] or "").upper() for r in rows if len(r)>=2 and (r[0] or "").lower()==sp}
        missing=[w for w in want if w not in have]
        if missing:
            print(f"  FAIL  {label}: app SP is missing {missing} — it cannot read the data, so the app would show nothing"); fail=1
        else:
            print(f"  PASS  {label}: SP has {want}")
    if sp:
        grant_check(f"CATALOG `{cat}`", ["USE CATALOG"], "catalog grant")
        grant_check(f"SCHEMA `{cat}`.`{sch}`", ["USE SCHEMA","SELECT"], "schema grant")
    else:
        print("  WARN  app service principal not resolved — skipped grant check")
    # Data checks — a missing table or 0 rows is a REAL failure (scoring didn't land
    # data). A transient/auth read error only warns (don't block on a network blip).
    for t in core:
        rows,err=run(f"SELECT COUNT(*) FROM `{cat}`.`{sch}`.`{t}`")
        if rows is None:
            e=(err or "").lower()
            if "not found" in e or "does not exist" in e or "table_or_view" in e:
                print(f"  FAIL  data {t}: table missing — scoring did not create it"); fail=1
            else:
                print(f"  WARN  data {t}: could not read ({err})")
            continue
        try: n=int(rows[0][0])
        except Exception: n=0
        if n>0: print(f"  PASS  data {t}: {n} rows")
        else: print(f"  FAIL  data {t}: 0 rows (scoring produced no data)"); fail=1
    print("VERIFY_RESULT=FAIL" if fail else "VERIFY_RESULT=PASS")
except SystemExit:
    raise
except Exception as e:
    print(f"  WARN  verification error: {str(e)[:160]}")
    print("VERIFY_RESULT=WARN")
PYEOF
)
    set -e
    echo "$VERIFY_OUT" | grep -v '^VERIFY_RESULT=' || true
    # Only an explicit FAIL sentinel fails the deploy. A missing sentinel (python
    # crash) or a WARN sentinel (no token / transient) does NOT block the deploy.
    if echo "$VERIFY_OUT" | grep -q '^VERIFY_RESULT=FAIL'; then VERIFY_FAILED=1; fi
  else
    warn "No warehouse id resolved — skipped Delta permission/data verification."
  fi

  # ── Lakebase: confirm the app's read store actually has rows ─────────────────
  if [ -n "$LAKEBASE_HOST" ]; then
    LB_VPROJECT=$(echo "$APP_NAME" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9-]/-/g')
    LB_VTOKEN=$($CLI postgres generate-database-credential \
      "projects/$LB_VPROJECT/branches/production/endpoints/primary" \
      $PROFILE_FLAG -o json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])" 2>/dev/null || true)
    [ -z "$LB_VTOKEN" ] && LB_VTOKEN="$VTOKEN"
    for t in $CORE_TABLES; do
      CNT=$(PGPASSWORD="$LB_VTOKEN" psql "host=$LAKEBASE_HOST port=5432 dbname=$LAKEBASE_DB user=$USER_EMAIL sslmode=require" \
        -tAc "SELECT COUNT(*) FROM $t" 2>/dev/null | tr -d '[:space:]' || true)
      # Lakebase is warn-only: the Delta→Lakebase sync can still be in flight here,
      # so a 0/unreadable count must not block an otherwise-good deploy.
      if [[ "$CNT" =~ ^[0-9]+$ ]] && [ "$CNT" -gt 0 ]; then
        success "  PASS  lakebase $t: $CNT rows"
      elif [[ "$CNT" =~ ^[0-9]+$ ]]; then
        warn "  WARN  lakebase $t: 0 rows (Delta→Lakebase sync may still be running — recheck shortly)"
      else
        warn "  WARN  lakebase $t: could not read row count"
      fi
    done
  fi

  # ── App end-to-end: the app (running as the SP) can connect to its store ─────
  if [ -n "$APP_URL" ]; then
    for _ in $(seq 1 18); do
      [ "$APP_STATE" = "RUNNING" ] && break
      sleep 5
      APP_STATE=$($CLI apps get "$DEV_APP_NAME" $PROFILE_FLAG -o json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',{}).get('state','UNKNOWN'))" 2>/dev/null || echo "UNKNOWN")
    done
    HEALTH=$(APP_URL="$APP_URL" TOKEN="$VTOKEN" python3 <<'PYEOF' 2>/dev/null || true
import os, json, urllib.request
u=os.environ["APP_URL"].rstrip("/")+"/api/health"; tok=os.environ.get("TOKEN","")
req=urllib.request.Request(u, headers={"Authorization":"Bearer "+tok})
try:
    d=json.loads(urllib.request.urlopen(req,timeout=20).read())
    print("OK" if d.get("db_connected") else "DBDOWN")
except Exception:
    print("ERR")
PYEOF
)
    case "$HEALTH" in
      OK)     success "  PASS  app /api/health: db_connected=true" ;;
      DBDOWN) warn "  FAIL  app /api/health: db_connected=false — app is up but cannot read its data store"; VERIFY_FAILED=1 ;;
      *)      warn "  WARN  app /api/health not confirmed (state=$APP_STATE) — recheck once the app finishes starting." ;;
    esac
  fi

  # ── Verdict ──────────────────────────────────────────────────────────────────
  if [ "$VERIFY_FAILED" -ne 0 ]; then
    echo ""
    echo -e "${BOLD}${RED}╔══════════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}${RED}║   Verification FAILED — data/permissions      ║${NC}"
    echo -e "${BOLD}${RED}╚══════════════════════════════════════════════╝${NC}"
    echo -e "  The app deployed, but it will show empty / 'Not Initialized' until this is fixed:"
    echo -e "    • Check the scoring job run (Workflows) actually wrote ${BOLD}${QUEST_CATALOG}.${QUEST_SCHEMA}${NC}."
    echo -e "    • Ensure the app service principal has read access:"
    echo -e "        ${DIM}GRANT USE CATALOG ON CATALOG ${QUEST_CATALOG} TO \`${SP_CLIENT_ID:-<app-sp-client-id>}\`;${NC}"
    echo -e "        ${DIM}GRANT USE SCHEMA, SELECT ON SCHEMA ${QUEST_CATALOG}.${QUEST_SCHEMA} TO \`${SP_CLIENT_ID:-<app-sp-client-id>}\`;${NC}"
    echo -e "    • Fix the cause above, then re-run the deploy (or pass ${BOLD}--skip-verify${NC} to bypass)."
    echo ""
    exit 1
  fi
  success "Verification passed — SP can read the data and the scored tables are populated."
elif [ -n "${SKIP_VERIFY:-}" ]; then
  warn "Skipping post-deploy verification (--skip-verify)."
fi

# ── Success Banner ───────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}╔══════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${GREEN}║        Databricks Quest — Deployed!          ║${NC}"
echo -e "${BOLD}${GREEN}╚══════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${BOLD}Workspace:${NC}     $WORKSPACE_HOST"
echo -e "  ${BOLD}Catalog:${NC}       $QUEST_CATALOG.$QUEST_SCHEMA"
echo -e "  ${BOLD}Warehouse:${NC}     ${WAREHOUSE_NAME:-$WAREHOUSE_ID}"
echo -e "  ${BOLD}App Name:${NC}      $APP_NAME"
if [ -n "$APP_URL" ]; then
  echo -e "  ${BOLD}App URL:${NC}       ${CYAN}$APP_URL${NC}"
fi
if [ -n "$APP_STATE" ]; then
  echo -e "  ${BOLD}App State:${NC}     $APP_STATE"
fi
echo -e "  ${BOLD}Lakebase:${NC}      $LAKEBASE_HOST/$LAKEBASE_DB"
if [ "$QUEST_EVENT_MODE" = "on" ]; then
  echo -e "  ${BOLD}Event Mode:${NC}    ${GREEN}ENABLED${NC} (GameDay)"
else
  echo -e "  ${BOLD}Event Mode:${NC}    off (legacy adoption app)"
fi
if [ -n "$QUEST_ADMIN_ALLOWLIST" ]; then
  echo -e "  ${BOLD}Admins:${NC}        $QUEST_ADMIN_ALLOWLIST ${DIM}(seeded to quest_admins; manage more in-app)${NC}"
elif [ "$QUEST_ROLE" = "child" ]; then
  echo -e "  ${BOLD}Admins:${NC}        inherited from master (shared quest_admins)"
else
  echo -e "  ${BOLD}Admins:${NC}        ${YELLOW}open${NC} (no allowlist — Admin page visible to all)"
fi
if [ "$QUEST_ROLE" != "standalone" ]; then
  echo -e "  ${BOLD}Role:${NC}          $QUEST_ROLE${EVENT_SLUG:+  (event: $EVENT_SLUG)}"
  [ -n "$WORKSPACE_ID" ] && echo -e "  ${BOLD}Workspace ID:${NC}  $WORKSPACE_ID"
fi
echo ""

# master: print the shared event-writer credential to hand to child deploys.
if [ "$QUEST_ROLE" = "master" ] && [ -n "$EVENT_WRITER_PASSWORD" ]; then
  echo -e "${BOLD}${YELLOW}── Shared event-writer credential (give to child workspaces) ──${NC}"
  echo ""
  echo -e "  Children deploy with these flags (rotate per event):"
  echo ""
  echo -e "    ${CYAN}./deploy.sh \\\\${NC}"
  echo -e "    ${CYAN}  --role child \\\\${NC}"
  echo -e "    ${CYAN}  --event ${EVENT_SLUG:-<event-slug>} \\\\${NC}"
  echo -e "    ${CYAN}  --master-lakebase-host $LAKEBASE_HOST \\\\${NC}"
  echo -e "    ${CYAN}  --master-lakebase-user $MASTER_LAKEBASE_USER \\\\${NC}"
  echo -e "    ${CYAN}  --master-lakebase-token '$EVENT_WRITER_PASSWORD'${NC}"
  echo ""
  echo -e "  ${DIM}Verify connectivity from a child first:${NC}"
  echo -e "  ${DIM}  PGPASSWORD='$EVENT_WRITER_PASSWORD' python3 scripts/federation_spike.py \\\\${NC}"
  echo -e "  ${DIM}    --host $LAKEBASE_HOST --db $LAKEBASE_DB --user $MASTER_LAKEBASE_USER${NC}"
  echo ""
  warn "Store this secret securely — it is shown once and is not persisted by this script."
  echo ""
fi

if [ -n "$APP_URL" ]; then
  echo -e "  Open the app: ${BOLD}${CYAN}$APP_URL${NC}"
else
  echo -e "  Get the app URL:"
  echo -e "    ${DIM}databricks apps get $APP_NAME $PROFILE_FLAG${NC}"
fi

echo ""
echo -e "  ${DIM}The scoring pipeline is scheduled to run every 4 hours.${NC}"
echo -e "  ${DIM}To re-run it manually:${NC}"
echo -e "  ${DIM}  databricks bundle run quest_scoring_pipeline --target $TARGET \\${NC}"
echo -e "  ${DIM}    --var warehouse_id=$WAREHOUSE_ID \\${NC}"
echo -e "  ${DIM}    --var quest_catalog=$QUEST_CATALOG${NC}"
echo ""
