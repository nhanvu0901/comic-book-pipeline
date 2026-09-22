"""Exceptions deliberately raised by the research-scout user workflow."""

from __future__ import annotations


class ScoutUserError(ValueError):
    """A complete, safe message that the scout UI may show without a traceback.

    Ordinary ``ValueError`` remains an unexpected programming or downstream
    failure.  The explicit marker keeps that diagnostic traceback visible
    instead of treating every value error as user-facing copy.
    """

