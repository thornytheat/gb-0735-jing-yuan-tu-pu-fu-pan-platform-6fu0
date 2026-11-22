from fastapi import APIRouter, Request

from app.dependencies import Archive
from app.modules.intake.catalog import CATALOG
from app.modules.runs.service import intake_state

router = APIRouter(prefix="/api")

INTAKE_CODES = {"BLANK_ROW", "REPEATED_HEADER", "COLUMN_COUNT", "UNKNOWN_HEADER", "UNKNOWN_SEGMENT"}


@router.get("/samples")
def samples():
    return [{"id": key, "label": label} for key, label in CATALOG.items()]


@router.get("/runs/{run_id}/intake")
def intake(archive: Archive, request: Request):
    _, snapshot, _ = archive
    return {"files": snapshot["files"],
            "intake": intake_state(snapshot),
            "adjudication": request.app.state.runs.store.get_adjudication(archive[0]["run_id"]),
            "issues": [row for row in snapshot["rows"] if row["code"] in INTAKE_CODES]}
