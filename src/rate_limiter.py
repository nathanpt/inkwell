from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass

from src import db

logger = logging.getLogger(__name__)


@dataclass
class RateLimitConfig:
    multiplier_step: float = 1.5
    max_multiplier: float = 8.0
    pause_threshold: float = 6.0
    decay_rate: float = 0.5


@dataclass
class SiteRateState:
    hit_count: int = 0
    cooldown_multiplier: float = 1.0


def _state_key(site: str) -> str:
    return f"rate_limit:{site}"


def _load(conn: sqlite3.Connection, site: str) -> SiteRateState:
    raw = db.get_state(conn, _state_key(site))
    if raw:
        data = json.loads(raw)
        return SiteRateState(**data)
    return SiteRateState()


def _save(conn: sqlite3.Connection, site: str, state: SiteRateState) -> None:
    db.set_state(conn, _state_key(site), json.dumps({
        "hit_count": state.hit_count,
        "cooldown_multiplier": state.cooldown_multiplier,
    }))


def record_hit(conn: sqlite3.Connection, site: str, config: RateLimitConfig) -> None:
    state = _load(conn, site)
    state.hit_count += 1
    state.cooldown_multiplier = min(
        state.cooldown_multiplier * config.multiplier_step,
        config.max_multiplier,
    )
    _save(conn, site, state)
    logger.warning(
        "Rate limit hit for %s (count=%d, multiplier=%.1f)",
        site, state.hit_count, state.cooldown_multiplier,
    )


def record_success(conn: sqlite3.Connection, site: str, config: RateLimitConfig) -> None:
    state = _load(conn, site)
    if state.cooldown_multiplier <= 1.0:
        return
    state.cooldown_multiplier = max(
        1.0,
        state.cooldown_multiplier - config.decay_rate,
    )
    if state.cooldown_multiplier <= 1.0:
        state.hit_count = 0
    _save(conn, site, state)


def get_cooldown_multiplier(conn: sqlite3.Connection, site: str) -> float:
    return _load(conn, site).cooldown_multiplier


def is_site_paused(conn: sqlite3.Connection, site: str, config: RateLimitConfig) -> bool:
    return _load(conn, site).cooldown_multiplier >= config.pause_threshold
