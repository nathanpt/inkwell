from __future__ import annotations

import json
import logging
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
    }))


def record_hit(site: str, config: RateLimitConfig) -> None:
    state = _load(site)
    state.hit_count += 1
    state.cooldown_multiplier = min(
        state.cooldown_multiplier * config.multiplier_step,
        config.max_multiplier,
    )
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
    return _load(site).cooldown_multiplier >= config.pause_threshold
