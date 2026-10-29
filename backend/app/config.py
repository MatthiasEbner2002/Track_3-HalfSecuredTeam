"""Runtime settings, read once from environment variables at import time."""

import os
from dataclasses import dataclass
from pathlib import Path


def _split(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    data_dir: Path  # folder with the five CSV files
    spe_dir: Path  # one sub-folder per secure processing environment
    cors_origins: tuple[str, ...]
    reference_date: str = "2025-12-31"  # date for ages and active medications
    small_cell_threshold: int = 10  # counts below this are never shown

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            data_dir=Path(os.environ.get("EHDS_DATA_DIR", "../data")),
            spe_dir=Path(os.environ.get("EHDS_SPE_DIR", "./spe")),
            cors_origins=_split(
                os.environ.get(
                    "EHDS_CORS_ORIGINS", "http://localhost:5173,http://localhost:3000"
                )
            ),
        )


settings = Settings.from_env()
