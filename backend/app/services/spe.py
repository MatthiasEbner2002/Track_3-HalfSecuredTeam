"""Secure processing environment (SPE), one per APPROVE_SPE request (Art. 73).

Provisioning copies only the selected patients and the record types the request
names into a separate folder under settings.spe_dir, keeping their patient IDs.
Downloads are always blocked. Every access and blocked download is audit-logged.
"""

from pathlib import Path
from typing import Any


def provision(request_id: str, patient_ids: list[str], record_types: list[str]) -> Path:
    """Create the SPE for a request and copy the selected records into it."""
    raise NotImplementedError


def describe(request_id: str) -> dict[str, Any]:
    """Return the SPE's record types, exact patient count and audit log."""
    raise NotImplementedError


def block_download(request_id: str) -> None:
    """Log a download attempt; the API then answers 403 citing Art. 73."""
    raise NotImplementedError


def audit(request_id: str, event: str) -> None:
    """Append an event to the SPE's audit log."""
    raise NotImplementedError
