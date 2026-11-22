"""Profile directory (spec §7.4): roster from operator config + heartbeats.

The directory never reads memory or credential files (FR-023). Roster entries
come from operator configuration; liveness comes from heartbeats written by
each profile's HIB instance and bus lease activity.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .db import Database


@dataclass
class ProfileEntry:
    profile: str
    description: str = ""
    capabilities: list[str] = field(default_factory=list)
    status: str = "offline"
    last_heartbeat: float | None = None


class Directory:
    """Roster and health for local profiles (FR-021..FR-025)."""

    def __init__(self, db: Database, *, roster: list[dict[str, Any]] | None = None,
                 refresh_seconds: int = 60) -> None:
        self._db = db
        self.refresh_seconds = max(1, int(refresh_seconds))
        self._roster: dict[str, dict[str, Any]] = {}
        for entry in roster or []:
            profile = str(entry.get("profile", "")).strip()
            if not profile:
                continue
            self._roster[profile] = {
                "description": str(entry.get("description", "")),
                "capabilities": [str(c) for c in entry.get("capabilities", [])],
            }

    # ----------------------------------------------------------------- roster

    def known(self, profile: str) -> bool:
        return profile in self._roster

    def entries(self, *, bus_counts: dict[str, dict[str, int]] | None = None) -> list[ProfileEntry]:
        """Roster entries with computed status; *bus_counts* maps profile → status counts."""
        result = []
        now = time.time()
        busy_profiles = set()
        if bus_counts:
            for profile, counts in bus_counts.items():
                if counts.get("processing", 0) > 0:
                    busy_profiles.add(profile)
        for profile, meta in sorted(self._roster.items()):
            last = self.last_heartbeat(profile)
            if last is not None and now - last <= self.refresh_seconds * 2:
                status = "busy" if profile in busy_profiles else "online"
            else:
                status = "offline"
            result.append(
                ProfileEntry(
                    profile=profile,
                    description=meta["description"],
                    capabilities=meta["capabilities"],
                    status=status,
                    last_heartbeat=last,
                )
            )
        return result

    def status_for(self, profile: str, *, bus_counts: dict[str, dict[str, int]] | None = None) -> str | None:
        """online | busy | offline for a known profile; None for an unknown one (FR-024)."""
        if not self.known(profile):
            return None
        for entry in self.entries(bus_counts=bus_counts):
            if entry.profile == profile:
                return entry.status
        return None

    # ------------------------------------------------------------- heartbeats

    def heartbeat(self, profile: str, *, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO hib_heartbeats (profile, updated_at) VALUES (?, ?)
                ON CONFLICT(profile) DO UPDATE SET updated_at=excluded.updated_at
                """,
                (profile, now),
            )

    def last_heartbeat(self, profile: str) -> float | None:
        row = self._db.query_one("SELECT updated_at FROM hib_heartbeats WHERE profile=?", (profile,))
        return row["updated_at"] if row else None

    def stale(self, profile: str) -> bool:
        """True when the profile is known but has not heartbeat recently."""
        last = self.last_heartbeat(profile)
        if last is None:
            return True
        return (time.time() - last) > self.refresh_seconds * 2
