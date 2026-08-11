from __future__ import annotations

from src import db
from src.models import Artist


class TestReactivateArtist:
    def _make(self, handle="insact9704", url="https://x.com/insact9704") -> Artist:
        a = Artist(handle=handle, site="x.com", source_url=url)
        a.id = db.insert_artist(a)
        return a

    def test_new_artist_is_active(self, db_conn):
        a = self._make()
        assert a.is_active is True
        assert any(x.id == a.id for x in db.get_active_artists())

    def test_deactivate_excludes_from_active(self, db_conn):
        a = self._make()
        db.deactivate_artist(a.id)
        assert not any(x.id == a.id for x in db.get_active_artists())
        # ...but the row still exists and is findable by URL (inactive)
        existing = db.get_artist_by_url(a.source_url)
        assert existing is not None
        assert existing.is_active is False

    def test_reactivate_returns_artist_to_active_list(self, db_conn):
        """The re-add path: an inactive artist is found by URL and reactivated."""
        a = self._make()
        db.deactivate_artist(a.id)

        existing = db.get_artist_by_url(a.source_url)
        assert existing is not None and not existing.is_active

        # The fix: reactivate (previously this called deactivate_artist, a no-op
        # on an already-inactive row, leaving the artist invisible).
        db.reactivate_artist(existing.id)

        active = db.get_active_artists()
        assert any(x.id == a.id for x in active)
        assert all(x.is_active for x in active)

    def test_deactivate_then_reactivate_is_idempotent(self, db_conn):
        a = self._make()
        db.reactivate_artist(a.id)  # already active -> stays active
        assert any(x.id == a.id for x in db.get_active_artists())
        db.deactivate_artist(a.id)
        db.deactivate_artist(a.id)  # double-remove is a no-op
        assert not any(x.id == a.id for x in db.get_active_artists())
