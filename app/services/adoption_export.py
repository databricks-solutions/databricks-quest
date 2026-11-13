"""Complete, admin-only Adoption Mode reports. No Event Mode or secret tables."""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from tempfile import SpooledTemporaryFile
from typing import Any, BinaryIO, Callable, Iterable, Iterator
from zipfile import ZIP_DEFLATED, ZipFile

Row = dict[str, Any]
ReadRows = Callable[..., Iterable[Row]]
WAREHOUSE_PAGE_SIZE = 10000


@dataclass(frozen=True)
class Dataset:
    filename: str
    table: str
    columns: tuple[str, ...]


# Explicit columns form the export contract and exclude operational/secret data.
DATASETS = (
    Dataset("user_profiles.csv", "user_profile_snapshot", (
        "user_id", "display_name", "total_points", "level", "current_streak", "max_streak",
        "badge_count", "missions_completed", "first_activity_date", "last_activity_date",
        "distinct_products_used", "updated_at",
    )),
    Dataset("mission_completions.csv", "mission_completions", (
        "user_id", "mission_id", "mission_name", "points_awarded", "completed_at",
        "period_start", "period_end", "scored_at",
    )),
    Dataset("points_history.csv", "user_points_fact", (
        "user_id", "event_type", "mission_id", "points", "reason", "event_timestamp", "scored_at",
    )),
    Dataset("leaderboard.csv", "leaderboard", (
        "user_id", "display_name", "total_points", "weekly_points", "monthly_points", "level",
        "all_time_rank", "weekly_rank", "monthly_rank", "updated_at",
    )),
    Dataset("badges.csv", "badges", ("user_id", "badge_id", "badge_name", "badge_icon", "earned_at")),
    Dataset("notifications.csv", "notifications", (
        "user_id", "notification_type", "title", "message", "mission_id", "points", "created_at",
    )),
)
TRAINING = {
    "warehouse": Dataset("training_completions.csv", "training_completions", (
        "user_id", "course_id", "course_name", "course_type", "completed_at",
    )),
    "lakebase": Dataset("training_attestations.csv", "training_attestations", (
        "user_id", "course_mission_id", "course_id", "attested_at",
    )),
}

NOTES = [
    "All retained Adoption Mode rows from the active data backend, with no user, period, or top-N filter.",
    "Data follows the scoring scope and retention of this deployment, not every workspace user ever.",
    "Tables are read sequentially, not as one transactional snapshot. Scoring or training updates during export can affect consistency.",
    "user_profiles.csv and leaderboard.csv contain stored point totals; do not add them to points_history.csv.",
    "mission_completions.csv preserves each award and repeat period. points_history.csv also contains mission awards plus consumption points; do not sum both files together.",
    "Warehouse exports include the Delta training feed; Lakebase exports include local self-attestations. These are different sources, not interchangeable training histories.",
    "Mission and badge catalogs describe the current app version; historical awards may reference retired definitions.",
    "CSV files use UTF-8 with BOM. Empty cells represent null or empty values; structured catalog fields contain JSON.",
    "Text cells that could execute spreadsheet formulas have a leading apostrophe. Numeric values retain their types.",
    "Raw system-table telemetry, credentials, administrator settings, and dormant Event Mode records are outside this report.",
]


@dataclass
class ExportArchive:
    file: BinaryIO
    filename: str
    size: int
    row_counts: dict[str, int]

    def chunks(self) -> Iterator[bytes]:
        try:
            while block := self.file.read(64 * 1024):
                yield block
        finally:
            self.file.close()


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, str):
        stripped = value.lstrip(" \t\r\n\v\f\ufeff")
        if stripped.startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")):
            return "'" + value
    if isinstance(value, Decimal):
        return str(value)
    return value


def _write_csv(archive: ZipFile, name: str, columns: tuple[str, ...], rows: Iterable[Row]) -> dict:
    count = 0
    with archive.open(name, "w", force_zip64=True) as raw:
        with io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(columns)
            for row in rows:
                writer.writerow([_csv_value(row.get(column)) for column in columns])
                count += 1
    return {"row_count": count, "columns": list(columns)}


def _dataset_rows(dataset: Dataset, read_rows: ReadRows, backend: str) -> Iterator[Row]:
    columns = ", ".join(dataset.columns)
    expected_columns = set(dataset.columns)

    def checked(rows: Iterable[Row]) -> Iterator[Row]:
        for row in rows:
            if set(row) != expected_columns:
                raise RuntimeError(f"Export dataset columns mismatch: {dataset.table}")
            yield row

    # Sort on all exported columns so page boundaries also handle repeat awards.
    sql = f"SELECT {columns} FROM {dataset.table} ORDER BY {columns}"
    if backend == "lakebase":
        yield from checked(read_rows(sql, backend=backend))
        return
    # Bound each Statements API response, not the total report size. All chunks
    # within each page are consumed by warehouse_backend.iter_query.
    offset = 0
    while True:
        count = 0
        for row in checked(read_rows(sql + " LIMIT %s OFFSET %s", (WAREHOUSE_PAGE_SIZE, offset), backend=backend)):
            count += 1
            yield row
        if count < WAREHOUSE_PAGE_SIZE:
            return
        offset += count


def build_export(
    read_rows: ReadRows,
    *,
    backend: str,
    missions: list[Row],
    badges: list[Row],
    scoring_workspace_ids: list[str],
) -> ExportArchive:
    """Finish the archive before serving it: a failed query never becomes a partial download."""
    if backend not in TRAINING:
        raise ValueError("Unknown data backend")
    started = datetime.now(timezone.utc)
    file = SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
    files: dict[str, dict] = {}
    try:
        with ZipFile(file, "w", compression=ZIP_DEFLATED, allowZip64=True) as archive:
            for dataset in (*DATASETS, TRAINING[backend]):
                files[dataset.filename] = _write_csv(
                    archive, dataset.filename, dataset.columns, _dataset_rows(dataset, read_rows, backend),
                )
                files[dataset.filename]["source"] = dataset.table
            for name, catalog in (("mission_catalog.csv", missions), ("badge_catalog.csv", badges)):
                columns = tuple(dict.fromkeys(key for row in catalog for key in row)) or ("id", "name")
                files[name] = _write_csv(archive, name, columns, catalog)
                files[name]["source"] = "app_catalog"
            metadata = {
                "format_version": 1,
                "started_at": started.isoformat(),
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "data_backend": backend,
                "scoring_workspace_ids": scoring_workspace_ids,
                "files": files,
                "notes": NOTES,
            }
            archive.writestr("metadata.json", json.dumps(metadata, indent=2, ensure_ascii=False))
            archive.writestr("README.txt", "Databricks Quest: full adoption data export\n\n" + "\n\n".join(NOTES))
        size = file.tell()
        file.seek(0)
        return ExportArchive(
            file=file,
            filename=f"quest-adoption-export-{started.strftime('%Y%m%dT%H%M%S%fZ')}.zip",
            size=size,
            row_counts={name: info["row_count"] for name, info in files.items()},
        )
    except BaseException:
        file.close()
        raise
