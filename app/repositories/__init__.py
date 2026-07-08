"""Data-access repositories for GameDay Event Mode.

Each repository owns reads/writes for one slice of the domain and is the only
place that should contain SQL for that slice. Read paths degrade gracefully
(return empty/None) when Lakebase is unavailable, mirroring adoption mode.

These are foundational stubs introduced in PR01. Mutation paths that require
auditing, transactions, or validation are intentionally deferred to later PRs
and raise ``NotImplementedError`` until then.
"""

from .events import EventsRepository, EventStateError
from .quest_packs import QuestPacksRepository
from .attempts import AttemptsRepository
from .scoring import ScoringRepository
from .leaderboard import LeaderboardRepository
from .federation import FederationRepository, RosterImportError
from .admins import AdminsRepository
from .announcements import AnnouncementsRepository
from .resources import ResourcesRepository
from .reporting import ReportingRepository

__all__ = [
    "EventsRepository",
    "EventStateError",
    "QuestPacksRepository",
    "AttemptsRepository",
    "ScoringRepository",
    "LeaderboardRepository",
    "FederationRepository",
    "RosterImportError",
    "AdminsRepository",
    "AnnouncementsRepository",
    "ResourcesRepository",
    "ReportingRepository",
]
