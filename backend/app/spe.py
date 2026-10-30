"""Secure processing environment (SPE), Art. 73: one SQLite file per data permit.

create() copies the cohort's rows of the record types the request names out of
the main database into SPE_DIR/<request_id>.db and mints the permit's access
key. Patient IDs are kept; opted-out patients are already excluded by the cohort
query. The file itself is never served. Everything else goes through the
functions below, which spe_api.py exposes behind `Authorization: Bearer <key>`:

- query():    read-only SQL; the result is rendered to a PNG, at most
              SPE_PAGE_ROWS rows, so no record ever reaches the browser as text
- download(): always refused, Art. 73(1)(e)
- Every access is written to the SPE's audit_log (for the access body; no endpoint).
"""

import base64
import hashlib
import hmac
import io
import json
import re
import secrets
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from app import config
from app.config import SPE_PAGE_ROWS, SPE_QUERY_STEPS
from app.db import TABLES
from app.models import DataRequest

PATIENT_ID = re.compile(r"^p-[0-9a-f]{8}$")
PRIVATE_TABLES = {"manifest", "audit_log"}  # bookkeeping, not data; hidden from applicant SQL
DOWNLOAD_REFUSED = (
    "Downloads from a secure processing environment are not permitted "
    "(Art. 73(1)(e) Regulation (EU) 2025/327). Data is only viewed inside the SPE. "
    "This attempt was logged."
)
FONT_PATHS = ["/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
              "/System/Library/Fonts/Menlo.ttc", "C:/Windows/Fonts/consola.ttf"]


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


def _audit(spe_id: str, action: str, detail: Any = None) -> None:
    with _db(spe_id) as conn:
        conn.execute("INSERT INTO audit_log (at, action, detail) VALUES (?, ?, ?)",
                     (_now(), action, json.dumps(detail, ensure_ascii=False) if detail is not None else None))


def _manifest(conn: sqlite3.Connection) -> dict[str, Any]:
    return json.loads(conn.execute("SELECT json FROM manifest").fetchone()[0])


# --- creation and access key ----------------------------------------------

def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def create(request: DataRequest, conn: sqlite3.Connection, patient_ids: list[str], record_types: list[str],
           articles: list[str], reason: str) -> tuple[dict[str, Any], str]:
    """Build (or rebuild) the SPE for a permit from the main database behind `conn`.

    Returns (manifest, access key). The key is shown once; only its hash is stored.
    """
    src_path = conn.execute("PRAGMA database_list").fetchone()[2]
    tables = [t for t in TABLES if t in record_types]
    target = path(request.id)
    config.SPE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".db.tmp")
    tmp.unlink(missing_ok=True)
    key = f"{request.id}.{secrets.token_urlsafe(24)}"
    spe = sqlite3.connect(tmp)
    try:
        spe.execute("ATTACH DATABASE ? AS src", (str(src_path),))
        spe.execute("CREATE TABLE cohort (patient_id TEXT PRIMARY KEY)")
        spe.executemany("INSERT INTO cohort VALUES (?)", [(p,) for p in patient_ids])
        for t in tables:
            spe.execute(f"CREATE TABLE {t} AS SELECT t.* FROM src.{t} t JOIN cohort USING (patient_id)")
            spe.execute(f"CREATE INDEX idx_{t}_patient ON {t} (patient_id)")
        spe.execute("CREATE TABLE audit_log (id INTEGER PRIMARY KEY, at TEXT, action TEXT, detail TEXT)")
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
            "key_hash": _hash(key),
        }
        spe.execute("CREATE TABLE manifest (json TEXT)")
        spe.execute("INSERT INTO manifest VALUES (?)", (json.dumps(manifest, ensure_ascii=False),))
        spe.commit()
        spe.execute("DETACH DATABASE src")
    finally:
        spe.close()
    tmp.replace(target)
    _audit(request.id, "created", {"cohort_size": len(patient_ids), "tables": tables})
    return info(request.id), key


def _norm_name(name: str) -> bytes:
    return " ".join(name.casefold().split()).encode()


def authenticate(key: str | None, applicant: str | None) -> str:
    """The SPE id that key and applicant name open together, or 401.

    Keys look like '<request_id>.<secret>'. The name has to be the applicant's on the permit,
    so a leaked key alone is not enough and the person at the keyboard is named in the log.
    """
    spe_id, _, secret = (key or "").strip().partition(".")
    if not secret or not (applicant or "").strip():
        raise SpeError(401, "access needs the key returned with the APPROVE_SPE decision and the applicant's name")
    try:
        with _db(spe_id, readonly=True) as conn:
            manifest = _manifest(conn)
    except SpeError:
        raise SpeError(401, "key and name do not open any secure processing environment") from None
    key_ok = hmac.compare_digest(manifest["key_hash"], _hash(key.strip()))
    name_ok = hmac.compare_digest(_norm_name(manifest["applicant"]["name"]), _norm_name(applicant))
    if not (key_ok and name_ok):
        _audit(spe_id, "access_rejected", {"key_ok": key_ok, "name_ok": name_ok, "name_given": applicant.strip()[:80]})
        raise SpeError(401, "key and name do not open any secure processing environment")
    return spe_id


def exists(spe_id: str) -> bool:
    try:
        return path(spe_id).is_file()
    except SpeError:
        return False


def info(spe_id: str) -> dict[str, Any]:
    """Manifest plus row counts; what the applicant sees first."""
    with _db(spe_id, readonly=True) as conn:
        manifest = _manifest(conn)
        manifest["tables"] = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in manifest["record_types"]}
        manifest["columns"] = {t: [r[1] for r in conn.execute(f"PRAGMA table_info({t})")] for t in manifest["record_types"]}
    manifest.pop("key_hash")
    manifest["page_rows"] = SPE_PAGE_ROWS
    return manifest


# --- queries ---------------------------------------------------------------

def _authorizer(action: int, arg1: str | None, arg2: str | None, db_name: str | None, trigger: str | None) -> int:
    if action == sqlite3.SQLITE_READ:
        return sqlite3.SQLITE_DENY if arg1 in PRIVATE_TABLES else sqlite3.SQLITE_OK
    if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE):
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY  # no writes, no ATTACH, no PRAGMA


def _run(spe_id: str, sql: str, limit: int) -> tuple[list[str], list[dict[str, Any]], bool]:
    """One read-only statement with a step budget; at most `limit` rows, plus whether there were more."""
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
        rows = cur.fetchmany(limit + 1)
        columns = [d[0] for d in cur.description] if cur.description else []
    except (sqlite3.DatabaseError, sqlite3.Warning) as e:
        error = e
    finally:
        cur.close()  # an unexhausted cursor keeps a read lock even after conn.close()
        conn.close()
    if error is not None:
        _audit(spe_id, "query_refused", {"sql": sql, "error": str(error)})
        raise SpeError(400, f"query refused: {error}")
    return columns, [dict(r) for r in rows[:limit]], len(rows) > limit


def query(spe_id: str, sql: str) -> dict[str, Any]:
    """Run SQL inside the SPE and return the first page as an image, never as rows."""
    columns, rows, truncated = _run(spe_id, sql, SPE_PAGE_ROWS)
    mark = f"SPE {spe_id} · on-screen only · {_now()[:16].replace('T', ' ')} UTC"
    png = render_png(columns, rows, mark)
    _audit(spe_id, "query", {"sql": sql.strip(), "rows_shown": len(rows), "truncated": truncated})
    return {
        "columns": columns,
        "rows_shown": len(rows),
        "truncated": truncated,
        "page_rows": SPE_PAGE_ROWS,
        "image": "data:image/png;base64," + base64.b64encode(png).decode(),
        "notice": "Rendered inside the SPE; nothing leaves it.",
    }


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for p in FONT_PATHS:
        if Path(p).is_file():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def render_png(columns: list[str], rows: list[dict[str, Any]], mark: str, cell_chars: int = 40) -> bytes:
    """A table as a PNG with a watermark: readable on screen, not copy-pastable."""
    font, small = _font(13), _font(11)
    fmt = lambda v: "" if v is None else (f"{v:.4g}" if isinstance(v, float) else str(v))
    cells = [[fmt(r.get(c))[:cell_chars] for c in columns] for r in rows]
    widths = [max([len(c)] + [len(row[i]) for row in cells]) for i, c in enumerate(columns)] or [20]
    char_w, line_h, pad = font.getlength("M"), 20, 10
    width = int(sum(w * char_w for w in widths) + pad * 2 * max(len(columns), 1) + 1)
    height = line_h * (len(rows) + 1) + pad * 2 + 18
    img = Image.new("RGB", (max(width, 320), height), "white")
    draw = ImageDraw.Draw(img)
    x, y = pad, pad
    draw.rectangle([0, y - 4, img.width, y + line_h - 4], fill="#e9eef5")
    for i, c in enumerate(columns):
        draw.text((x, y), c, fill="#1f3a5f", font=font)
        x += widths[i] * char_w + pad * 2
    for n, row in enumerate(cells):
        y += line_h
        if n % 2:
            draw.rectangle([0, y - 4, img.width, y + line_h - 4], fill="#f7f8fa")
        x = pad
        for i, v in enumerate(row):
            draw.text((x, y), v, fill="#222", font=font)
            x += widths[i] * char_w + pad * 2
    if not rows:
        draw.text((pad, y + line_h), "no rows", fill="#777", font=font)
    draw.text((pad, img.height - 16), mark, fill="#8aa0bb", font=small)
    stamp = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(stamp)
    for yy in range(0, img.height, 90):
        for xx in range(0, img.width, 260):
            sd.text((xx, yy), mark.split(" · ")[0] + " · on-screen only", fill=(31, 58, 95, 28), font=font)
    img = Image.alpha_composite(img.convert("RGBA"), stamp).convert("RGB")
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


def download(spe_id: str, what: str = "") -> None:
    """There is no download. Logs the attempt and raises 403."""
    _audit(spe_id, "download_blocked", {"what": what or "records"})
    raise SpeError(403, DOWNLOAD_REFUSED)


def audit_log(spe_id: str) -> list[dict[str, Any]]:
    """For the access body's own records (tests, scripts); not exposed over HTTP."""
    with _db(spe_id, readonly=True) as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM audit_log ORDER BY id DESC")]
    for r in rows:
        r["detail"] = json.loads(r["detail"]) if r["detail"] else None
    return rows
