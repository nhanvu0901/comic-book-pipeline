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
    """The download could not get the chapters it needs: none matched, or some came back
    missing or empty."""


class NothingToDeleteError(UserFacingError, ValueError):
    """The folder a delete targets no longer exists — removed from another tab or on disk.

    Callers that only want the thing gone can treat this as success."""


class MissingInputError(UserFacingError, FileNotFoundError):
    """An earlier step's output is not on disk yet. The message names that step as the app
    shows it (the code's stage numbers differ from the app's) plus the terminal command."""


class SourceUrlError(UserFacingError, ValueError):
    """A download has no comic link to work from, or was given one it cannot use."""
