"""Review Read Model — the single owner of the pending-review count.

Both the sidebar badge and the Review page must agree on "how much is waiting for a
decision". That number is computed in exactly one place; everything else reads it and
never recomputes it from the raw queues. The computation itself lives in the ``review``
business module (it needs the integrity scan, not just the repositories); this Read
Model is the stable, named entry point every caller goes through, so a second count
source can never quietly appear.
"""
from __future__ import annotations

from ..review import inbox as _inbox


def review_inbox() -> dict:
    """The one and only source of the Review pending total and its per-kind groups."""
    return _inbox()
