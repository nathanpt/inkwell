from __future__ import annotations

from src import db
from src.rate_limiter import (
    RateLimitConfig,
    get_cooldown_multiplier,
    is_site_paused,
    record_hit,
    record_success,
)


class TestRateLimiter:
    def test_initial_state(self, db_conn):
        assert get_cooldown_multiplier("x.com") == 1.0
        assert not is_site_paused("x.com", RateLimitConfig())

    def test_record_hit_increases_multiplier(self, db_conn):
        config = RateLimitConfig(multiplier_step=2.0, max_multiplier=16.0)
        record_hit("x.com", config)
        assert get_cooldown_multiplier("x.com") == 2.0

        record_hit("x.com", config)
        assert get_cooldown_multiplier("x.com") == 4.0

    def test_multiplier_capped_at_max(self, db_conn):
        config = RateLimitConfig(multiplier_step=4.0, max_multiplier=8.0)
        record_hit("x.com", config)
        assert get_cooldown_multiplier("x.com") == 4.0

        record_hit("x.com", config)
        # 4.0 * 4.0 = 16.0, capped at 8.0
        assert get_cooldown_multiplier("x.com") == 8.0

    def test_pause_threshold(self, db_conn):
        config = RateLimitConfig(multiplier_step=2.0, max_multiplier=16.0, pause_threshold=6.0)
        record_hit("x.com", config)  # 2.0
        assert not is_site_paused("x.com", config)

        record_hit("x.com", config)  # 4.0
        assert not is_site_paused("x.com", config)

        record_hit("x.com", config)  # 8.0
        assert is_site_paused("x.com", config)

    def test_success_decays_multiplier(self, db_conn):
        config = RateLimitConfig(multiplier_step=2.0, decay_rate=0.5)
        record_hit("x.com", config)  # 2.0

        record_success("x.com", config)  # 2.0 - 0.5 = 1.5
        assert get_cooldown_multiplier("x.com") == 1.5

    def test_success_resets_to_baseline(self, db_conn):
        config = RateLimitConfig(multiplier_step=2.0, decay_rate=1.0)
        record_hit("x.com", config)  # 2.0

        record_success("x.com", config)  # 2.0 - 1.0 = 1.0
        assert get_cooldown_multiplier("x.com") == 1.0

    def test_success_no_op_at_baseline(self, db_conn):
        config = RateLimitConfig()
        # No hits recorded — success should be a no-op
        record_success("x.com", config)
        assert get_cooldown_multiplier("x.com") == 1.0

    def test_per_site_isolation(self, db_conn):
        config = RateLimitConfig(multiplier_step=2.0)
        record_hit("x.com", config)  # 2.0
        record_hit("pixiv", config)  # 2.0
        record_hit("x.com", config)  # 4.0

        assert get_cooldown_multiplier("x.com") == 4.0
        assert get_cooldown_multiplier("pixiv") == 2.0
        assert get_cooldown_multiplier("deviantart") == 1.0
