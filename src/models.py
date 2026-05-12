from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Artist:
    id: int | None = None
    handle: str = ""
    site: str = "x.com"
    source_url: str = ""
    added_at: str | None = None
    last_scan_at: str | None = None
    is_active: bool = True


@dataclass
class Job:
    id: int | None = None
    artist_id: int | None = None
    status: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    file_count: int = 0
    total_bytes: int = 0
    error_message: str | None = None
    triggered_by: str = "manual"
