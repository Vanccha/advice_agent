from __future__ import annotations


def test_channel_seeding_is_idempotent(app_client):
    """Running bootstrap again (on top of the app's own startup run) must not duplicate channels."""
    from app.bootstrap import run_bootstrap
    from app.db import SessionFactory
    from app.models import Channel

    run_bootstrap()
    run_bootstrap()

    session = SessionFactory()
    try:
        channels = session.query(Channel).all()
        slugs = {c.slug for c in channels}
    finally:
        session.close()

    assert len(channels) == 5
    assert slugs == {
        "technical-infra",
        "billing",
        "subscription-ops",
        "field-install",
        "ops-general",
    }
