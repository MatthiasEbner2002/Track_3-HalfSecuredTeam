"""Secure processing environment (SPE), Art. 73: one SQLite file per data permit.

create() copies the cohort's rows of the record types the request names out of
the main database into SPE_DIR/<request_id>.db. Patient IDs are kept; opted-out
patients are already excluded by the cohort query. The file itself is never
served. Everything else goes through the functions below, which main.py exposes
under /api/spe/...:

- query():    read-only SQL, at most SPE_PAGE_ROWS rows on screen per call
- download(): always refused, Art. 73(1)(e)
- export():   the only way out; a table has to pass the small-cell check and
              then waits in the outputs queue until staff approve it
- Every access is written to the SPE's audit_log, which staff can read.
"""

import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app import config
from app.config import SMALL_CELL_THRESHOLD, SPE_PAGE_ROWS, SPE_QUERY_STEPS
from app.db import TABLES
from app.models import DataRequest

PATIENT_ID = re.compile(r"^p-[0-9a-f]{8}$")
ID_COLUMNS = {"patient_id", "encounter_id"}
DATE_COLUMN = re.compile(r"(^|_)date$")
PRIVATE_TABLES = {"manifest", "audit_log", "outputs"}  # bookkeeping, not data; hidden from applicant SQL
DOWNLOAD_REFUSED = (
    "Downloads from a secure processing environment are not permitted "
    "(Art. 73(1)(e) Regulation (EU) 2025/327). Data is only viewed inside the SPE; "
    "the only way out is an output that passes the small-cell check and staff review. "
    "This attempt was logged."
)


class SpeError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def path(spe_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", spe_id):
        raise SpeError(404, f"no secure processing environment {spe_id!r}")
    return config.SPE_DIR / f"{spe_id}.db"


def _open(spe_id: str, readonly: bool = False) -> sqlite3.Connection:
    p = path(spe_id)
    if not p.is_file():
        raise SpeError(404, f"no secure processing environment {spe_id!r}")
    conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True) if readonly else sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def _db(spe_id: str, readonly: bool = False) -> Iterator[sqlite3.Connection]:
    """A connection that commits on success and is closed afterwards."""
    conn = _open(spe_id, readonly)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _audit(spe_id: str, actor: str, action: str, detail: Any = None) -> None:
    with _db(spe_id) as conn:
        conn.execute("INSERT INTO audit_log (at, actor, action, detail) VALUES (?, ?, ?, ?)",
                     (_now(), actor, action, json.dumps(detail, ensure_ascii=False) if detail is not None else None))


# --- creation --------------------------------------------------------------

def create(request: DataRequest, conn: sqlite3.Connection, patient_ids: list[str], record_types: list[str],
           articles: list[str], reason: str) -> dict[str, Any]:
    """Build (or rebuild) the SPE for a permit from the main database behind `conn`. Returns its manifest."""
    src_path = conn.execute("PRAGMA database_list").fetchone()[2]
    tables = [t for t in TABLES if t in record_types]
    target = path(request.id)
    config.SPE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".db.tmp")
    tmp.unlink(missing_ok=True)
    spe = sqlite3.connect(tmp)
    try:
        spe.execute("ATTACH DATABASE ? AS src", (str(src_path),))
        spe.execute("CREATE TABLE cohort (patient_id TEXT PRIMARY KEY)")
        spe.executemany("INSERT INTO cohort VALUES (?)", [(p,) for p in patient_ids])
        for t in tables:
            spe.execute(f"CREATE TABLE {t} AS SELECT t.* FROM src.{t} t JOIN cohort USING (patient_id)")
            spe.execute(f"CREATE INDEX idx_{t}_patient ON {t} (patient_id)")
        spe.execute("CREATE TABLE audit_log (id INTEGER PRIMARY KEY, at TEXT, actor TEXT, action TEXT, detail TEXT)")
        spe.execute("CREATE TABLE outputs (id INTEGER PRIMARY KEY, at TEXT, actor TEXT, name TEXT, kind TEXT, "
                    "content TEXT, status TEXT, checks TEXT, reviewed_at TEXT, note TEXT)")
        manifest = {
            "id": request.id,
            "applicant": request.applicant.model_dump(),
            "purpose": request.purpose,
            "question": request.question,
            "articles": articles,
            "reason": reason,
            "cohort_size": len(patient_ids),
            "record_types": tables,
            "created": _now(),
        }
        spe.execute("CREATE TABLE manifest (json TEXT)")
        spe.execute("INSERT INTO manifest VALUES (?)", (json.dumps(manifest, ensure_ascii=False),))
        spe.commit()
        spe.execute("DETACH DATABASE src")
    finally:
        spe.close()
    tmp.replace(target)
    _audit(request.id, "system", "created", {"cohort_size": len(patient_ids), "tables": tables})
    return info(request.id)


def exists(spe_id: str) -> bool:
    try:
        return path(spe_id).is_file()
    except SpeError:
        return False


def info(spe_id: str) -> dict[str, Any]:
    """Manifest plus row counts; what both the applicant and staff see first."""
    with _db(spe_id, readonly=True) as conn:
        manifest = json.loads(conn.execute("SELECT json FROM manifest").fetchone()[0])
        manifest["tables"] = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in manifest["record_types"]}
        manifest["pending_outputs"] = conn.execute("SELECT COUNT(*) FROM outputs WHERE status = 'pending'").fetchone()[0]
        manifest["audit_entries"] = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    manifest["page_rows"] = SPE_PAGE_ROWS
    return manifest


# --- access ----------------------------------------------------------------

def _authorizer(action: int, arg1: str | None, arg2: str | None, db_name: str | None, trigger: str | None) -> int:
    if action == sqlite3.SQLITE_READ:
        return sqlite3.SQLITE_DENY if arg1 in PRIVATE_TABLES else sqlite3.SQLITE_OK
    if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE):
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY  # no writes, no ATTACH, no PRAGMA


def query(spe_id: str, sql: str, actor: str = "applicant") -> dict[str, Any]:
    """Run one read-only statement inside the SPE and show at most SPE_PAGE_ROWS rows."""
    sql = sql.strip().rstrip(";").strip()
    if not sql:
        raise SpeError(400, "empty query")
    conn = _open(spe_id, readonly=True)
    conn.set_authorizer(_authorizer)
    budget = [SPE_QUERY_STEPS // 1000]
    conn.set_progress_handler(lambda: budget.__setitem__(0, budget[0] - 1) or budget[0] < 0, 1000)
    cur = conn.cursor()
    error = None
    try:
        cur.execute(sql)
        rows = cur.fetchmany(SPE_PAGE_ROWS + 1)
        columns = [d[0] for d in cur.description] if cur.description else []
    except (sqlite3.DatabaseError, sqlite3.Warning) as e:
        error = e
    finally:
        cur.close()  # an unexhausted cursor keeps a read lock even after conn.close()
        conn.close()
    if error is not None:
        _audit(spe_id, actor, "query_refused", {"sql": sql, "error": str(error)})
        raise SpeError(400, f"query refused: {error}") from error
    truncated = len(rows) > SPE_PAGE_ROWS
    rows = [dict(r) for r in rows[:SPE_PAGE_ROWS]]
    _audit(spe_id, actor, "query", {"sql": sql, "rows_shown": len(rows), "truncated": truncated})
    return {
        "columns": columns,
        "rows": rows,
        "truncated": truncated,
        "page_rows": SPE_PAGE_ROWS,
        "notice": "Shown inside the SPE only. Aggregates can be exported as an output; records cannot.",
    }


def download(spe_id: str, actor: str = "applicant", what: str = "") -> None:
    """There is no download. Logs the attempt and raises 403."""
    _open(spe_id, readonly=True).close()  # 404 if the SPE does not exist
    _audit(spe_id, actor, "download_blocked", {"what": what or "records"})
    raise SpeError(403, DOWNLOAD_REFUSED)


# --- outputs (the airlock) -------------------------------------------------

def check_table(rows: list[dict[str, Any]]) -> list[str]:
    """Why a table may not leave the SPE: record-level columns, patient IDs, counts below the threshold."""
    problems = []
    columns = list(dict.fromkeys(k for r in rows for k in r))
    if record_cols := [c for c in columns if c in ID_COLUMNS or DATE_COLUMN.search(c)]:
        problems.append(f"record-level columns: {', '.join(record_cols)}")
    if any(isinstance(v, str) and PATIENT_ID.match(v) for r in rows for v in r.values()):
        problems.append("contains patient IDs")
    small = [f"{c}={v}" for r in rows for c, v in r.items()
             if isinstance(v, int) and not isinstance(v, bool) and 0 <= v < SMALL_CELL_THRESHOLD]
    if small:
        problems.append(f"{len(small)} count(s) below {SMALL_CELL_THRESHOLD}: {', '.join(small[:5])}")
    return problems


def check_text(content: str) -> list[str]:
    ids = set(re.findall(r"\bp-[0-9a-f]{8}\b", content))
    return [f"contains {len(ids)} patient ID(s)"] if ids else []


def export(spe_id: str, name: str, rows: list[dict[str, Any]] | None = None, content: str | None = None,
           actor: str = "applicant") -> dict[str, Any]:
    """Ask for an output to leave the SPE. Tables are small-cell checked; anything else waits for staff."""
    if rows is None and content is None:
        raise SpeError(400, "an output needs rows (a table) or content (text)")
    kind = "table" if rows is not None else "text"
    problems = check_table(rows) if rows is not None else check_text(content or "")
    status = "refused" if problems else "pending"
    payload = json.dumps(rows, ensure_ascii=False) if rows is not None else content
    with _db(spe_id) as conn:
        cur = conn.execute(
            "INSERT INTO outputs (at, actor, name, kind, content, status, checks) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_now(), actor, name, kind, payload, status, json.dumps(problems)))
        output_id = cur.lastrowid
    _audit(spe_id, actor, "output_refused" if problems else "output_submitted",
           {"output_id": output_id, "name": name, "checks": problems})
    return get_output(spe_id, output_id, role="staff")


def _row_to_output(row: sqlite3.Row, with_content: bool) -> dict[str, Any]:
    out = dict(row)
    out["checks"] = json.loads(out["checks"] or "[]")
    content = out.pop("content")
    if with_content:
        out["content"] = json.loads(content) if out["kind"] == "table" and content else content
    return out


def list_outputs(spe_id: str) -> list[dict[str, Any]]:
    with _db(spe_id, readonly=True) as conn:
        return [_row_to_output(r, with_content=False) for r in conn.execute("SELECT * FROM outputs ORDER BY id DESC")]


def get_output(spe_id: str, output_id: int, role: str = "applicant") -> dict[str, Any]:
    """Staff see every output. The applicant only receives the content once it is approved."""
    with _db(spe_id, readonly=True) as conn:
        row = conn.execute("SELECT * FROM outputs WHERE id = ?", (output_id,)).fetchone()
    if row is None:
        raise SpeError(404, f"no output {output_id}")
    return _row_to_output(row, with_content=role == "staff" or row["status"] == "approved")


def review(spe_id: str, output_id: int, status: str, note: str = "", actor: str = "staff") -> dict[str, Any]:
    if status not in ("approved", "rejected"):
        raise SpeError(400, "status must be approved or rejected")
    with _db(spe_id) as conn:
        current = conn.execute("SELECT status FROM outputs WHERE id = ?", (output_id,)).fetchone()
        if current is None:
            raise SpeError(404, f"no output {output_id}")
        if current[0] != "pending":
            raise SpeError(409, f"output {output_id} is {current[0]}, only pending outputs can be reviewed")
        conn.execute("UPDATE outputs SET status = ?, reviewed_at = ?, note = ? WHERE id = ?",
                     (status, _now(), note, output_id))
    _audit(spe_id, actor, f"output_{status}", {"output_id": output_id, "note": note})
    return get_output(spe_id, output_id, role="staff")


def audit_log(spe_id: str) -> list[dict[str, Any]]:
    with _db(spe_id, readonly=True) as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM audit_log ORDER BY id DESC")]
    for r in rows:
        r["detail"] = json.loads(r["detail"]) if r["detail"] else None
    return rows
