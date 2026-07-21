import os
import time
import logging
from datetime import datetime, date
from typing import List, Dict, Any, Optional

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import BaseModel

# Lakebase connection handling is centralized in db.py so the app and the
# migration runner share one implementation.
import db
from db import (
    LAKEBASE_HOST,
    LAKEBASE_DB,
    serialize,
    get_workspace_client,
    get_lakebase_connection,
    execute_query,
)
import config
from repositories import (
    QuestPacksRepository,
    EventsRepository,
    EventStateError,
    AttemptsRepository,
    LeaderboardRepository,
    ScoringRepository,
    FederationRepository,
    RosterImportError,
    AdminsRepository,
    AnnouncementsRepository,
    ResourcesRepository,
    ReportingRepository,
)
from repositories.events import attempts_open, JOINABLE_STATUSES, ALLOWED_TRANSITIONS
from services import quest_pack_loader
from services import federation as fed
from services import record_audit
from services import namespace as ns
from services import resource_service as rsvc
from services import observability as obs
from services import report_service as report_svc
from services.validation_engine import default_engine, aggregate_status
from services.scoring_service import default_scoring_service
from services.resource_service import default_resource_service
from services.quest_pack_loader import QuestPackImportError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("databricks-quest")

app = FastAPI(title="Databricks Quest API")


# ── Request correlation + standard error envelope (PR10) ─────────────────────


@app.middleware("http")
async def _request_id_middleware(request: Request, call_next):
    """Assign a correlation id to every request and echo it on the response.

    Stored on ``request.state.request_id`` so handlers and the exception
    handlers below can stamp it into structured logs and error bodies.
    """
    rid = obs.normalize_request_id(request.headers.get(obs.REQUEST_ID_HEADER))
    request.state.request_id = rid
    try:
        response = await call_next(request)
    except Exception:
        # Let the exception handlers below build the body; re-raise so they run.
        logger.exception("unhandled error request_id=%s path=%s", rid, request.url.path)
        raise
    response.headers[obs.REQUEST_ID_HEADER] = rid
    return response


def _error_body(code: str, message: str, request_id: Optional[str], extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    err: Dict[str, Any] = {"code": code, "message": message}
    if request_id:
        err["request_id"] = request_id
    if extra:
        err.update(extra)
    return {"error": err}


@app.exception_handler(StarletteHTTPException)
async def _http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Normalize HTTPException bodies to ``{"error": {..., request_id}}``.

    Handlers across the app already raise ``detail={"error": {...}}``; we merge
    the request id into that error object (or wrap a bare string detail) without
    changing the existing ``code``/``message`` shape clients depend on.
    """
    rid = getattr(request.state, "request_id", None)
    detail = exc.detail
    if isinstance(detail, dict) and isinstance(detail.get("error"), dict):
        err = dict(detail["error"])
        if rid and "request_id" not in err:
            err["request_id"] = rid
        body = {"error": err}
    else:
        body = _error_body("HTTP_ERROR", str(detail), rid)
    headers = {obs.REQUEST_ID_HEADER: rid} if rid else None
    return JSONResponse(status_code=exc.status_code, content=body, headers=headers)


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    """Catch-all: never leak internals; return a player-safe error + request id."""
    rid = getattr(request.state, "request_id", None)
    logger.exception("unhandled error request_id=%s: %s", rid, exc)
    return JSONResponse(
        status_code=500,
        content=_error_body(
            "INTERNAL_ERROR",
            "Something went wrong. Quote the request id to your host if it persists.",
            rid,
        ),
        headers={obs.REQUEST_ID_HEADER: rid} if rid else None,
    )

if not LAKEBASE_HOST:
    logger.warning("LAKEBASE_HOST not set — database queries will fail until configured")
else:
    logger.info(f"Data source: Lakebase ({LAKEBASE_HOST}/{LAKEBASE_DB})")

logger.info("Quest role: %s", config.QUEST_ROLE)
if config.is_child():
    logger.info(
        "Federation child: workspace_id=%s event=%s → shared master Lakebase",
        config.QUEST_WORKSPACE_ID or "(unset)",
        config.QUEST_EVENT_SLUG or "(unset)",
    )

# Each mission carries a `doc_url` pointing at the specific official Databricks
# documentation page for that capability. Every URL below was verified live
# against docs.databricks.com (AWS/en docs) — do not swap for a generic landing
# page. When adding a mission, link the most specific how-to page that exists.
_DOCS = "https://docs.databricks.com/aws/en"

# Databricks Academy course base. Each Get Started mission's `doc_url` points at
# the FREE self-paced course a user takes; `learning_resources` links the same free
# course. Points are self-attested via a tick-box (POST /api/training/attest) — see
# the tick-box revision in the design doc. All course ids/slugs verified live and
# confirmed FREE (price 0) against customer-academy.databricks.com on 2026-07-17.
_ACAD = "https://customer-academy.databricks.com/learn/courses"

MISSION_DEFINITIONS = [
    # --- Getting Started ---
    {"id": "first_steps", "name": "First Steps", "description": "Record your first Databricks usage", "points": 25, "category": "Getting Started", "award_type": "one_time", "icon": "rocket", "doc_url": f"{_DOCS}/getting-started/free-edition"},
    # --- Getting Started: Databricks Academy "Get Started" training series ---
    # Self-attested via the tick-box: a user confirms course completion, the app
    # writes it to the training_completions feed + serving tables (250 pts each),
    # and the scoring pipeline reconciles idempotently. Databricks Learner bonus
    # fires at 2+ distinct Get Started courses. All courses are FREE & self-paced.
    {"id": "gs_data_engineering", "name": "Get Started: Data Engineering", "description": "Complete the free Get Started with Databricks for Data Engineering self-paced course (2 hrs)", "points": 250, "category": "Getting Started", "award_type": "one_time", "icon": "git-branch", "detection": "training", "doc_url": f"{_ACAD}/2469/get-started-with-databricks-for-data-engineering", "learning_resources": [{"label": "Free self-paced course (2 hrs)", "url": f"{_ACAD}/2469/get-started-with-databricks-for-data-engineering"}]},
    {"id": "gs_machine_learning", "name": "Get Started: Machine Learning", "description": "Complete the free Get Started with Databricks for Machine Learning self-paced course (2 hrs)", "points": 250, "category": "Getting Started", "award_type": "one_time", "icon": "brain", "detection": "training", "doc_url": f"{_ACAD}/2460/get-started-with-databricks-for-machine-learning", "learning_resources": [{"label": "Free self-paced course (2 hrs)", "url": f"{_ACAD}/2460/get-started-with-databricks-for-machine-learning"}]},
    {"id": "gs_generative_ai", "name": "Get Started: Generative AI", "description": "Complete the free Get Started with Databricks for Generative AI self-paced course (2 hrs)", "points": 250, "category": "Getting Started", "award_type": "one_time", "icon": "sparkles", "detection": "training", "doc_url": f"{_ACAD}/2724/get-started-with-databricks-for-generative-ai", "learning_resources": [{"label": "Free self-paced course (2 hrs)", "url": f"{_ACAD}/2724/get-started-with-databricks-for-generative-ai"}]},
    {"id": "gs_sql_analytics_bi", "name": "Get Started: SQL Analytics & BI", "description": "Complete the free Get Started with SQL Analytics and BI on Databricks self-paced course (2 hrs)", "points": 250, "category": "Getting Started", "award_type": "one_time", "icon": "bar-chart-2", "detection": "training", "doc_url": f"{_ACAD}/3347/get-started-with-sql-analytics-and-bi-on-databricks", "learning_resources": [{"label": "Free self-paced course (2 hrs)", "url": f"{_ACAD}/3347/get-started-with-sql-analytics-and-bi-on-databricks"}]},
    {"id": "gs_data_warehousing", "name": "Get Started: Data Warehousing", "description": "Complete the free Get Started with Databricks for Data Warehousing self-paced course (2 hrs)", "points": 250, "category": "Getting Started", "award_type": "one_time", "icon": "database", "detection": "training", "doc_url": f"{_ACAD}/3603/get-started-with-databricks-for-data-warehousing", "learning_resources": [{"label": "Free self-paced course (2 hrs)", "url": f"{_ACAD}/3603/get-started-with-databricks-for-data-warehousing"}]},
    {"id": "gs_platform_admin", "name": "Get Started: Platform Administration", "description": "Complete the free Get Started with Databricks Platform Administration self-paced course (2 hrs)", "points": 250, "category": "Getting Started", "award_type": "one_time", "icon": "cpu", "detection": "training", "doc_url": f"{_ACAD}/2453/get-started-with-databricks-platform-administration", "learning_resources": [{"label": "Free self-paced course (2 hrs)", "url": f"{_ACAD}/2453/get-started-with-databricks-platform-administration"}]},
    {"id": "gs_data_governance", "name": "Get Started: Data Governance", "description": "Complete the free Get Started with Data Governance on Databricks self-paced course (2 hrs)", "points": 250, "category": "Getting Started", "award_type": "one_time", "icon": "share", "detection": "training", "doc_url": f"{_ACAD}/4677/get-started-with-data-governance-on-databricks", "learning_resources": [{"label": "Free self-paced course (2 hrs)", "url": f"{_ACAD}/4677/get-started-with-data-governance-on-databricks"}]},
    {"id": "gs_lakebase", "name": "Get Started: Lakebase", "description": "Complete the free Get Started with Lakebase self-paced course (2 hrs)", "points": 250, "category": "Getting Started", "award_type": "one_time", "icon": "database", "detection": "training", "doc_url": f"{_ACAD}/5081/get-started-with-lakebase", "learning_resources": [{"label": "Free self-paced course (2 hrs)", "url": f"{_ACAD}/5081/get-started-with-lakebase"}]},
    {"id": "gs_lakehouse_architecture", "name": "Get Started: Lakehouse Architecture", "description": "Complete the free Get Started with Lakehouse Architecture on Databricks self-paced course (2 hrs)", "points": 250, "category": "Getting Started", "award_type": "one_time", "icon": "layers", "detection": "training", "doc_url": f"{_ACAD}/3509/get-started-with-lakehouse-architecture-on-databricks", "learning_resources": [{"label": "Free self-paced course (2 hrs)", "url": f"{_ACAD}/3509/get-started-with-lakehouse-architecture-on-databricks"}]},
    # AI Agents has no FREE self-paced edition in the catalog; its instructor-led course is free (price 0), so link that.
    {"id": "gs_ai_agents", "name": "Get Started: AI Agents", "description": "Complete the free Get Started with AI Agents on Databricks course (2 hrs)", "points": 250, "category": "Getting Started", "award_type": "one_time", "icon": "zap", "detection": "training", "doc_url": f"{_ACAD}/4459/get-started-with-ai-agents-on-databricks", "learning_resources": [{"label": "Free course (2 hrs)", "url": f"{_ACAD}/4459/get-started-with-ai-agents-on-databricks"}]},
    {"id": "databricks_learner", "name": "Databricks Learner", "description": "Complete 2 or more Get Started courses to be recognized as a Databricks learner", "points": 500, "category": "Getting Started", "award_type": "one_time", "icon": "award", "detection": "training", "doc_url": "https://www.databricks.com/learn/training/home"},
    # --- Data Engineering ---
    {"id": "job_creator", "name": "Job Creator", "description": "Create your first Lakeflow Job", "points": 100, "category": "Data Engineering", "award_type": "one_time", "icon": "briefcase", "doc_url": f"{_DOCS}/jobs/create-run-jobs"},
    {"id": "pipeline_builder", "name": "Pipeline Builder", "description": "Create your first Lakeflow Spark Declarative Pipeline", "points": 150, "category": "Data Engineering", "award_type": "one_time", "icon": "git-branch", "doc_url": f"{_DOCS}/ldp/"},
    {"id": "pipeline_runner", "name": "Pipeline Runner", "description": "Complete your first successful pipeline update", "points": 200, "category": "Data Engineering", "award_type": "one_time", "icon": "play-circle", "doc_url": f"{_DOCS}/ldp/tutorial-pipelines"},
    {"id": "scheduler", "name": "Scheduler", "description": "Create a scheduled or CRON-triggered job", "points": 150, "category": "Data Engineering", "award_type": "one_time", "icon": "clock", "doc_url": f"{_DOCS}/jobs/triggers"},
    {"id": "auto_loader_pioneer", "name": "Auto Loader Pioneer", "description": "Use Auto Loader in a pipeline for streaming ingestion", "points": 250, "category": "Data Engineering", "award_type": "one_time", "icon": "upload-cloud", "doc_url": f"{_DOCS}/ingestion/cloud-object-storage/auto-loader/"},
    {"id": "multi_task_orchestrator", "name": "Multi-Task Orchestrator", "description": "Create a workflow with 3+ tasks", "points": 200, "category": "Data Engineering", "award_type": "one_time", "icon": "git-branch", "doc_url": f"{_DOCS}/jobs/configure-task"},
    {"id": "liquid_clustering", "name": "Liquid Clustering Adopter", "description": "Enable Liquid Clustering on a table", "points": 200, "category": "Data Engineering", "award_type": "one_time", "icon": "layers", "doc_url": f"{_DOCS}/delta/clustering/"},
    # --- Business Users (Genie, dashboards, SQL, apps, notebooks) — track: Business Users ---
    {"id": "genie_creator", "name": "Genie Creator", "description": "Create your first AI/BI Genie space", "points": 200, "category": "Analytics", "track": "Business Users", "award_type": "one_time", "icon": "sparkles", "doc_url": f"{_DOCS}/genie/set-up"},
    {"id": "genie_explorer", "name": "Genie Explorer", "description": "Ask a question in an AI/BI Genie space", "points": 100, "category": "Analytics", "track": "Business Users", "award_type": "one_time", "icon": "search", "doc_url": f"{_DOCS}/genie/"},
    {"id": "genie_curator", "name": "Genie Curator", "description": "Add instructions or sample questions to tune a Genie space", "points": 150, "category": "Analytics", "track": "Business Users", "award_type": "one_time", "icon": "sparkles", "doc_url": f"{_DOCS}/genie/best-practices"},
    {"id": "genie_power_user", "name": "Genie Power User", "description": "Ask 10+ Genie questions in a single week", "points": 100, "category": "Analytics", "track": "Business Users", "award_type": "repeatable", "icon": "zap", "doc_url": f"{_DOCS}/genie/best-practices"},
    {"id": "genie_code_user", "name": "AI Assistant", "description": "Use the Databricks Assistant (Genie) to write or fix code", "points": 100, "category": "AI / ML", "track": "Business Users", "award_type": "one_time", "icon": "brain", "doc_url": f"{_DOCS}/genie-code/"},
    {"id": "dashboard_designer", "name": "Dashboard Designer", "description": "Create your first Databricks Dashboard", "points": 150, "category": "Analytics", "track": "Business Users", "award_type": "one_time", "icon": "layout-dashboard", "doc_url": f"{_DOCS}/dashboards/tutorials/create-dashboard"},
    {"id": "dashboard_viewer", "name": "Dashboard Explorer", "description": "Open and view a published AI/BI dashboard", "points": 75, "category": "Analytics", "track": "Business Users", "award_type": "one_time", "icon": "bar-chart-2", "doc_url": f"{_DOCS}/dashboards/"},
    {"id": "dashboard_publisher", "name": "Dashboard Publisher", "description": "Publish a dashboard for others to use", "points": 150, "category": "Analytics", "track": "Business Users", "award_type": "one_time", "icon": "share-2", "doc_url": f"{_DOCS}/dashboards/share/"},
    {"id": "dashboard_operator", "name": "Dashboard Operator", "description": "Schedule a dashboard delivery or subscription", "points": 150, "category": "Analytics", "track": "Business Users", "award_type": "one_time", "icon": "calendar-check", "doc_url": f"{_DOCS}/dashboards/share/schedule-subscribe"},
    {"id": "data_explorer", "name": "Data Explorer", "description": "Execute 50+ SQL queries in a single week", "points": 150, "category": "Analytics", "track": "Business Users", "award_type": "repeatable", "icon": "search", "doc_url": f"{_DOCS}/sql/user/sql-editor/"},
    {"id": "power_analyst", "name": "Power Analyst", "description": "Execute 200+ SQL queries in a single week", "points": 200, "category": "Analytics", "track": "Business Users", "award_type": "repeatable", "icon": "bar-chart-2", "doc_url": f"{_DOCS}/sql/user/sql-editor/"},
    {"id": "query_author", "name": "Query Author", "description": "Save a query in the SQL editor", "points": 75, "category": "Analytics", "track": "Business Users", "award_type": "one_time", "icon": "search", "doc_url": f"{_DOCS}/sql/user/queries/"},
    {"id": "alert_creator", "name": "Alert Creator", "description": "Create a SQL Alert with a schedule", "points": 150, "category": "Analytics", "track": "Business Users", "award_type": "one_time", "icon": "bell", "doc_url": f"{_DOCS}/sql/user/alerts/"},
    {"id": "app_builder", "name": "App Builder", "description": "Create and deploy a Databricks App", "points": 250, "category": "Analytics", "track": "Business Users", "award_type": "one_time", "icon": "layers", "doc_url": f"{_DOCS}/dev-tools/databricks-apps/"},
    {"id": "notebook_author", "name": "Notebook Author", "description": "Create your first notebook", "points": 75, "category": "Analytics", "track": "Business Users", "award_type": "one_time", "icon": "play", "doc_url": f"{_DOCS}/notebooks/"},
    # --- Lakebase — track: Lakebase ---
    {"id": "lakebase_builder", "name": "Lakebase Builder", "description": "Create your first Lakebase (Postgres) database instance", "points": 250, "category": "Lakebase", "track": "Lakebase", "award_type": "one_time", "icon": "database", "doc_url": f"{_DOCS}/oltp/create/"},
    {"id": "lakebase_sync", "name": "Lakebase Sync Builder", "description": "Sync a Unity Catalog table into Lakebase", "points": 250, "category": "Lakebase", "track": "Lakebase", "award_type": "one_time", "icon": "upload-cloud", "doc_url": f"{_DOCS}/oltp/"},
    {"id": "lakebase_database", "name": "Lakebase Database Creator", "description": "Create a Lakebase database or registered catalog", "points": 150, "category": "Lakebase", "track": "Lakebase", "award_type": "one_time", "icon": "database", "doc_url": f"{_DOCS}/oltp/create/"},
    {"id": "lakebase_connector", "name": "Lakebase Connector", "description": "Connect to Lakebase from an app or client", "points": 100, "category": "Lakebase", "track": "Lakebase", "award_type": "one_time", "icon": "zap", "doc_url": f"{_DOCS}/oltp/query/"},
    # --- AI / ML ---
    {"id": "model_deployer", "name": "Model Deployer", "description": "Deploy a model to a serving endpoint", "points": 300, "category": "AI / ML", "award_type": "one_time", "icon": "cpu", "doc_url": f"{_DOCS}/machine-learning/model-serving/"},
    {"id": "ai_function_builder", "name": "AI Function Builder", "description": "Use ai_query() in a SQL statement", "points": 250, "category": "AI / ML", "award_type": "one_time", "icon": "sparkles", "doc_url": f"{_DOCS}/large-language-models/ai-query/"},
    {"id": "vector_search_pioneer", "name": "Vector Search Pioneer", "description": "Create a Vector Search index", "points": 200, "category": "AI / ML", "award_type": "one_time", "icon": "search", "doc_url": f"{_DOCS}/generative-ai/vector-search/"},
    {"id": "mlflow_experimenter", "name": "MLflow Experimenter", "description": "Log 10+ MLflow models/runs to an experiment", "points": 150, "category": "AI / ML", "award_type": "one_time", "icon": "flask-conical", "doc_url": f"{_DOCS}/mlflow/tracking/"},
    # --- Streaming ---
    {"id": "stream_starter", "name": "Stream Starter", "description": "Run a Structured Streaming job", "points": 250, "category": "Streaming", "award_type": "one_time", "icon": "radio", "doc_url": f"{_DOCS}/structured-streaming/"},
    # --- Product-specific consumption (repeatable, monthly) — filed under the product's own category ---
    # Interactive human consumption only (see scoring_pipeline.py INTERACTIVE_USAGE).
    # Job/DLT consumption missions were removed: that compute is automated by
    # definition, so those missions rewarded every scheduled run rather than a
    # person. Setting up a job/pipeline is credited once via Job Creator /
    # Pipeline Builder instead.
    {"id": "sql_analyst", "name": "SQL Analyst", "description": "Consume 50+ SQL Warehouse DBUs in a month", "points": 100, "category": "Analytics", "track": "Business Users", "award_type": "repeatable", "icon": "database", "doc_url": f"{_DOCS}/compute/sql-warehouse/"},
    {"id": "ml_practitioner", "name": "ML Practitioner", "description": "Consume any Model Serving DBUs in a month", "points": 150, "category": "AI / ML", "award_type": "repeatable", "icon": "brain", "doc_url": f"{_DOCS}/machine-learning/model-serving/"},
    # --- Engagement ---
    {"id": "consistent_operator", "name": "Consistent Operator", "description": "Run pipelines or jobs on 7 distinct days within 30 days", "points": 300, "category": "Engagement", "award_type": "repeatable", "icon": "calendar-check", "doc_url": f"{_DOCS}/admin/system-tables/"},
    {"id": "daily_driver", "name": "Daily Driver", "description": "Active on 20+ days in a 30-day window", "points": 400, "category": "Engagement", "award_type": "repeatable", "icon": "calendar", "doc_url": f"{_DOCS}/admin/system-tables/"},
    {"id": "cross_product_champion", "name": "Cross-Product Champion", "description": "Use 6+ distinct Databricks products in a month", "points": 500, "category": "Engagement", "award_type": "repeatable", "icon": "award", "doc_url": f"{_DOCS}/admin/system-tables/billing"},
    # --- Governance ---
    {"id": "uc_publisher", "name": "Unity Catalog Publisher", "description": "Share a table across schemas", "points": 150, "category": "Governance", "award_type": "one_time", "icon": "share", "doc_url": f"{_DOCS}/data-governance/unity-catalog/"},
]

# Consumption points: 1 point per 10 DBUs consumed, scored weekly
CONSUMPTION_POINTS_RATIO = 10  # DBUs per point

BADGE_DEFINITIONS = [
    {"id": "pipeline_craftsman", "name": "Pipeline Craftsman", "description": "Complete 5 pipeline-related missions", "icon": "wrench", "required_missions": 5, "mission_filter": ["pipeline_builder", "pipeline_runner", "auto_loader_pioneer", "consistent_operator", "scheduler"]},
    {"id": "platform_explorer", "name": "Platform Explorer", "description": "Use 4+ distinct Databricks product areas", "icon": "compass", "required_products": 4},
    {"id": "consistent_contributor", "name": "Consistent Contributor", "description": "Maintain a 14-day activity streak", "icon": "flame", "required_streak": 14},
    {"id": "ai_pioneer", "name": "AI Pioneer", "description": "Complete 3 AI/ML missions", "icon": "brain", "required_missions": 3, "mission_filter": ["model_deployer", "ai_function_builder", "vector_search_pioneer", "mlflow_experimenter"]},
    {"id": "full_stack", "name": "Full Stack", "description": "Complete missions in 5+ different categories", "icon": "layers", "required_categories": 5},
]

LEVEL_THRESHOLDS = [
    ("Elite", 5000),
    ("Platinum", 2000),
    ("Gold", 800),
    ("Silver", 300),
    ("Bronze", 0),
]


def get_level(points: int) -> str:
    for name, threshold in LEVEL_THRESHOLDS:
        if points >= threshold:
            return name
    return "Bronze"


def get_level_progress(points: int) -> dict:
    level = get_level(points)
    for i, (name, threshold) in enumerate(LEVEL_THRESHOLDS):
        if points >= threshold:
            next_threshold = LEVEL_THRESHOLDS[i - 1][1] if i > 0 else threshold + 1000
            return {
                "level": name,
                "current_points": points,
                "level_floor": threshold,
                "level_ceiling": next_threshold,
                "progress_pct": min(100, int((points - threshold) / max(1, next_threshold - threshold) * 100)),
            }
    return {"level": "Bronze", "current_points": 0, "level_floor": 0, "level_ceiling": 300, "progress_pct": 0}


def get_user_email(request: Request) -> str:
    email = request.headers.get("X-Forwarded-Email", "")
    if not email:
        email = request.headers.get("X-Forwarded-User", "")
    if not email:
        email = os.getenv("QUEST_DEFAULT_USER", "unknown@example.com")
    return email


# Host authority for GameDay host endpoints. A user is a host when ANY of:
#   - they are listed in QUEST_HOST_ALLOWLIST (comma-separated emails), OR
#   - they are an admin (env QUEST_ADMIN_ALLOWLIST ∪ DB quest_admins), OR
#   - they hold an event_hosts row for the event in the request path.
# This unifies the three authorities so "see the Host tab but get a 403" and
# "allowlisted but no tab" can no longer diverge — the frontend lobby uses the
# same rule via the event lobby's ``is_host`` field.
QUEST_HOST_ALLOWLIST = [
    e.strip().lower() for e in os.getenv("QUEST_HOST_ALLOWLIST", "").split(",") if e.strip()
]

# Fail-closed dev escape hatch. When Event Mode is on but NO host authority is
# configured anywhere (no allowlist, no admins, and — for an event — no
# event_hosts rows), host endpoints deny by default. Set QUEST_HOST_OPEN=1 to
# reopen them for local development only.
QUEST_HOST_OPEN = os.getenv("QUEST_HOST_OPEN", "").strip().lower() in {
    "1", "true", "on", "yes", "enabled", "enable",
}

# Env admin allowlist — the *bootstrap/fallback* for the Admin page
# (/api/admin/*). Set from deploy.sh's --admins flag (comma-separated emails),
# defaulting to the deploying user. The durable source of truth is the
# Lakebase `quest_admins` table (shared across master/child); this env list is
# seeded into it on startup and is always unioned in as a fallback so the
# deployer keeps access even if the table is empty/unreachable. When BOTH are
# empty (e.g. local dev) the endpoints stay open, matching prior behaviour.
QUEST_ADMIN_ALLOWLIST = [
    e.strip().lower() for e in os.getenv("QUEST_ADMIN_ALLOWLIST", "").split(",") if e.strip()
]

admins_repo = AdminsRepository()

# Small TTL cache over the DB admin set so we don't query Lakebase on every
# request (profile + each admin call). Invalidated locally on add/remove; other
# app instances pick up changes within the TTL.
_ADMIN_CACHE: Dict[str, Any] = {"emails": set(), "expiry": 0.0}
_ADMIN_CACHE_TTL = 30.0


def _invalidate_admin_cache() -> None:
    _ADMIN_CACHE["expiry"] = 0.0


def _db_admin_emails() -> set:
    """DB admin emails (cached). Returns empty set if the table is unreachable."""
    now = time.time()
    if _ADMIN_CACHE["expiry"] > now:
        return _ADMIN_CACHE["emails"]
    try:
        emails = set(admins_repo.list_emails())
        _ADMIN_CACHE["emails"] = emails
        _ADMIN_CACHE["expiry"] = now + _ADMIN_CACHE_TTL
        return emails
    except Exception as exc:  # noqa: BLE001 - fall back to env allowlist
        logger.warning("admin list read failed; using env allowlist only: %s", exc)
        _ADMIN_CACHE["emails"] = set()
        _ADMIN_CACHE["expiry"] = now + 5.0  # brief negative cache
        return set()


def admin_emails() -> set:
    """Effective admin set: env allowlist ∪ DB allowlist (both lowercased)."""
    return set(QUEST_ADMIN_ALLOWLIST) | _db_admin_emails()


def is_admin_user(user: str) -> bool:
    """True if the user may see the Admin page.

    When admins are configured (env allowlist ∪ DB table), only they qualify.
    When NOTHING is configured we fail CLOSED in a deployed app (a running
    Databricks App sets DATABRICKS_CLIENT_ID) so an empty/unreachable admin table
    can't silently open the Admin page to everyone. Locally (no SP env) we stay
    open for dev/legacy parity.
    """
    effective = admin_emails()
    if not effective:
        running_as_app = bool(os.getenv("DATABRICKS_CLIENT_ID", "").strip())
        if running_as_app:
            logger.warning("No admin configured in a deployed app; denying Admin access (fail-closed).")
            return False
        return True  # local dev / no service principal
    return (user or "").lower() in effective


def require_admin(request: Request) -> str:
    """FastAPI dependency: enforce the admin allowlist on /api/admin/* endpoints."""
    user = get_user_email(request)
    if not is_admin_user(user):
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": "FORBIDDEN", "message": "Admin access required."}},
        )
    return user


def _ensure_event_mode() -> None:
    """Gate GameDay/Event Mode surfaces. 404 when Event Mode is not enabled.

    Event Mode is opt-in (``QUEST_EVENT_MODE`` / ``--event-mode``; implied by the
    master/child roles). When off, the deployment is the legacy adoption app and
    every GameDay endpoint behaves as if it does not exist.
    """
    if not config.event_mode_enabled():
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "EVENT_MODE_DISABLED",
                    "message": "Event Mode is not enabled on this deployment.",
                }
            },
        )


def require_event_mode() -> None:
    """FastAPI dependency form of :func:`_ensure_event_mode`."""
    _ensure_event_mode()


def _resolve_event_id_opt(event_id_or_slug: Optional[str]) -> Optional[str]:
    """Resolve an event id/slug to a canonical event_id, or None if unknown."""
    if not event_id_or_slug:
        return None
    try:
        ev = events_repo.get_event(event_id_or_slug) or events_repo.get_event_by_slug(
            event_id_or_slug
        )
        return ev["event_id"] if ev else None
    except Exception:  # noqa: BLE001
        return None


def is_host_user(user: str, event_id: Optional[str]) -> bool:
    """Unified host check: allowlist OR admin OR event_hosts membership.

    Fail-closed: when no host authority is configured anywhere (empty allowlist,
    no admins, and no event_hosts rows for the event) the user is a host only if
    the ``QUEST_HOST_OPEN`` dev escape hatch is set.
    """
    u = (user or "").lower()
    allowlist = QUEST_HOST_ALLOWLIST
    admins = admin_emails()  # env ∪ DB

    if allowlist and u in allowlist:
        return True
    if admins and u in admins:
        return True
    if event_id:
        try:
            if events_repo.is_host(event_id, user):
                return True
        except Exception:  # noqa: BLE001
            pass

    # No explicit grant matched. Decide open vs fail-closed.
    event_has_hosts = False
    if event_id:
        try:
            event_has_hosts = bool(events_repo.list_event_hosts(event_id))
        except Exception:  # noqa: BLE001
            event_has_hosts = False
    no_authority = (not allowlist) and (not admins) and (not event_has_hosts)
    if no_authority:
        return QUEST_HOST_OPEN
    return False


def require_host(request: Request) -> str:
    """FastAPI dependency: resolve the user and enforce host authority.

    Also gates on Event Mode — every ``/api/host/*`` surface is GameDay-only, so
    a legacy (Event-Mode-off) deployment 404s here. Host authority is the union
    of the allowlist, admins, and per-event ``event_hosts`` membership, resolved
    against the ``event_id`` (or slug) in the request path when present.
    """
    _ensure_event_mode()
    user = get_user_email(request)
    path_params = getattr(request, "path_params", {}) or {}
    event_id = _resolve_event_id_opt(path_params.get("event_id"))
    if is_host_user(user, event_id):
        return user
    raise HTTPException(
        status_code=403,
        detail={"error": {"code": "FORBIDDEN", "message": "Host role required."}},
    )


def require_master_host(request: Request) -> str:
    """Host dependency that additionally forbids the child role.

    Roster / workspace-health endpoints are master-side concerns (children have
    only INSERT on the shared facts and cannot create teams/participants). They
    are available on ``master`` and ``standalone`` (for local testing) but
    return 404 on a ``child`` deployment, so the same binary exposes the right
    surface per role.
    """
    if config.is_child():
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "NOT_FOUND", "message": "Endpoint not available in child role."}},
        )
    return require_host(request)


quest_packs_repo = QuestPacksRepository()
events_repo = EventsRepository()
attempts_repo = AttemptsRepository()
leaderboard_repo = LeaderboardRepository()
scoring_repo = ScoringRepository()
federation_repo = FederationRepository()
announcements_repo = AnnouncementsRepository()
resources_repo = ResourcesRepository()
reporting_repo = ReportingRepository()


@app.on_event("startup")
async def _federation_startup() -> None:
    """Child role: record this workspace's presence in the shared DB once."""
    if not config.event_mode_enabled():
        return
    try:
        fed.startup_checkin()
    except Exception as exc:  # noqa: BLE001 - never block startup
        logger.warning("federation startup check-in skipped: %s", exc)


@app.on_event("startup")
async def _admin_seed_startup() -> None:
    """Seed the shared quest_admins table from the env allowlist.

    Runs for standalone/master (which own the table and connect with DDL
    rights); child apps inherit the shared admin list from the master DB, so
    they neither seed nor get a default allowlist. Best-effort — never blocks
    startup and a read-only role simply no-ops.
    """
    if config.QUEST_ROLE == "child":
        return
    if not QUEST_ADMIN_ALLOWLIST:
        return
    try:
        admins_repo.ensure_schema()
        added = admins_repo.seed(QUEST_ADMIN_ALLOWLIST)
        if added:
            logger.info("seeded %d admin(s) from QUEST_ADMIN_ALLOWLIST", added)
        _invalidate_admin_cache()
    except Exception as exc:  # noqa: BLE001 - never block startup
        logger.warning("admin seed skipped: %s", exc)


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health():
    # Lakebase round-trip with a measured latency, so an operator can see slow
    # DB as "degraded" rather than a binary up/down.
    db_ok = False
    db_latency_ms: Optional[float] = None
    started = time.time()
    try:
        result = execute_query("SELECT 1 AS ok")
        db_ok = len(result) > 0
        db_latency_ms = round((time.time() - started) * 1000, 1)
    except Exception:
        db_ok = False

    # GameDay migration status — empty when migrations have not run yet or
    # Lakebase is unavailable, so health never fails because of this.
    migrations = db.applied_migrations()

    # Validator + scoring subsystem indicators (no DB writes).
    validator_types = default_engine.supported_types()
    try:
        from services.sdk_checks import known_checks
        sdk_checks = known_checks()
    except Exception:  # noqa: BLE001
        sdk_checks = []
    warehouse_configured = bool(os.getenv("QUEST_SQL_WAREHOUSE_ID", "").strip())

    checks = {
        "lakebase": {"ok": db_ok, "latency_ms": db_latency_ms},
        "migrations": {"ok": bool(migrations), "applied": len(migrations)},
        "validators": {"ok": bool(validator_types), "types": validator_types, "sdk_checks": sdk_checks},
        "scoring": {"ok": db_ok},  # scoring needs the ledger (Lakebase)
        "sql_warehouse": {"ok": warehouse_configured, "configured": warehouse_configured},
    }
    # Overall status: degraded if the DB is down; healthy otherwise. The
    # warehouse being unset is informational (dry-run still works), not degraded.
    status = "ok" if db_ok else "degraded"

    return {
        "status": status,
        "db_connected": db_ok,
        "db_latency_ms": db_latency_ms,
        "migrations_applied": migrations,
        "migrations_count": len(migrations),
        "validator_types": validator_types,
        "checks": checks,
        "event_mode": config.event_mode_enabled(),
        "role": config.QUEST_ROLE,
        "federation": config.summary(),
        "timestamp": datetime.utcnow().isoformat(),
    }


@app.get("/api/profile")
async def get_profile(request: Request):
    user = get_user_email(request)
    try:
        profiles = execute_query(
            "SELECT * FROM user_profile_snapshot WHERE user_id = %s", (user,)
        )
        if profiles:
            profile = profiles[0]
            profile["level_progress"] = get_level_progress(int(profile.get("total_points", 0)))
            badges = execute_query(
                "SELECT * FROM badges WHERE user_id = %s ORDER BY earned_at DESC", (user,)
            )
            profile["badges"] = badges
            profile["is_admin"] = is_admin_user(user)
            return profile
        return _empty_profile(user)
    except Exception as e:
        logger.warning(f"Profile fetch error: {e}")
        result = _empty_profile(user)
        result["setup_required"] = True
        return result


def _empty_profile(user: str) -> dict:
    return {
        "user_id": user,
        "display_name": user.split("@")[0] if "@" in user else user,
        "total_points": 0,
        "level": "Bronze",
        "level_progress": get_level_progress(0),
        "current_streak": 0,
        "max_streak": 0,
        "badge_count": 0,
        "missions_completed": 0,
        "distinct_products_used": 0,
        "badges": [],
        "is_admin": is_admin_user(user),
    }


@app.get("/api/missions")
async def get_missions(request: Request):
    user = get_user_email(request)
    missions = [m.copy() for m in MISSION_DEFINITIONS]
    try:
        completions = execute_query(
            "SELECT mission_id, completed_at, points_awarded FROM mission_completions WHERE user_id = %s",
            (user,),
        )
        completed_map = {c["mission_id"]: c for c in completions}
    except Exception:
        completed_map = {}

    for m in missions:
        if m["id"] in completed_map:
            m["status"] = "completed"
            m["completed_at"] = completed_map[m["id"]].get("completed_at")
        else:
            m["status"] = "available"
    return {"missions": missions, "user_id": user}


# --- Get Started training: self-attested course completion (tick-box) --------
# The 10 Get Started course missions are self-reported: a user ticks a course and
# confirms, and points are awarded immediately. Durability across the 4-hourly
# scoring rebuild is handled by ALSO appending to the training_completions feed,
# which scoring_pipeline.py Step 2b re-derives idempotently (same mission_id, same
# award) so a tick is never dropped or double-counted.
_TRAINING_MISSIONS = {m["id"]: m for m in MISSION_DEFINITIONS if m.get("detection") == "training"}
# The free self-paced course id the tick attests against == the first path segment
# of each mission's doc_url (…/courses/<id>/<slug>).
def _course_id_for(mission: dict) -> str:
    try:
        return mission["doc_url"].split("/courses/")[1].split("/")[0]
    except (KeyError, IndexError):
        return ""


class AttestPayload(BaseModel):
    course_mission_id: str


@app.post("/api/training/attest")
async def attest_training(payload: AttestPayload, request: Request):
    """Self-attest completion of a Get Started course. Awards points instantly and
    records the attestation in the durable feed. Idempotent; Lakebase-only."""
    user = get_user_email(request)
    mission = _TRAINING_MISSIONS.get(payload.course_mission_id)
    if mission is None:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "UNKNOWN_MISSION", "message": "Not a Get Started training mission."}},
        )
    # Writes require the Lakebase backend; the warehouse backend is read-only.
    if db.warehouse_backend():
        raise HTTPException(
            status_code=409,
            detail={"error": {"code": "WAREHOUSE_READ_ONLY",
                              "message": "Self-reporting course completions requires the Lakebase data backend. "
                                         "Ask an admin to switch the backend in Admin settings."}},
        )

    mission_id = mission["id"]
    points = int(mission["points"])
    mission_name = mission["name"]
    course_id = _course_id_for(mission)
    display_name = user.split("@")[0] if "@" in user else user

    # The Databricks Learner bonus is DERIVED (awarded automatically at >= 2 completed
    # courses), not a course a user can tick. It has no Academy course id, so a direct
    # attest would write it into training_attestations with course_id='' — which never
    # matches any course in scoring, so its points would be wiped on the next sync and
    # never re-derived. Reject direct attest of the bonus (or any training mission
    # missing a course id) so it can only ever be earned through the derived path.
    if mission_id == "databricks_learner" or not course_id:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "NOT_ATTESTABLE",
                              "message": "This reward is earned automatically, not self-reported."}},
        )

    try:
        already = execute_query(
            "SELECT 1 AS ok FROM mission_completions WHERE user_id = %s AND mission_id = %s",
            (user, mission_id),
        )
        newly_awarded = []  # (mission_id, mission_name, points) written this call

        with db.transaction() as cur:
            # 1) durable record → training_attestations (Lakebase). This table is
            #    NEVER truncated by the scoring rebuild; the roundtrip_attestations
            #    job task rolls it into Delta training_completions each cycle so
            #    scoring reconciles it. Idempotent on (user_id, course_mission_id).
            cur.execute(
                "INSERT INTO training_attestations (user_id, course_mission_id, course_id, attested_at) "
                "VALUES (%s, %s, %s, now()) "
                "ON CONFLICT (user_id, course_mission_id) DO NOTHING",
                (user, mission_id, course_id),
            )
            # rowcount == 0 means this course was already attested by this user on a
            # prior call. training_attestations is never truncated, so this is the
            # DURABLE idempotency signal: gate the instant award on it so a re-tick
            # can't double-credit points even if the scoring rebuild has transiently
            # cleared the (unconstrained) mission_completions serving row.
            newly_attested = cur.rowcount == 1

            # 2) instant serving award — only for a genuinely new tick that isn't
            #    already completed (one-time).
            if newly_attested and not already:
                _award_mission_tx(cur, user, mission_id, mission_name, points)
                newly_awarded.append((mission_id, mission_name, points))

                # 3) Databricks Learner bonus at >= 2 distinct completed Get Started courses
                learner = _TRAINING_MISSIONS.get("databricks_learner")
                if learner:
                    cur.execute(
                        "SELECT COUNT(*) AS c FROM mission_completions "
                        "WHERE user_id = %s AND mission_id IN %s",
                        (user, tuple(mid for mid in _TRAINING_MISSIONS if mid != "databricks_learner")),
                    )
                    distinct_done = cur.fetchone()[0]
                    cur.execute(
                        "SELECT 1 FROM mission_completions WHERE user_id = %s AND mission_id = 'databricks_learner'",
                        (user,),
                    )
                    has_learner = cur.fetchone() is not None
                    if distinct_done >= 2 and not has_learner:
                        lpts = int(learner["points"])
                        _award_mission_tx(cur, user, "databricks_learner", learner["name"], lpts)
                        newly_awarded.append(("databricks_learner", learner["name"], lpts))

            # 4) roll the awarded points into this user's profile + leaderboard rows
            total_new = sum(p for _, _, p in newly_awarded)
            if total_new:
                _bump_user_totals_tx(cur, user, display_name, total_new, len(newly_awarded))

        new_total = _current_total_points(user)
        return {
            "ok": True,
            "mission_id": mission_id,
            "status": "completed",
            "awarded": [{"mission_id": m, "name": n, "points": p} for m, n, p in newly_awarded],
            "points_added": sum(p for _, _, p in newly_awarded),
            "total_points": new_total,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("attest_training failed for %s / %s: %s", user, payload.course_mission_id, e)
        raise HTTPException(
            status_code=503,
            detail={"error": {"code": "ATTEST_FAILED", "message": "Could not record completion. Try again."}},
        )


def _award_mission_tx(cur, user: str, mission_id: str, mission_name: str, points: int) -> None:
    """Insert a one-time completion + its points-fact row inside a transaction.

    The base serving-table DDL (deploy.sh) has NO unique constraint on
    mission_completions/user_points_fact — the scoring pipeline keeps them clean by
    full DELETE+reinsert, not upserts. So we cannot use ON CONFLICT here; instead
    each INSERT is guarded by WHERE NOT EXISTS so a retry (or a caller that already
    checked) never writes a duplicate completion or double-credits the points.
    """
    now_ts = datetime.utcnow()
    cur.execute(
        "INSERT INTO mission_completions "
        "(user_id, mission_id, mission_name, points_awarded, completed_at, period_start, period_end, scored_at) "
        "SELECT %s, %s, %s, %s, %s, %s, %s, %s "
        "WHERE NOT EXISTS (SELECT 1 FROM mission_completions WHERE user_id = %s AND mission_id = %s)",
        (user, mission_id, mission_name, points, now_ts, now_ts.date(), now_ts.date(), now_ts, user, mission_id),
    )
    cur.execute(
        "INSERT INTO user_points_fact (user_id, event_type, mission_id, points, reason, event_timestamp, scored_at) "
        "SELECT %s, 'mission_completion', %s, %s, %s, %s, %s "
        "WHERE NOT EXISTS (SELECT 1 FROM user_points_fact WHERE user_id = %s AND mission_id = %s AND event_type = 'mission_completion')",
        (user, mission_id, points, f"Completed mission: {mission_name}", now_ts, now_ts, user, mission_id),
    )


def _bump_user_totals_tx(cur, user: str, display_name: str, added_points: int, added_missions: int = 1) -> None:
    """Increment the user's total on profile + leaderboard so points show instantly.
    `added_missions` is the number of missions completed in this call (a course tick
    plus the Learner bonus = 2), so missions_completed doesn't undercount. Ranks stay
    approximate until the next scoring run recomputes them globally."""
    now_ts = datetime.utcnow()
    # profile snapshot
    cur.execute("SELECT total_points FROM user_profile_snapshot WHERE user_id = %s", (user,))
    row = cur.fetchone()
    if row:
        new_total = int(row[0] or 0) + added_points
        cur.execute(
            "UPDATE user_profile_snapshot SET total_points = %s, level = %s, "
            "missions_completed = COALESCE(missions_completed, 0) + %s, updated_at = %s WHERE user_id = %s",
            (new_total, get_level(new_total), added_missions, now_ts, user),
        )
    else:
        new_total = added_points
        cur.execute(
            "INSERT INTO user_profile_snapshot "
            "(user_id, display_name, total_points, level, current_streak, max_streak, badge_count, "
            "missions_completed, distinct_products_used, updated_at) "
            "VALUES (%s, %s, %s, %s, 0, 0, 0, %s, 0, %s)",
            (user, display_name, new_total, get_level(new_total), added_missions, now_ts),
        )
    # leaderboard row (weekly/monthly also credited; ranks fixed on next scoring run)
    cur.execute("SELECT total_points FROM leaderboard WHERE user_id = %s", (user,))
    lrow = cur.fetchone()
    if lrow:
        lt = int(lrow[0] or 0) + added_points
        cur.execute(
            "UPDATE leaderboard SET total_points = %s, weekly_points = COALESCE(weekly_points,0) + %s, "
            "monthly_points = COALESCE(monthly_points,0) + %s, level = %s, updated_at = %s WHERE user_id = %s",
            (lt, added_points, added_points, get_level(lt), now_ts, user),
        )
    else:
        cur.execute(
            "INSERT INTO leaderboard "
            "(user_id, display_name, total_points, weekly_points, monthly_points, level, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (user, display_name, added_points, added_points, added_points, get_level(added_points), now_ts),
        )


def _current_total_points(user: str) -> int:
    try:
        rows = execute_query("SELECT total_points FROM user_profile_snapshot WHERE user_id = %s", (user,))
        return int(rows[0]["total_points"]) if rows else 0
    except Exception:
        return 0


@app.get("/api/leaderboard")
async def get_leaderboard(request: Request, period: str = "all", limit: int = 50):
    # Return the top `limit` rows PLUS the caller's own row (as `me`) even when
    # they rank outside the top N — otherwise ~99% of users never see their rank.
    valid_periods = {"weekly": "weekly_rank", "monthly": "monthly_rank", "all": "all_time_rank"}
    order_col = valid_periods.get(period, "all_time_rank")
    try:
        limit = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        limit = 50
    try:
        rows = execute_query(
            f"""
            SELECT user_id, display_name, total_points, weekly_points, monthly_points,
                   level, all_time_rank, weekly_rank, monthly_rank
            FROM leaderboard
            ORDER BY {order_col} ASC
            LIMIT {limit}
            """
        )
        me = None
        try:
            user = get_user_email(request)
            if user:
                in_top = any((r.get("user_id") or "").lower() == user.lower() for r in rows)
                if not in_top:
                    my_rows = execute_query(
                        """
                        SELECT user_id, display_name, total_points, weekly_points, monthly_points,
                               level, all_time_rank, weekly_rank, monthly_rank
                        FROM leaderboard WHERE lower(user_id) = lower(%s) LIMIT 1
                        """,
                        (user,),
                    )
                    me = my_rows[0] if my_rows else None
        except Exception as me_err:
            logger.warning(f"Leaderboard self-row lookup failed: {me_err}")
        return {"leaderboard": rows, "period": period, "me": me}
    except Exception as e:
        logger.warning(f"Leaderboard error: {e}")
        return {"leaderboard": [], "period": period, "me": None}


@app.get("/api/notifications")
async def get_notifications(request: Request):
    user = get_user_email(request)
    try:
        rows = execute_query(
            """
            SELECT notification_type, title, message, mission_id, points, created_at
            FROM notifications
            WHERE user_id = %s
            ORDER BY created_at DESC
            LIMIT 20
            """,
            (user,),
        )
        return {"notifications": rows}
    except Exception:
        return {"notifications": []}


@app.get("/api/admin/stats")
async def get_admin_stats(user: str = Depends(require_admin)):
    try:
        user_count = execute_query("SELECT COUNT(DISTINCT user_id) AS cnt FROM user_profile_snapshot")
        mission_count = execute_query("SELECT COUNT(*) AS cnt FROM mission_completions")
        top_missions = execute_query(
            """
            SELECT mission_id, mission_name, COUNT(*) AS completions
            FROM mission_completions
            GROUP BY mission_id, mission_name
            ORDER BY completions DESC
            """
        )
        level_dist = execute_query(
            """
            SELECT level, COUNT(*) AS cnt
            FROM user_profile_snapshot
            GROUP BY level
            ORDER BY cnt DESC
            """
        )
        latest_run = execute_query(
            "SELECT MAX(updated_at) AS last_refresh FROM user_profile_snapshot"
        )
        return {
            "total_users": user_count[0]["cnt"] if user_count else 0,
            "total_mission_completions": mission_count[0]["cnt"] if mission_count else 0,
            "top_missions": top_missions,
            "level_distribution": level_dist,
            "last_refresh": latest_run[0]["last_refresh"] if latest_run else None,
        }
    except Exception as e:
        logger.warning(f"Admin stats error: {e}")
        return {
            "total_users": 0,
            "total_mission_completions": 0,
            "top_missions": [],
            "level_distribution": [],
            "last_refresh": None,
            "setup_required": True,
        }


@app.get("/api/admin/pipeline-status")
async def get_pipeline_status(user: str = Depends(require_admin)):
    try:
        latest = execute_query("SELECT MAX(updated_at) AS last_run FROM user_profile_snapshot")
        last_run = latest[0]["last_run"] if latest and latest[0].get("last_run") else None
        total = execute_query("SELECT COUNT(*) AS cnt FROM user_points_fact")
        return {
            "status": "healthy" if last_run else "not_initialized",
            "last_run": last_run,
            "total_events_scored": total[0]["cnt"] if total else 0,
        }
    except Exception:
        return {"status": "not_initialized", "last_run": None, "total_events_scored": 0}


# ---------------------------------------------------------------------------
# Data backend toggle (admin) — both Lakebase and a SQL warehouse are
# provisioned; admins switch which one serves adoption data, at runtime.
# ---------------------------------------------------------------------------

class DataBackendPayload(BaseModel):
    backend: str


@app.get("/api/admin/data-backend")
async def get_data_backend_setting(user: str = Depends(require_admin)):
    try:
        active = db.get_data_backend()
    except Exception:
        active = db.QUEST_DATA_BACKEND_DEFAULT
    return {
        "backend": active,
        "default": db.QUEST_DATA_BACKEND_DEFAULT,
        "options": list(db._VALID_BACKENDS),
        "warehouse_configured": db.warehouse_ready(),
    }


@app.post("/api/admin/data-backend")
async def set_data_backend_setting(payload: DataBackendPayload, user: str = Depends(require_admin)):
    """Switch the active data backend (lakebase|warehouse). Persisted; takes
    effect within ~30s (cache TTL). Warehouse mode serves all adoption data from
    Delta via the SQL warehouse, bypassing Lakebase."""
    backend = (payload.backend or "").strip().lower()
    if backend not in db._VALID_BACKENDS:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "INVALID_BACKEND",
                              "message": f"backend must be one of {list(db._VALID_BACKENDS)}"}},
        )
    if backend == "warehouse" and not db.warehouse_ready():
        raise HTTPException(
            status_code=409,
            detail={"error": {"code": "WAREHOUSE_NOT_CONFIGURED",
                              "message": "No SQL warehouse (or QUEST_CATALOG) is configured for this deployment."}},
        )
    try:
        active = db.set_data_backend(backend)
    except Exception as exc:  # noqa: BLE001
        logger.warning("set_data_backend failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={"error": {"code": "BACKEND_WRITE_FAILED",
                              "message": f"Could not persist the backend setting: {str(exc)[:300]}"}},
        )
    return {"backend": active, "changed_by": user}


# ---------------------------------------------------------------------------
# Admin allowlist management — DB-backed, shared across master/child apps.
# Always available (not Event-Mode gated), admin-only. The env allowlist is the
# bootstrap/fallback; quest_admins in Lakebase is the durable source of truth.
# ---------------------------------------------------------------------------

class AddAdminPayload(BaseModel):
    email: str


@app.get("/api/admin/admins")
async def list_admins(user: str = Depends(require_admin)):
    """List admins: DB rows plus any env-only (seed/fallback) entries."""
    try:
        rows = admins_repo.list_admins()
    except Exception as exc:  # noqa: BLE001
        logger.warning("list_admins failed: %s", exc)
        rows = []
    db_emails = {(r.get("email") or "").lower() for r in rows}
    env_only = [
        {"email": e, "added_by": None, "source": "env", "added_at": None}
        for e in QUEST_ADMIN_ALLOWLIST
        if e not in db_emails
    ]
    return {"admins": rows + env_only, "caller": user, "caller_is_admin": True}


@app.post("/api/admin/admins")
async def add_admin(payload: AddAdminPayload, user: str = Depends(require_admin)):
    """Add an admin to the shared allowlist. Admins can grant admin."""
    email = (payload.email or "").strip().lower()
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "INVALID_EMAIL", "message": "A valid email is required."}},
        )
    try:
        admins_repo.ensure_schema()
        created = bool(admins_repo.add(email, added_by=user))
    except Exception as exc:  # noqa: BLE001
        logger.warning("add_admin failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={"error": {"code": "ADMIN_WRITE_FAILED",
                              "message": "Could not persist admin (is Lakebase writable from this app?)."}},
        )
    _invalidate_admin_cache()
    record_audit(
        action="admin.add",
        actor_user_id=user,
        event_id=None,
        target_type="admin",
        target_id=email,
        payload={"email": email, "added": created},
    )
    return {"email": email, "added": created, "added_by": user}


@app.delete("/api/admin/admins/{email}")
async def remove_admin(email: str, user: str = Depends(require_admin)):
    """Remove an admin. Refuses to remove the last DB admin (lockout guard)."""
    target = (email or "").strip().lower()
    try:
        current = {(r.get("email") or "").lower() for r in admins_repo.list_admins()}
    except Exception as exc:  # noqa: BLE001
        logger.warning("remove_admin read failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={"error": {"code": "ADMIN_READ_FAILED",
                              "message": "Could not read the admin list."}},
        )
    if target not in current:
        # Env-only admins live in deploy config, not the DB — can't remove here.
        if target in set(QUEST_ADMIN_ALLOWLIST):
            raise HTTPException(
                status_code=409,
                detail={"error": {"code": "ENV_ADMIN",
                                  "message": "This admin is set via deploy config (QUEST_ADMIN_ALLOWLIST); "
                                             "remove it from the deployment's --admins instead."}},
            )
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "NOT_FOUND", "message": "Not an admin."}},
        )
    if len(current) <= 1:
        raise HTTPException(
            status_code=409,
            detail={"error": {"code": "LAST_ADMIN", "message": "Cannot remove the last admin."}},
        )
    try:
        admins_repo.remove(target)
    except Exception as exc:  # noqa: BLE001 - child role has no DELETE on quest_admins
        logger.warning("remove_admin write failed: %s", exc)
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": "REMOVE_NOT_PERMITTED",
                              "message": "Removing admins isn't permitted from this app. "
                                         "Use the master/host workspace app to remove admins."}},
        )
    _invalidate_admin_cache()
    record_audit(
        action="admin.remove",
        actor_user_id=user,
        event_id=None,
        target_type="admin",
        target_id=target,
        payload={"email": target},
    )
    return {"email": target, "removed": True}


# ---------------------------------------------------------------------------
# Host / admin — Quest Packs (PR02)
# ---------------------------------------------------------------------------

class QuestPackPayload(BaseModel):
    manifest_yaml: str


@app.post("/api/host/quest-packs/lint")
async def host_lint_quest_pack(body: QuestPackPayload, user: str = Depends(require_host)):
    """Lint a quest pack manifest without persisting anything."""
    return quest_pack_loader.lint_text(body.manifest_yaml)


@app.post("/api/host/quest-packs/import")
async def host_import_quest_pack(body: QuestPackPayload, user: str = Depends(require_host)):
    """Lint and import a quest pack manifest as a new immutable version."""
    try:
        return quest_pack_loader.import_text(body.manifest_yaml, actor=user)
    except QuestPackImportError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {"code": "QUEST_PACK_INVALID", "message": exc.message},
                "lint": exc.lint,
            },
        )


@app.get("/api/host/quest-packs")
async def host_list_quest_packs(user: str = Depends(require_host)):
    """List imported quest packs."""
    return {"quest_packs": quest_packs_repo.list_packs()}


@app.get("/api/host/quest-packs/{pack_id}")
async def host_get_quest_pack(pack_id: str, user: str = Depends(require_host)):
    """Get a quest pack with its versions and per-version content counts."""
    detail = quest_packs_repo.get_pack_detail(pack_id)
    if not detail:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "NOT_FOUND", "message": "Quest pack not found."}},
        )
    return detail


# ---------------------------------------------------------------------------
# Events, teams, participants — lifecycle (PR04)
# ---------------------------------------------------------------------------
# Player endpoints (list/lobby/join) are Event-Mode-gated and open to any
# authenticated user. Host endpoints (create event/teams, import participants,
# lifecycle transitions) go through require_host. Every mutation writes an
# audit row. The event status state machine lives in repositories.events.


class CreateEventPayload(BaseModel):
    title: str
    pack_version_id: str
    slug: Optional[str] = None
    description: Optional[str] = None
    timezone: str = "UTC"
    mode: str = "gameday"


class CreateTeamPayload(BaseModel):
    name: str
    display_name: Optional[str] = None
    color: Optional[str] = None
    team_catalog: Optional[str] = None
    team_schema: Optional[str] = None


class RenameTeamPayload(BaseModel):
    display_name: str
    color: Optional[str] = None
    team_catalog: Optional[str] = None
    team_schema: Optional[str] = None


class JoinEventPayload(BaseModel):
    display_name: Optional[str] = None
    team_id: Optional[str] = None
    team_name: Optional[str] = None


class ParticipantImportPayload(BaseModel):
    participants: List[Dict[str, Any]]


def _slugify(text: str) -> str:
    import re

    s = re.sub(r"[^a-z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return s or f"event-{uuid_hex8()}"


def uuid_hex8() -> str:
    import uuid as _uuid

    return _uuid.uuid4().hex[:8]


def _event_state_error(exc: EventStateError) -> HTTPException:
    return HTTPException(
        status_code=exc.status,
        detail={"error": {"code": exc.code, "message": str(exc)}},
    )


def _event_public(ev: Dict[str, Any]) -> Dict[str, Any]:
    """Trim an event row to the fields players/hosts need."""
    keys = (
        "event_id", "slug", "title", "description", "status", "mode",
        "starts_at", "ends_at", "timezone", "scoring_frozen_at",
        "pack_version_id", "created_by", "created_at",
    )
    return {k: ev.get(k) for k in keys}


@app.get("/api/events")
async def list_events(request: Request, _: None = Depends(require_event_mode)):
    """Player-facing list of joinable/visible events with team counts."""
    return {"events": events_repo.list_player_events()}


@app.get("/api/events/{event_id}")
async def get_event_lobby(event_id: str, request: Request, _: None = Depends(require_event_mode)):
    """Event lobby: event header, teams (with counts), your participant/team, counts."""
    resolved_event_id = _resolve_event_or_404(event_id)
    ev = events_repo.get_event(resolved_event_id)
    user = get_user_email(request)

    participant = events_repo.get_participant(resolved_event_id, user)
    team = events_repo.get_team_for_user(resolved_event_id, user)
    return {
        "event": _event_public(ev),
        "joinable": ev["status"] in JOINABLE_STATUSES,
        "attempts_open": attempts_open(ev["status"]),
        "is_host": is_host_user(user, resolved_event_id),
        "team_self_service": _TEAM_SELF_SERVICE,
        "teams": events_repo.list_teams_with_counts(resolved_event_id),
        "counts": events_repo.event_counts(resolved_event_id, ev.get("pack_version_id")),
        "you": {
            "joined": participant is not None,
            "participant_id": participant.get("participant_id") if participant else None,
            "team_id": team.get("team_id") if team else None,
            "team_name": team.get("display_name") or team.get("name") if team else None,
        },
    }


@app.post("/api/events/{event_id}/join")
async def join_event(
    event_id: str,
    body: JoinEventPayload,
    request: Request,
    _: None = Depends(require_event_mode),
):
    """Self-register the caller as a participant; optionally join a named team."""
    resolved_event_id = _resolve_event_or_404(event_id)
    ev = events_repo.get_event(resolved_event_id)
    user = get_user_email(request)
    if ev["status"] not in JOINABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail={"error": {"code": "NOT_JOINABLE", "message": "This event is not open for joining right now."}},
        )

    try:
        participant = events_repo.register_participant(
            event_id=resolved_event_id, user_id=user, display_name=body.display_name, email=user
        )
        team = None
        if body.team_id or body.team_name:
            team = (
                events_repo.get_team(body.team_id)
                if body.team_id
                else _find_team_by_name(resolved_event_id, body.team_name)
            )
            if not team or team.get("event_id") != resolved_event_id:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": "TEAM_NOT_FOUND", "message": "That team is not part of this event."}},
                )
            events_repo.set_participant_team(
                resolved_event_id, participant["participant_id"], team["team_id"]
            )
    except EventStateError as exc:
        raise _event_state_error(exc)

    record_audit(
        action="event.join",
        actor_user_id=user,
        event_id=resolved_event_id,
        target_type="participant",
        target_id=participant["participant_id"],
        payload={"team_id": team["team_id"] if team else None},
    )
    return {
        "joined": True,
        "participant_id": participant["participant_id"],
        "team_id": team["team_id"] if team else None,
        "team_name": (team.get("display_name") or team.get("name")) if team else None,
    }


# Player team self-service: create/rename a team from the lobby. Defaults on;
# set QUEST_TEAM_SELF_SERVICE=0 to require hosts to pre-create every team.
_TEAM_SELF_SERVICE = os.getenv("QUEST_TEAM_SELF_SERVICE", "1").strip().lower() not in (
    "0", "false", "no", "off",
)


@app.post("/api/events/{event_id}/teams")
async def player_create_team(
    event_id: str,
    body: CreateTeamPayload,
    request: Request,
    _: None = Depends(require_event_mode),
):
    """Player self-service: create a team and join it (gated to joinable events).

    Distinct from the host endpoint (``/api/host/events/{id}/teams``): this is
    open to any authenticated player while the event is joinable and self-service
    is enabled, and it places the caller on the new team (single-team invariant).
    """
    if not _TEAM_SELF_SERVICE:
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": "SELF_SERVICE_DISABLED", "message": "Team self-service is turned off for this deployment."}},
        )
    resolved_event_id = _resolve_event_or_404(event_id)
    ev = events_repo.get_event(resolved_event_id)
    user = get_user_email(request)
    if ev["status"] not in JOINABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail={"error": {"code": "NOT_JOINABLE", "message": "This event is not open for joining right now."}},
        )
    try:
        team = events_repo.create_team(
            event_id=resolved_event_id, name=body.name, display_name=body.display_name
        )
        participant = events_repo.register_participant(
            event_id=resolved_event_id, user_id=user, email=user
        )
        events_repo.set_participant_team(
            resolved_event_id, participant["participant_id"], team["team_id"]
        )
    except EventStateError as exc:
        raise _event_state_error(exc)
    record_audit(
        action="team.self_create",
        actor_user_id=user,
        event_id=resolved_event_id,
        target_type="team",
        target_id=team["team_id"],
        payload={"name": team.get("name"), "joined": True},
    )
    return {"team": _team_public(team), "joined": True}


@app.post("/api/events/{event_id}/team/rename")
async def player_rename_team(
    event_id: str,
    body: RenameTeamPayload,
    request: Request,
    _: None = Depends(require_event_mode),
):
    """Player self-service: rename the caller's own team (display name only)."""
    if not _TEAM_SELF_SERVICE:
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": "SELF_SERVICE_DISABLED", "message": "Team self-service is turned off for this deployment."}},
        )
    resolved_event_id = _resolve_event_or_404(event_id)
    ev = events_repo.get_event(resolved_event_id)
    user = get_user_email(request)
    if ev["status"] not in JOINABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail={"error": {"code": "NOT_JOINABLE", "message": "Renaming is closed for this event right now."}},
        )
    team = events_repo.get_team_for_user(resolved_event_id, user)
    if not team:
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": "NOT_ON_TEAM", "message": "Join a team before renaming it."}},
        )
    try:
        updated = events_repo.rename_team(team["team_id"], body.display_name)
    except EventStateError as exc:
        raise _event_state_error(exc)
    record_audit(
        action="team.self_rename",
        actor_user_id=user,
        event_id=resolved_event_id,
        target_type="team",
        target_id=team["team_id"],
        payload={"display_name": updated.get("display_name")},
    )
    return {"team": _team_public(updated)}


def _find_team_by_name(event_id: str, name: Optional[str]) -> Optional[Dict[str, Any]]:
    if not name:
        return None
    for t in events_repo.list_teams(event_id):
        if t.get("name") == name or t.get("display_name") == name:
            return t
    return None


def _team_public(team: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "team_id": team.get("team_id"),
        "name": team.get("name"),
        "display_name": team.get("display_name") or team.get("name"),
        "color": team.get("color"),
    }


@app.get("/api/events/{event_id}/team")
async def get_event_team(event_id: str, request: Request, _: None = Depends(require_event_mode)):
    """Team gameplay dashboard: team, members, score, rank, progress, recent scoring."""
    resolved_event_id = _resolve_event_or_404(event_id)
    user = get_user_email(request)
    participant = events_repo.get_participant(resolved_event_id, user)
    team = events_repo.get_team_for_user(resolved_event_id, user)
    if not team:
        return {"joined": participant is not None, "team": None}

    team_id = team["team_id"]
    ev = events_repo.get_event(resolved_event_id)
    completed = leaderboard_repo.completed_task_ids(resolved_event_id, team_id)
    counts = events_repo.event_counts(resolved_event_id, ev.get("pack_version_id") if ev else None)
    recent = [
        r for r in leaderboard_repo.list_recent_scoring_events(resolved_event_id, limit=50)
        if r.get("team_id") == team_id
    ][:10]
    return {
        "joined": True,
        "team": _team_public(team),
        "members": [
            {"user_id": m.get("user_id"), "display_name": m.get("display_name"), "role": m.get("role")}
            for m in events_repo.list_team_members(team_id)
        ],
        "score": leaderboard_repo.get_team_score(resolved_event_id, team_id),
        "rank": leaderboard_repo.get_team_rank(resolved_event_id, team_id),
        "completed_task_ids": completed,
        "progress": {"completed_tasks": len(completed), "total_tasks": counts.get("tasks", 0)},
        "recent": recent,
        "attempts_open": attempts_open(ev["status"]) if ev else False,
    }


@app.get("/api/events/{event_id}/quests")
async def list_event_quests(event_id: str, request: Request, _: None = Depends(require_event_mode)):
    """Event quests with per-quest task counts and the caller team's progress."""
    resolved_event_id = _resolve_event_or_404(event_id)
    ev = events_repo.get_event(resolved_event_id)
    pack_version_id = ev.get("pack_version_id") if ev else None
    user = get_user_email(request)
    team = events_repo.get_team_for_user(resolved_event_id, user)
    completed = set(
        leaderboard_repo.completed_task_ids(resolved_event_id, team["team_id"]) if team else []
    )

    quests = quest_packs_repo.list_quests(pack_version_id) if pack_version_id else []
    out = []
    for q in quests:
        tasks = quest_packs_repo.list_tasks(q["quest_id"])
        total = len(tasks)
        done = sum(1 for t in tasks if t.get("task_id") in completed)
        out.append({
            **q,
            "task_count": total,
            "completed_tasks": done,
            "complete": total > 0 and done == total,
        })
    return {
        "quests": out,
        "team_id": team["team_id"] if team else None,
        "attempts_open": attempts_open(ev["status"]) if ev else False,
    }


@app.get("/api/events/{event_id}/quests/{quest_id}")
async def get_event_quest(event_id: str, quest_id: str, request: Request, _: None = Depends(require_event_mode)):
    """Quest detail/runner: narrative, tasks (instructions, hints), completion."""
    resolved_event_id = _resolve_event_or_404(event_id)
    ev = events_repo.get_event(resolved_event_id)
    quest = quest_packs_repo.get_quest(quest_id)
    if not quest or (ev and quest.get("pack_version_id") != ev.get("pack_version_id")):
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "NOT_FOUND", "message": "Quest not found in this event."}},
        )
    user = get_user_email(request)
    team = events_repo.get_team_for_user(resolved_event_id, user)
    completed = set(
        leaderboard_repo.completed_task_ids(resolved_event_id, team["team_id"]) if team else []
    )

    revealed = set(
        leaderboard_repo.revealed_hint_ids(resolved_event_id, team["team_id"]) if team else []
    )

    tasks = []
    for t in quest_packs_repo.list_tasks_detail(quest_id):
        tid = t.get("task_id")
        tasks.append({
            **t,
            "complete": tid in completed,
            "hints": [
                {"hint_id": h.get("hint_id"), "title": h.get("title"),
                 # Body is withheld until the team reveals (and is charged for)
                 # the hint — otherwise the penalty would be free to dodge.
                 "body_md": h.get("body_md") if h.get("hint_id") in revealed else None,
                 "penalty_points": h.get("penalty_points"),
                 "sort_order": h.get("sort_order"),
                 "revealed": h.get("hint_id") in revealed}
                for h in quest_packs_repo.list_hints(tid)
            ],
        })
    return {
        "quest": quest,
        "tasks": tasks,
        "team_id": team["team_id"] if team else None,
        "attempts_open": attempts_open(ev["status"]) if ev else False,
    }


@app.get("/api/events/{event_id}/announcements")
async def list_event_announcements(event_id: str, request: Request, _: None = Depends(require_event_mode)):
    """Player-facing announcement feed for an event (host broadcasts)."""
    resolved_event_id = _resolve_event_or_404(event_id)
    return {"announcements": announcements_repo.list_for_event(resolved_event_id, limit=20)}


def _scoring_frozen(ev: Dict[str, Any]) -> bool:
    """True when an event no longer accepts new player scoring.

    Frozen and completed events are locked; an explicit ``scoring_frozen_at``
    timestamp also locks scoring even while the event is otherwise active.
    """
    return ev["status"] in ("frozen", "completed", "archived") or bool(
        ev.get("scoring_frozen_at")
    )


@app.get("/api/events/{event_id}/leaderboard")
async def get_event_leaderboard(
    event_id: str, request: Request, _: None = Depends(require_event_mode)
):
    """Player-facing live leaderboard for an event.

    Returns the ranked teams (deterministic tie-break: higher points first, then
    earliest to reach that total), a recent scoring activity feed, the frozen/
    final flag, and the caller's own team rank highlight.
    """
    resolved_event_id = _resolve_event_or_404(event_id)
    ev = events_repo.get_event(resolved_event_id)
    if not ev:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "NOT_FOUND", "message": "Event not found."}},
        )

    rows = leaderboard_repo.get_team_leaderboard(resolved_event_id)

    user = get_user_email(request)
    identity = _resolve_attempt_identity(resolved_event_id, user)
    team_id = identity.get("team_id")
    you = None
    if team_id:
        you = next((r for r in rows if r.get("team_id") == team_id), None)
        if you is None:
            # On a team but unscored yet — surface with a null rank.
            team_row = identity.get("team_row") or events_repo.get_team(team_id)
            you = {
                "event_id": resolved_event_id,
                "team_id": team_id,
                "display_name": (team_row or {}).get("display_name"),
                "total_points": 0,
                "rank": None,
            }

    return {
        "event": _event_public(ev),
        "frozen": _scoring_frozen(ev),
        "status": ev["status"],
        "leaderboard": rows,
        "recent": leaderboard_repo.recent_scoring_feed(resolved_event_id, limit=25),
        "you": you,
    }


@app.post("/api/host/events")
async def host_create_event(body: CreateEventPayload, user: str = Depends(require_host)):
    """Create a draft event from an imported pack version (creator becomes owner-host)."""
    slug = (body.slug or _slugify(body.title)).strip().lower()
    try:
        ev = events_repo.create_event(
            slug=slug,
            title=body.title,
            pack_version_id=body.pack_version_id,
            created_by=user,
            description=body.description,
            mode=body.mode,
            timezone=body.timezone,
        )
    except EventStateError as exc:
        raise _event_state_error(exc)

    record_audit(
        action="event.create",
        actor_user_id=user,
        event_id=ev["event_id"],
        target_type="event",
        target_id=ev["event_id"],
        payload={"slug": ev["slug"], "pack_version_id": body.pack_version_id},
    )
    return {"event": _event_public(ev)}


@app.post("/api/host/events/{event_id}/teams")
async def host_create_team(
    event_id: str, body: CreateTeamPayload, user: str = Depends(require_host)
):
    """Create a team in an event."""
    resolved_event_id = _resolve_event_or_404(event_id)
    try:
        team = events_repo.create_team(
            event_id=resolved_event_id,
            name=body.name,
            display_name=body.display_name,
            color=body.color,
            team_catalog=body.team_catalog,
            team_schema=body.team_schema,
        )
    except EventStateError as exc:
        raise _event_state_error(exc)

    record_audit(
        action="team.create",
        actor_user_id=user,
        event_id=resolved_event_id,
        target_type="team",
        target_id=team["team_id"],
        payload={"name": team["name"]},
    )
    return {"team": team}


class TeamAssignPayload(BaseModel):
    user_id: Optional[str] = None
    participant_id: Optional[str] = None
    display_name: Optional[str] = None


@app.post("/api/host/events/{event_id}/teams/{team_id}/members")
async def host_assign_team_member(
    event_id: str,
    team_id: str,
    body: TeamAssignPayload,
    user: str = Depends(require_host),
):
    """Assign a participant to a team (single team per event; reassigns if moved).

    Accepts an existing ``participant_id`` or a ``user_id`` (registered on demand
    so a host can place someone who hasn't self-joined yet).
    """
    resolved_event_id = _resolve_event_or_404(event_id)
    team = events_repo.get_team(team_id)
    if not team or team.get("event_id") != resolved_event_id:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "TEAM_NOT_FOUND", "message": "That team is not part of this event."}},
        )

    try:
        if body.participant_id:
            participant = db.execute_query(
                "SELECT * FROM participants WHERE participant_id = %s AND event_id = %s",
                (body.participant_id, resolved_event_id),
            )
            participant = participant[0] if participant else None
        elif body.user_id:
            participant = events_repo.register_participant(
                event_id=resolved_event_id,
                user_id=body.user_id,
                display_name=body.display_name,
                email=body.user_id,
            )
        else:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": "INVALID_ASSIGN", "message": "Provide a user_id or participant_id."}},
            )
        if not participant:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": "PARTICIPANT_NOT_FOUND", "message": "That participant is not in this event."}},
            )
        events_repo.set_participant_team(
            resolved_event_id, participant["participant_id"], team_id
        )
    except EventStateError as exc:
        raise _event_state_error(exc)

    record_audit(
        action="team.assign",
        actor_user_id=user,
        event_id=resolved_event_id,
        target_type="team",
        target_id=team_id,
        payload={"participant_id": participant["participant_id"]},
    )
    return {
        "assigned": True,
        "team_id": team_id,
        "participant_id": participant["participant_id"],
    }


@app.post("/api/host/events/{event_id}/participants/import")
async def host_import_participants(
    event_id: str, body: ParticipantImportPayload, user: str = Depends(require_host)
):
    """Bulk-register participants with optional team assignment (idempotent)."""
    resolved_event_id = _resolve_event_or_404(event_id)
    try:
        result = events_repo.import_participants(resolved_event_id, body.participants)
    except EventStateError as exc:
        raise _event_state_error(exc)

    record_audit(
        action="participants.import",
        actor_user_id=user,
        event_id=resolved_event_id,
        target_type="event",
        target_id=resolved_event_id,
        payload=result,
    )
    return result


class AddHostPayload(BaseModel):
    email: str
    role: str = "host"


@app.get("/api/host/events/{event_id}/hosts")
async def host_list_hosts(event_id: str, user: str = Depends(require_host)):
    """List the hosts (event_hosts rows) for an event."""
    resolved_event_id = _resolve_event_or_404(event_id)
    return {"hosts": events_repo.list_event_hosts(resolved_event_id)}


@app.post("/api/host/events/{event_id}/hosts")
async def host_add_host(
    event_id: str, body: AddHostPayload, user: str = Depends(require_host)
):
    """Grant a user the host role for an event (audited)."""
    resolved_event_id = _resolve_event_or_404(event_id)
    email = (body.email or "").strip().lower()
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "INVALID_EMAIL", "message": "A valid email is required."}},
        )
    try:
        created = events_repo.add_host(resolved_event_id, email, role=body.role)
    except EventStateError as exc:
        raise _event_state_error(exc)
    record_audit(
        action="host.add",
        actor_user_id=user,
        event_id=resolved_event_id,
        target_type="event_host",
        target_id=email,
        payload={"email": email, "role": body.role, "added": created},
    )
    return {"email": email, "role": body.role, "added": created}


@app.delete("/api/host/events/{event_id}/hosts/{email}")
async def host_remove_host(event_id: str, email: str, user: str = Depends(require_host)):
    """Revoke a user's host role for an event (audited, last-owner-protected)."""
    resolved_event_id = _resolve_event_or_404(event_id)
    target = (email or "").strip().lower()
    try:
        events_repo.remove_host(resolved_event_id, target)
    except EventStateError as exc:
        raise _event_state_error(exc)
    record_audit(
        action="host.remove",
        actor_user_id=user,
        event_id=resolved_event_id,
        target_type="event_host",
        target_id=target,
        payload={"email": target},
    )
    return {"email": target, "removed": True}


def _transition_endpoint(target: str):
    async def _do(event_id: str, request: Request, user: str = Depends(require_host)):
        resolved_event_id = _resolve_event_or_404(event_id)
        try:
            ev = events_repo.set_status(resolved_event_id, target, user)
        except EventStateError as exc:
            raise _event_state_error(exc)
        record_audit(
            action=f"event.{target}",
            actor_user_id=user,
            event_id=resolved_event_id,
            target_type="event",
            target_id=resolved_event_id,
            payload={"status": ev["status"]},
        )
        return {"event": _event_public(ev)}

    return _do


# Map the spec's verbs to target statuses. Each writes an audit row.
app.add_api_route("/api/host/events/{event_id}/start", _transition_endpoint("active"), methods=["POST"])
app.add_api_route("/api/host/events/{event_id}/pause", _transition_endpoint("paused"), methods=["POST"])
app.add_api_route("/api/host/events/{event_id}/freeze", _transition_endpoint("frozen"), methods=["POST"])
app.add_api_route("/api/host/events/{event_id}/complete", _transition_endpoint("completed"), methods=["POST"])
app.add_api_route("/api/host/events/{event_id}/ready", _transition_endpoint("ready"), methods=["POST"])
app.add_api_route("/api/host/events/{event_id}/archive", _transition_endpoint("archived"), methods=["POST"])


# ---------------------------------------------------------------------------
# Host console — dashboard, teams, attempts, announcements, adjustments (PR06)
# ---------------------------------------------------------------------------


class AnnouncementPayload(BaseModel):
    title: str
    body_md: str
    severity: str = "info"


class AdjustmentPayload(BaseModel):
    team_id: str
    points_delta: int
    reason: str
    task_id: Optional[str] = None
    user_id: Optional[str] = None


def _host_team_rows(event_id: str) -> List[Dict[str, Any]]:
    """Teams in an event annotated with score + rank for the host table."""
    rank_by_team = {
        r["team_id"]: r.get("rank")
        for r in leaderboard_repo.get_team_leaderboard(event_id)
    }
    rows = []
    for t in events_repo.list_teams_with_counts(event_id):
        tid = t["team_id"]
        rows.append({
            **t,
            "score": leaderboard_repo.get_team_score(event_id, tid),
            "rank": rank_by_team.get(tid),
        })
    rows.sort(key=lambda r: (r["rank"] is None, r["rank"] or 0, -r["score"]))
    return rows


@app.get("/api/host/events/{event_id}")
async def host_event_overview(event_id: str, user: str = Depends(require_host)):
    """Host dashboard: event header, lifecycle, counts, teams, attempt stats."""
    resolved_event_id = _resolve_event_or_404(event_id)
    ev = events_repo.get_event(resolved_event_id)
    if not ev:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "NOT_FOUND", "message": "Event not found."}},
        )
    transitions = sorted(ALLOWED_TRANSITIONS.get(ev["status"], set()))
    return {
        "event": _event_public(ev),
        "attempts_open": attempts_open(ev["status"]),
        "allowed_transitions": transitions,
        "counts": events_repo.event_counts(resolved_event_id, ev.get("pack_version_id")),
        "teams": _host_team_rows(resolved_event_id),
        "attempt_status_counts": attempts_repo.attempt_status_counts(resolved_event_id),
        "announcements": announcements_repo.list_for_event(resolved_event_id, limit=10),
    }


@app.get("/api/host/events/{event_id}/teams")
async def host_list_teams(event_id: str, user: str = Depends(require_host)):
    """Teams with scores, ranks, and members for the host team table."""
    resolved_event_id = _resolve_event_or_404(event_id)
    teams = []
    for t in _host_team_rows(resolved_event_id):
        teams.append({
            **t,
            "members_list": [
                {"user_id": m.get("user_id"), "display_name": m.get("display_name")}
                for m in events_repo.list_team_members(t["team_id"])
            ],
        })
    return {"teams": teams}


@app.get("/api/host/events/{event_id}/attempts")
async def host_list_attempts(
    event_id: str,
    status: Optional[str] = None,
    limit: int = 100,
    user: str = Depends(require_host),
):
    """Validation queue / results / failed view for the host."""
    resolved_event_id = _resolve_event_or_404(event_id)
    limit = max(1, min(int(limit or 100), 500))
    return {
        "attempts": attempts_repo.list_event_attempts(resolved_event_id, status=status, limit=limit),
        "status_counts": attempts_repo.attempt_status_counts(resolved_event_id),
    }


@app.get("/api/host/events/{event_id}/attempts/{attempt_id}")
async def host_get_attempt(event_id: str, attempt_id: str, user: str = Depends(require_host)):
    """Full attempt detail incl. private validator diagnostics (host only)."""
    resolved_event_id = _resolve_event_or_404(event_id)
    attempt = attempts_repo.get_attempt(attempt_id)
    if not attempt or attempt.get("event_id") != resolved_event_id:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "NOT_FOUND", "message": "Attempt not found."}},
        )
    return {
        "attempt": attempt,
        "results": attempts_repo.list_validation_results_admin(attempt_id),
    }


@app.get("/api/host/events/{event_id}/announcements")
async def host_list_announcements(event_id: str, user: str = Depends(require_host)):
    resolved_event_id = _resolve_event_or_404(event_id)
    return {"announcements": announcements_repo.list_for_event(resolved_event_id, limit=50)}


@app.post("/api/host/events/{event_id}/announcements")
async def host_create_announcement(
    event_id: str, body: AnnouncementPayload, user: str = Depends(require_host)
):
    resolved_event_id = _resolve_event_or_404(event_id)
    if not body.title.strip() or not body.body_md.strip():
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "INVALID_ANNOUNCEMENT", "message": "Title and body are required."}},
        )
    try:
        ann = announcements_repo.create(
            event_id=resolved_event_id,
            title=body.title.strip(),
            body_md=body.body_md.strip(),
            severity=body.severity,
            created_by=user,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("create_announcement failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={"error": {"code": "ANNOUNCE_FAILED", "message": "Could not post the announcement."}},
        )
    record_audit(
        action="announcement.create",
        actor_user_id=user,
        event_id=resolved_event_id,
        target_type="announcement",
        target_id=ann["announcement_id"],
        payload={"severity": ann["severity"], "title": ann["title"]},
    )
    return ann


@app.post("/api/host/events/{event_id}/adjustments")
async def host_adjust_score(
    event_id: str, body: AdjustmentPayload, user: str = Depends(require_host)
):
    """Manually adjust a team's score with a required reason (audited + ledgered)."""
    resolved_event_id = _resolve_event_or_404(event_id)
    if not body.reason or not body.reason.strip():
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "REASON_REQUIRED", "message": "A reason is required for manual adjustments."}},
        )
    if body.points_delta == 0:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "ZERO_DELTA", "message": "Point adjustment cannot be zero."}},
        )
    team = events_repo.get_team(body.team_id)
    if not team or team.get("event_id") != resolved_event_id:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "TEAM_NOT_FOUND", "message": "That team is not part of this event."}},
        )
    try:
        result = scoring_repo.record_manual_adjustment(
            event_id=resolved_event_id,
            points_delta=int(body.points_delta),
            reason=body.reason.strip(),
            created_by=user,
            team_id=body.team_id,
            user_id=body.user_id,
            task_id=body.task_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("record_manual_adjustment failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={"error": {"code": "ADJUST_FAILED", "message": "Could not record the adjustment."}},
        )
    record_audit(
        action="score.adjust",
        actor_user_id=user,
        event_id=resolved_event_id,
        target_type="team",
        target_id=body.team_id,
        payload={"points_delta": body.points_delta, "reason": body.reason.strip(),
                 "adjustment_id": result["adjustment_id"]},
    )
    return result


# ── Event report / field reporting (PR11) ────────────────────────────────────


def _assemble_event_report(event_id: str) -> Dict[str, Any]:
    """Gather every report input from the repos and build the structured report.

    All repo calls are read-only and individually degrade to empty results, so a
    partially-available Lakebase still yields a (smaller) report rather than a 500.
    """
    ev = events_repo.get_event(event_id)
    if not ev:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "NOT_FOUND", "message": "Event not found."}},
        )
    pack_version_id = ev.get("pack_version_id")
    return report_svc.build_report(
        event=ev,
        teams=events_repo.list_teams_with_counts(event_id),
        leaderboard=leaderboard_repo.get_team_leaderboard(event_id),
        task_catalog=reporting_repo.task_catalog(pack_version_id) if pack_version_id else [],
        completion_pairs=reporting_repo.team_task_completion(event_id),
        failures=reporting_repo.failure_summary(event_id),
        hint_usage=reporting_repo.hint_usage(event_id),
        first_solves=reporting_repo.first_solves(event_id),
        status_counts=attempts_repo.attempt_status_counts(event_id),
        counts=events_repo.event_counts(event_id, pack_version_id),
        participants=reporting_repo.participant_roster(event_id),
    )


@app.get("/api/host/events/{event_id}/report")
async def host_event_report(event_id: str, user: str = Depends(require_host)):
    """Structured post-event report (JSON): summary, leaderboard, completion
    matrix, blockers, hint usage, champions, and recommended follow-ups."""
    resolved_event_id = _resolve_event_or_404(event_id)
    return _assemble_event_report(resolved_event_id)


@app.get("/api/host/events/{event_id}/export")
async def host_event_report_export(
    event_id: str, format: str = "json", user: str = Depends(require_host)
):
    """Export the event report as JSON, CSV, or Markdown for the field/account team."""
    resolved_event_id = _resolve_event_or_404(event_id)
    fmt = (format or "json").strip().lower()
    if fmt not in ("json", "csv", "markdown", "md"):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "BAD_FORMAT", "message": "format must be json, csv, or markdown."}},
        )
    report = _assemble_event_report(resolved_event_id)
    slug = report["summary"].get("slug") or resolved_event_id
    record_audit(
        action="report.export",
        actor_user_id=user,
        event_id=resolved_event_id,
        target_type="event",
        target_id=resolved_event_id,
        payload={"format": fmt},
    )
    if fmt == "json":
        return Response(
            content=report_svc.render_json(report),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{slug}-report.json"'},
        )
    if fmt == "csv":
        return Response(
            content=report_svc.render_csv(report),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{slug}-report.csv"'},
        )
    return PlainTextResponse(
        content=report_svc.render_markdown(report),
        headers={"Content-Disposition": f'attachment; filename="{slug}-report.md"'},
    )


# ── Resource bootstrap & reset (PR08) ────────────────────────────────────────


class ResourcePlanPayload(BaseModel):
    action: str = "bootstrap"  # "bootstrap" | "reset"


class ResourceResetPayload(BaseModel):
    confirm: bool = False


def _event_for_resources(event_id: str) -> Dict[str, Any]:
    ev = events_repo.get_event(event_id)
    if not ev:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "NOT_FOUND", "message": "Event not found."}},
        )
    return ev


def _pack_resources(ev: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Read the event pack version's optional ``resources`` section."""
    pvid = ev.get("pack_version_id")
    if not pvid:
        return None
    version = quest_packs_repo.get_version(pvid)
    manifest = (version or {}).get("manifest_json") or {}
    if isinstance(manifest, dict):
        return manifest.get("resources")
    return None


def _build_resource_plan(ev: Dict[str, Any], action: str) -> List[Dict[str, Any]]:
    teams = events_repo.list_teams(ev["event_id"])
    if action == "reset":
        return rsvc.build_reset_plan(ev, teams)
    return rsvc.build_bootstrap_plan(ev, teams, _pack_resources(ev))


@app.get("/api/host/events/{event_id}/resources")
async def host_list_resources(event_id: str, user: str = Depends(require_host)):
    """Resource health for an event: computed namespace, per-team targets, registry."""
    resolved_event_id = _resolve_event_or_404(event_id)
    ev = _event_for_resources(resolved_event_id)
    teams = events_repo.list_teams(resolved_event_id)
    try:
        namespace = ns.event_namespace(ev)
        targets = ns.team_targets(ev, teams)
        namespace_error = None
    except ns.NamespaceError as exc:
        namespace, targets, namespace_error = None, [], str(exc)
    return {
        "namespace": namespace,
        "namespace_error": namespace_error,
        "targets": targets,
        "resources": resources_repo.list_for_event(resolved_event_id),
        "warehouse_configured": bool(os.getenv("QUEST_SQL_WAREHOUSE_ID", "").strip()),
    }


@app.post("/api/host/events/{event_id}/resources/plan")
async def host_plan_resources(
    event_id: str, body: ResourcePlanPayload, user: str = Depends(require_host)
):
    """Dry-run: build the bootstrap/reset plan and surface any namespace blockers.

    No SQL runs. ``blockers`` lists statements that fall outside the event's
    namespace — execution will refuse a plan with any blocker.
    """
    resolved_event_id = _resolve_event_or_404(event_id)
    ev = _event_for_resources(resolved_event_id)
    action = body.action if body.action in ("bootstrap", "reset") else "bootstrap"
    try:
        plan = _build_resource_plan(ev, action)
    except ns.NamespaceError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": exc.code, "message": str(exc)}},
        )
    blockers = rsvc.plan_blockers(plan)
    record_audit(
        action="resources.plan", actor_user_id=user, event_id=resolved_event_id,
        target_type="event", target_id=resolved_event_id,
        payload={"action": action, "statements": len(plan), "blockers": len(blockers)},
    )
    return {
        "action": action,
        "plan": plan,
        "blockers": blockers,
        "warehouse_configured": bool(os.getenv("QUEST_SQL_WAREHOUSE_ID", "").strip()),
    }


def _resource_executor():
    """Build the warehouse executor, surfacing a clean 503 if unavailable."""
    if not os.getenv("QUEST_SQL_WAREHOUSE_ID", "").strip():
        raise HTTPException(
            status_code=503,
            detail={"error": {"code": "NO_WAREHOUSE",
                              "message": "Set QUEST_SQL_WAREHOUSE_ID to provision resources."}},
        )
    from services.sql_runner import build_warehouse_executor

    return build_warehouse_executor()


@app.post("/api/host/events/{event_id}/resources/bootstrap")
async def host_bootstrap_resources(
    event_id: str, user: str = Depends(require_host)
):
    """Provision all team catalogs/schemas (and pack seed data) for an event."""
    resolved_event_id = _resolve_event_or_404(event_id)
    ev = _event_for_resources(resolved_event_id)
    try:
        plan = _build_resource_plan(ev, "bootstrap")
    except ns.NamespaceError as exc:
        raise HTTPException(status_code=400, detail={"error": {"code": exc.code, "message": str(exc)}})

    executor = _resource_executor()
    result = default_resource_service.execute_plan(
        ev, plan, executor, created_by=user
    )

    # Persist each team's resolved namespace so validator-variable resolution
    # and the host resource view see a stable, materialized target (the same
    # value the plan just provisioned), rather than re-deriving it per request.
    if result.get("ok"):
        try:
            teams = events_repo.list_teams(resolved_event_id)
            for target in ns.team_targets(ev, teams):
                if target.get("team_id"):
                    events_repo.set_team_namespace(
                        target["team_id"], target["catalog"], target["schema"]
                    )
        except ns.NamespaceError as exc:
            logger.warning("namespace persist skipped (unsafe namespace): %s", exc)

    record_audit(
        action="resources.bootstrap", actor_user_id=user, event_id=resolved_event_id,
        target_type="event", target_id=resolved_event_id,
        payload={"ok": result["ok"], "statements": len(plan),
                 "blockers": len(result.get("blockers", []))},
    )
    return {"action": "bootstrap", **result}


@app.post("/api/host/events/{event_id}/resources/reset")
async def host_reset_resources(
    event_id: str, body: ResourceResetPayload, user: str = Depends(require_host)
):
    """Drop all team schemas for an event — only within the event's namespace.

    Requires ``confirm: true``. Every DROP target is re-validated against the
    namespace; a target outside it blocks the whole plan (nothing is dropped).
    """
    resolved_event_id = _resolve_event_or_404(event_id)
    ev = _event_for_resources(resolved_event_id)
    if not body.confirm:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "CONFIRM_REQUIRED",
                              "message": "Reset is destructive — resend with confirm=true."}},
        )
    try:
        plan = _build_resource_plan(ev, "reset")
    except ns.NamespaceError as exc:
        raise HTTPException(status_code=400, detail={"error": {"code": exc.code, "message": str(exc)}})

    blockers = rsvc.plan_blockers(plan)
    if blockers:
        # Refuse before touching the warehouse — out-of-namespace target present.
        record_audit(
            action="resources.reset.refused", actor_user_id=user, event_id=resolved_event_id,
            target_type="event", target_id=resolved_event_id,
            payload={"blockers": [b.get("target") for b in blockers]},
        )
        raise HTTPException(
            status_code=409,
            detail={"error": {"code": "OUTSIDE_NAMESPACE",
                              "message": "Reset refused: a target is outside the event namespace.",
                              "blockers": blockers}},
        )

    executor = _resource_executor()
    result = default_resource_service.execute_plan(ev, plan, executor, created_by=user)
    record_audit(
        action="resources.reset", actor_user_id=user, event_id=resolved_event_id,
        target_type="event", target_id=resolved_event_id,
        payload={"ok": result["ok"], "dropped": len(plan)},
    )
    return {"action": "reset", **result}


# ---------------------------------------------------------------------------
# Federation — multi-workspace GameDay (ADR_006)
# ---------------------------------------------------------------------------
# These endpoints ship in every deployment; role only changes which are
# meaningfully active. The child read endpoints work everywhere (standalone
# returns an empty/standalone shape); the host roster/health endpoints 404 on
# a child deployment via require_master_host.


class RosterImportPayload(BaseModel):
    csv: str


def _resolve_event_or_404(event_id_or_slug: str) -> str:
    """Accept either an event_id or a slug and return the canonical event_id."""
    ev = events_repo.get_event(event_id_or_slug) or events_repo.get_event_by_slug(
        event_id_or_slug
    )
    if not ev:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "NOT_FOUND", "message": "Event not found."}},
        )
    return ev["event_id"]


@app.get("/api/federation/status")
async def federation_status(request: Request, _: None = Depends(require_event_mode)):
    """Role + this workspace's own team mapping (child 'your rank' context).

    Includes a DB-connection health flag the child UI uses for its indicator.
    """
    user = get_user_email(request)
    status = fed.child_status(submitted_by=user)
    status["submitted_by"] = user
    status["event_mode"] = config.event_mode_enabled()
    try:
        status["db_connected"] = db.healthcheck()
    except Exception:  # noqa: BLE001
        status["db_connected"] = False
    return status


@app.get("/api/federation/leaderboard")
async def federation_leaderboard(
    request: Request,
    event: Optional[str] = None,
    _: None = Depends(require_event_mode),
):
    """Event-wide leaderboard from the shared view + this workspace's own team.

    Resolves the event from the query param, else the configured event slug.
    Returns ``{ leaderboard: [...], you: {...}|null, mapped: bool }`` so the
    child UI can render the overall standings and highlight its team's rank.
    """
    user = get_user_email(request)
    event_id = fed.resolve_event_id(event) if event else fed.resolve_event_id()
    if event and not event_id:
        # Caller passed an explicit id/slug — try it directly as an id.
        ev = events_repo.get_event(event)
        event_id = ev["event_id"] if ev else None
    if not event_id:
        return {"leaderboard": [], "you": None, "mapped": False, "event_id": None}

    rows = leaderboard_repo.get_team_leaderboard(event_id)
    you = None
    mapped = False
    if config.QUEST_WORKSPACE_ID:
        identity = federation_repo.resolve_identity(
            event_id, config.QUEST_WORKSPACE_ID, user
        )
        if identity and identity.get("team_id"):
            mapped = True
            team_id = identity["team_id"]
            you = next((r for r in rows if r.get("team_id") == team_id), None)
            if you is None:
                # Mapped but no points yet — surface the team with a null rank.
                you = {
                    "event_id": event_id,
                    "team_id": team_id,
                    "display_name": identity.get("team_display_name"),
                    "total_points": 0,
                    "rank": None,
                }
    return {
        "leaderboard": rows,
        "you": you,
        "mapped": mapped,
        "event_id": event_id,
        "workspace_id": config.QUEST_WORKSPACE_ID or None,
    }


@app.post("/api/host/events/{event_id}/roster/import")
async def host_import_roster(
    event_id: str,
    body: RosterImportPayload,
    user: str = Depends(require_master_host),
):
    """Import a roster CSV: pre-create teams/participants + identity map.

    CSV columns (aliases accepted): workspace_id|workspace_host, lab_user_email,
    team_name, optional display_name, real_email. Re-import is idempotent.
    """
    resolved = _resolve_event_or_404(event_id)
    try:
        result = federation_repo.import_roster(resolved, body.csv)
    except RosterImportError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": "ROSTER_INVALID", "message": str(exc)}},
        )
    from services import record_audit

    record_audit(
        action="roster.import",
        actor_user_id=user,
        event_id=resolved,
        target_type="event",
        target_id=resolved,
        payload={k: result[k] for k in ("rows", "teams_created", "participants_created", "identities_mapped")},
    )
    return result


@app.get("/api/host/events/{event_id}/workspaces")
async def host_event_workspaces(event_id: str, user: str = Depends(require_master_host)):
    """Per-workspace health: check-ins, write counts, validation pass rate."""
    resolved = _resolve_event_or_404(event_id)
    return {"workspaces": federation_repo.list_event_workspaces(resolved)}


@app.get("/api/host/events/{event_id}/identities/unmapped")
async def host_unmapped_identities(event_id: str, user: str = Depends(require_master_host)):
    """Federated scoring rows not yet attributable to a team, for reconciliation."""
    resolved = _resolve_event_or_404(event_id)
    return {"unmapped": federation_repo.list_unmapped_identities(resolved)}


# ---------------------------------------------------------------------------
# Player gameplay — attempt submission + validation (PR03)
# ---------------------------------------------------------------------------
# One submission path serves every role. Standalone/master resolve the team via
# team membership; a federation child resolves it via the identity map (and
# leaves team_id NULL, stamping workspace_id so the master attributes it later).
# Validators run through services.validation_engine; a passing task awards base
# points exactly once via services.scoring_service.


class AttemptSubmissionPayload(BaseModel):
    submission: Optional[Dict[str, Any]] = None


def _build_validator_variables(
    event_id: str, team_row: Optional[Dict[str, Any]], team_id: Optional[str]
) -> Dict[str, Any]:
    """Server-resolved template variables. Players never supply these.

    Only these names may appear in a validator's ``${...}`` slots; the safety
    layer rejects any other slot, so a player can't redirect a check at another
    team's resources.
    """
    variables: Dict[str, Any] = {"event_id": event_id}

    # Load the event once so we can resolve event-scoped variables and derive
    # team namespace fallbacks from the same source.
    ev: Optional[Dict[str, Any]] = None
    try:
        ev = events_repo.get_event(event_id)
    except Exception:  # noqa: BLE001 - event lookup is best-effort here
        ev = None

    if ev:
        if ev.get("slug"):
            variables["event_slug"] = ev["slug"]
        starts_at = _iso_or_none(ev.get("starts_at"))
        ends_at = _iso_or_none(ev.get("ends_at"))
        if starts_at:
            variables["event_start"] = starts_at
        if ends_at:
            variables["event_end"] = ends_at
        try:
            ens = ns.event_namespace(ev)
            variables["event_catalog"] = ens["catalog"]
            variables["team_prefix"] = ens["schema_prefix"]
        except ns.NamespaceError:
            pass

    if team_id:
        variables["team_id"] = team_id
    if team_row:
        if team_row.get("name"):
            # team_slug is the sanitized team name used to derive its schema and
            # to name team-scoped artefacts; SDK checks filter dashboards/jobs by
            # it (name_contains: ${team_slug}).
            variables["team_slug"] = ns.sanitize_identifier(team_row["name"])
        catalog = team_row.get("team_catalog")
        schema = team_row.get("team_schema")
        # Fallback: if the team row's namespace columns are blank (teams created
        # by self-join / bulk import before bootstrap persisted them), derive the
        # same deterministic target the bootstrap plan uses so
        # ${team_catalog}.${team_schema} always resolves. This is the core P0
        # gameplay fix — without it, sql_assertion checks reference an empty
        # namespace and silently fail right after an event opens.
        if (not catalog or not schema) and ev:
            try:
                target = ns.team_target(ev, team_row)
                catalog = catalog or target["catalog"]
                schema = schema or target["schema"]
            except ns.NamespaceError:
                # An unsafe/misconfigured namespace stays unresolved; the safety
                # layer will reject any ${...} slot rather than guess.
                pass
        if catalog:
            variables["team_catalog"] = catalog
        if schema:
            variables["team_schema"] = schema
    return variables


def _iso_or_none(value: Any) -> Optional[str]:
    """Best-effort ISO-8601 rendering of a timestamp column for template vars."""
    if value is None:
        return None
    iso = getattr(value, "isoformat", None)
    if callable(iso):
        try:
            return iso()
        except Exception:  # noqa: BLE001
            return str(value)
    return str(value)


def _resolve_attempt_identity(event_id: str, user: str) -> Dict[str, Any]:
    """Resolve (team_id, team_row, workspace_id) for the submitting user."""
    if config.is_child():
        workspace_id = config.QUEST_WORKSPACE_ID or None
        team_id = None
        team_row = None
        if workspace_id:
            identity = federation_repo.resolve_identity(event_id, workspace_id, user)
            team_id = identity.get("team_id") if identity else None
            if team_id:
                team_row = events_repo.get_team(team_id)
        return {"team_id": team_id, "team_row": team_row, "workspace_id": workspace_id}

    team_row = events_repo.get_team_for_user(event_id, user)
    return {
        "team_id": team_row["team_id"] if team_row else None,
        "team_row": team_row,
        "workspace_id": None,
    }


def _closed_event_message(status: str) -> str:
    """Player-safe reason a submission is blocked for a non-active event."""
    return {
        "paused": "This event is paused. Your host will resume it shortly.",
        "frozen": "Scoring is frozen for the finale — submissions are closed.",
        "completed": "This event has ended. Thanks for playing!",
        "archived": "This event has been archived.",
        "draft": "This event hasn't opened yet.",
        "ready": "This event hasn't started yet. Hang tight!",
    }.get(status, "Submissions are closed right now.")


def _attempt_message(status: str, points: int, already_awarded: bool) -> str:
    """Player-safe summary message for an attempt's terminal status."""
    if status == "passed":
        if already_awarded:
            return "Already completed — your team keeps its points."
        if points > 0:
            return f"Task complete! +{points} points."
        return "Task complete!"
    if status == "failed":
        return "Not passed yet. Review the success criteria and try again."
    if status == "manual":
        return "Submitted for host review. A facilitator will confirm this task."
    if status == "error":
        return "We couldn't verify this task right now. Please try again shortly."
    return "Submitted."


@app.post("/api/events/{event_id}/tasks/{task_id}/attempts")
async def submit_attempt(
    event_id: str,
    task_id: str,
    body: AttemptSubmissionPayload,
    request: Request,
    _: None = Depends(require_event_mode),
):
    """Submit an attempt for a task: validate, persist, and score on pass.

    Accepts an ``event_id`` or slug. Returns a player-safe result; raw validator
    diagnostics are persisted to ``validation_results.private_message`` for the
    host, never returned to the player.
    """
    user = get_user_email(request)
    resolved_event_id = _resolve_event_or_404(event_id)

    # Event lifecycle gate: only an active event accepts new attempts. Paused,
    # frozen, completed, draft/ready, and archived all block with a safe message.
    ev = events_repo.get_event(resolved_event_id)
    if ev and not attempts_open(ev["status"]):
        raise HTTPException(
            status_code=409,
            detail={
                "error": {
                    "code": "EVENT_NOT_ACTIVE",
                    "message": _closed_event_message(ev["status"]),
                }
            },
        )

    task = quest_packs_repo.get_task(task_id)
    if not task:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "NOT_FOUND", "message": "Task not found."}},
        )

    identity = _resolve_attempt_identity(resolved_event_id, user)
    team_id = identity["team_id"]
    workspace_id = identity["workspace_id"]
    variables = _build_validator_variables(resolved_event_id, identity["team_row"], team_id)

    try:
        attempt_id = attempts_repo.create_attempt(
            event_id=resolved_event_id,
            task_id=task_id,
            submitted_by=user,
            submission=body.submission,
            team_id=team_id,
            workspace_id=workspace_id,
            status="running",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("create_attempt failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={"error": {"code": "ATTEMPT_FAILED", "message": "Could not record your attempt. Try again shortly."}},
        )

    submission = body.submission or {}
    validators = quest_packs_repo.list_validators(task_id)

    request_id = getattr(request.state, "request_id", None)
    outcomes = []
    public_results = []
    results_persisted = 0
    results_failed_to_persist = 0
    for v in validators:
        outcome = default_engine.run_validator(v, submission, variables)
        outcomes.append(outcome)
        obs.log_validation(
            request_id=request_id,
            event_id=resolved_event_id,
            task_id=task_id,
            team_id=team_id,
            validator_id=v.get("validator_id"),
            validator_type=v.get("type"),
            status=outcome.status,
            score_delta=outcome.score_delta,
        )
        try:
            attempts_repo.record_validation_result(
                attempt_id=attempt_id,
                validator_id=v["validator_id"],
                status=outcome.status,
                score_delta=outcome.score_delta,
                public_message=outcome.public_message,
                private_message=outcome.private_message,
                evidence=outcome.evidence,
                workspace_id=workspace_id,
            )
            results_persisted += 1
        except Exception as exc:  # noqa: BLE001 - persistence best-effort per result
            results_failed_to_persist += 1
            # Distinct, loud log: a passing task whose evidence didn't persist is
            # an integrity problem the host must be able to find.
            logger.error(
                "VALIDATION_RESULT_PERSIST_FAILED attempt=%s validator=%s: %s",
                attempt_id, v.get("validator_id"), exc,
            )
        public_results.append({"status": outcome.status, "message": outcome.public_message})

    status = aggregate_status(outcomes) if validators else "error"

    # Never let scoring proceed when the result ledger is empty: if we ran
    # validators but persisted ZERO result rows, the evidence trail is missing,
    # so force an error rather than awarding points on unrecorded results.
    persist_integrity_error = bool(validators) and results_persisted == 0
    if persist_integrity_error:
        status = "error"

    points_awarded = 0
    already_awarded = False
    if status == "passed":
        task_points = int(task.get("points") or 0)
        award = default_scoring_service.award_task_base_points(
            event_id=resolved_event_id,
            task_id=task_id,
            points=task_points,
            attempt_id=attempt_id,
            quest_id=task.get("quest_id"),
            team_id=team_id,
            user_id=user,
            workspace_id=workspace_id,
            created_by=user,
        )
        points_awarded = award["points"]
        already_awarded = bool(
            (not award["awarded"]) and task_points > 0 and (team_id or workspace_id)
        )
        obs.log_scoring(
            request_id=request_id,
            event_id=resolved_event_id,
            team_id=team_id,
            workspace_id=workspace_id,
            source_type="validation",
            points_delta=points_awarded,
            awarded=bool(award["awarded"]),
            reason="task_passed",
        )

    if persist_integrity_error:
        error_sentinel = "result_persist_failed"
    elif status == "error":
        error_sentinel = "validation_error"
    else:
        error_sentinel = None
    try:
        attempts_repo.set_status(attempt_id, status, error_sentinel)
    except Exception as exc:  # noqa: BLE001
        logger.warning("set_status failed: %s", exc)

    record_audit(
        action="attempt.submit",
        actor_user_id=user,
        event_id=resolved_event_id,
        target_type="task",
        target_id=task_id,
        payload={
            "attempt_id": attempt_id,
            "status": status,
            "points_awarded": points_awarded,
            "validators": len(validators),
            "results_persisted": results_persisted,
            "results_failed_to_persist": results_failed_to_persist,
            "workspace_id": workspace_id,
        },
    )

    return {
        "attempt_id": attempt_id,
        "status": status,
        "message": _attempt_message(status, points_awarded, already_awarded),
        "points_awarded": points_awarded,
        "already_awarded": already_awarded,
        "results": public_results,
        "team_id": team_id,
    }


@app.post("/api/events/{event_id}/tasks/{task_id}/hints/{hint_id}/reveal")
async def reveal_hint(
    event_id: str,
    task_id: str,
    hint_id: str,
    request: Request,
    _: None = Depends(require_event_mode),
):
    """Reveal a hint and charge its penalty once per team.

    Returns the hint body plus ``penalty_applied`` (the negative delta written
    this call, 0 if already revealed or scoring is closed) and ``newly_applied``.
    Revealing again is free — the per-team idempotency key guarantees a single
    charge. While the event is paused/frozen the hint text is still returned but
    no penalty is incurred (no new scoring when play is closed).
    """
    user = get_user_email(request)
    resolved_event_id = _resolve_event_or_404(event_id)
    ev = events_repo.get_event(resolved_event_id)

    hint = quest_packs_repo.get_hint(hint_id)
    if not hint or hint.get("task_id") != task_id:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "NOT_FOUND", "message": "Hint not found for this task."}},
        )

    identity = _resolve_attempt_identity(resolved_event_id, user)
    team_id = identity.get("team_id")
    workspace_id = identity.get("workspace_id")

    penalty_applied = 0
    newly_applied = False
    scoring_open = bool(ev and attempts_open(ev["status"]) and not _scoring_frozen(ev))
    if scoring_open:
        outcome = default_scoring_service.apply_hint_penalty(
            event_id=resolved_event_id,
            hint_id=hint_id,
            penalty_points=int(hint.get("penalty_points") or 0),
            task_id=task_id,
            quest_id=hint.get("quest_id"),
            team_id=team_id,
            user_id=user,
            workspace_id=workspace_id,
            created_by=user,
        )
        penalty_applied = outcome["points_delta"]
        newly_applied = outcome["applied"]
        if newly_applied:
            record_audit(
                action="hint.reveal",
                actor_user_id=user,
                event_id=resolved_event_id,
                target_type="hint",
                target_id=hint_id,
                payload={"task_id": task_id, "team_id": team_id,
                         "points_delta": penalty_applied, "workspace_id": workspace_id},
            )

    return {
        "hint": {
            "hint_id": hint_id,
            "title": hint.get("title"),
            "body_md": hint.get("body_md"),
            "penalty_points": hint.get("penalty_points"),
        },
        "revealed": True,
        "penalty_applied": penalty_applied,
        "newly_applied": newly_applied,
        "team_score": leaderboard_repo.get_team_score(resolved_event_id, team_id) if team_id else None,
    }


@app.get("/api/events/{event_id}/attempts/{attempt_id}")
async def get_attempt_status(
    event_id: str,
    attempt_id: str,
    request: Request,
    _: None = Depends(require_event_mode),
):
    """Return an attempt's status + player-safe per-validator results."""
    resolved_event_id = _resolve_event_or_404(event_id)
    attempt = attempts_repo.get_attempt(attempt_id)
    if not attempt or attempt.get("event_id") != resolved_event_id:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "NOT_FOUND", "message": "Attempt not found."}},
        )
    return {
        "attempt": {
            "attempt_id": attempt.get("attempt_id"),
            "task_id": attempt.get("task_id"),
            "team_id": attempt.get("team_id"),
            "status": attempt.get("status"),
            "submitted_at": attempt.get("submitted_at"),
            "completed_at": attempt.get("completed_at"),
        },
        "results": attempts_repo.list_validation_results(attempt_id),
    }


# ---------------------------------------------------------------------------
# Static files & SPA fallback
# ---------------------------------------------------------------------------

static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(static_dir):
    app.mount("/assets", StaticFiles(directory=os.path.join(static_dir, "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        file_path = os.path.join(static_dir, full_path)
        if os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(static_dir, "index.html"))
