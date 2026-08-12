from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

from src import db

logger = logging.getLogger(__name__)


@dataclass
class RateLimitConfig:
    multiplier_step: float = 1.5
    max_multiplier: float = 8.0
    pause_threshold: float = 6.0
    decay_rate: float = 0.5
    pause_seconds: int = 900  # max seconds a site stays "paused" since its last hit


@dataclass
class SiteRateState:
    hit_count: int = 0
    cooldown_multiplier: float = 1.0
    last_hit_ts: float = 0.0


def _state_key(site: str) -> str:
    return f"rate_limit:{site}"


def _load(site: str) -> SiteRateState:
    raw = db.get_state(_state_key(site))
    if raw:
        data = json.loads(raw)
        return SiteRateState(**data)
    return SiteRateState()


def _save(site: str, state: SiteRateState) -> None:
    db.set_state(_state_key(site), json.dumps({
        "hit_count": state.hit_count,
        "cooldown_multiplier": state.cooldown_multiplier,
        "last_hit_ts": state.last_hit_ts,
    }))


def record_hit(site: str, config: RateLimitConfig) -> None:
    state = _load(site)
    state.hit_count += 1
    state.cooldown_multiplier = min(
        state.cooldown_multiplier * config.multiplier_step,
        config.max_multiplier,
    )
    state.last_hit_ts = time.time()
    _save(site, state)
    logger.warning(
        "Rate limit hit for %s (count=%d, multiplier=%.1f)",
        site, state.hit_count, state.cooldown_multiplier,
    )


def record_success(site: str, config: RateLimitConfig) -> None:
    state = _load(site)
    if state.cooldown_multiplier <= 1.0:
        return
    state.cooldown_multiplier = max(
        1.0,
        state.cooldown_multiplier - config.decay_rate,
    )
    if state.cooldown_multiplier <= 1.0:
        state.hit_count = 0
    _save(site, state)


def get_cooldown_multiplier(site: str) -> float:
    return _load(site).cooldown_multiplier


def is_site_paused(site: str, config: RateLimitConfig) -> bool:
    state = _load(site)
    if state.cooldown_multiplier < config.pause_threshold:
        return False
    # A pause is bounded by the rate window: once ``pause_seconds`` have passed
    # since the last hit the upstream limit has reset, so the site is no longer
    # considered paused (the multiplier still scales cooldowns until successes
    # decay it). An unknown last-hit time (legacy state) is treated as already
    # expired so previously-stuck sites unblock instead of skipping forever.
    if not state.last_hit_ts:
        return False
    return (time.time() - state.last_hit_ts) < config.pause_seconds
