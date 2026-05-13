from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_PATH = Path("/app/config/config.toml")


@dataclass
class NASConfig:
    mount_path: str = "/nas/inkwell"
    display_path: str | None = None


@dataclass
class ScheduleConfig:
    cron: str = "0 3 * * *"


@dataclass
class DownloadConfig:
    retry_attempts: int = 3
    retry_backoff: list[int] = field(default_factory=lambda: [5, 15, 45])
    timeout: int = 600
    inter_artist_cooldown: list[int] = field(default_factory=lambda: [30, 60])


@dataclass
class CookiesConfig:
    expiry_warning_days: int = 30


@dataclass
class AuthConfig:
    password_hash: str = ""


@dataclass
class RetentionConfig:
    log_days: int = 90


@dataclass
class RateLimitConfig:
    multiplier_step: float = 1.5
    max_multiplier: float = 8.0
    pause_threshold: float = 6.0
    decay_rate: float = 0.5


@dataclass
class SiteConfig:
    cooldown: list[int] = field(default_factory=lambda: [30, 60])


@dataclass
class Config:
    nas: NASConfig = field(default_factory=NASConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    download: DownloadConfig = field(default_factory=DownloadConfig)
    cookies: CookiesConfig = field(default_factory=CookiesConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    sites: dict[str, SiteConfig] = field(default_factory=dict)


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    raw = tomllib.loads(path.read_text())
    sites_raw = raw.pop("sites", {})
    sites = {
        name: SiteConfig(**cfg) for name, cfg in sites_raw.items()
    }
    return Config(
        nas=NASConfig(**raw.get("nas", {})),
        schedule=ScheduleConfig(**raw.get("schedule", {})),
        download=DownloadConfig(**raw.get("download", {})),
        cookies=CookiesConfig(**raw.get("cookies", {})),
        auth=AuthConfig(**raw.get("auth", {})),
        retention=RetentionConfig(**raw.get("retention", {})),
        rate_limit=RateLimitConfig(**raw.get("rate_limit", {})),
        sites=sites,
    )
