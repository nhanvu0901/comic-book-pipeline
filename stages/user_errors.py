"""Errors whose message is complete, human-readable copy.

The UI shows ``str(e)`` alone for these (see ``ui.bridge.format_exception``); anything
else keeps its traceback, because an unexpected exception is a bug to investigate.
Subclasses also inherit the builtin their callers already catch (ValueError,
RuntimeError), so existing ``except`` clauses keep working."""
from __future__ import annotations


class UserFacingError(Exception):
    """Base marker: the message tells the user what happened and what to do."""


class ScriptMappingError(UserFacingError, ValueError):
    """A pasted narration script does not fit the project's answer items."""


class DownloadIncompleteError(UserFacingError, RuntimeError):
    """One or more chapters did not download completely."""


class NothingToDeleteError(UserFacingError, ValueError):
    """The folder a delete targets no longer exists — removed from another tab or on disk.

    Callers that only want the thing gone can treat this as success."""
