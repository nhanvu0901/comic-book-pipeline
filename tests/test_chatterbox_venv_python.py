"""The Chatterbox venv's interpreter must resolve on BOTH venv layouts.

A venv puts its interpreter at bin/python on POSIX and Scripts/python.exe on Windows.
chatterbox_tts hard-coded the POSIX path, which made available() permanently False on
Windows and killed every render with "Chatterbox venv missing" — even though the worker
ran fine when invoked directly. A `bin` junction pointing at Scripts did NOT rescue it:
the directory resolved, but the file is python.exe, so bin/python still never existed.

These are layout tests, not platform tests — each builds the layout it means to check, so
they assert the same thing whichever OS runs them.
"""
from stages.stage_4.chatterbox_tts import _venv_python


def _make(venv, *rel):
    """Create an empty stand-in interpreter at venv/<rel...> and return its path."""
    p = venv.joinpath(*rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("")
    return p


def test_posix_layout_resolves_bin_python(tmp_path):
    venv = tmp_path / ".venv-chatterbox"
    want = _make(venv, "bin", "python")
    assert _venv_python(venv) == want


def test_windows_layout_resolves_scripts_python_exe(tmp_path):
    venv = tmp_path / ".venv-chatterbox"
    want = _make(venv, "Scripts", "python.exe")
    assert _venv_python(venv) == want


def test_missing_venv_falls_back_to_the_posix_path(tmp_path):
    """No venv at all → still name a sane path, because the caller puts it in the
    "create it with…" error message."""
    venv = tmp_path / ".venv-chatterbox"
    assert _venv_python(venv) == venv / "bin" / "python"


def test_available_reads_the_env_var_and_re_probes_per_call(tmp_path, monkeypatch):
    """available() must resolve per CALL: the old module constant fixed the PATH at import
    and only late-bound its .exists(), so a venv created mid-session stayed invisible."""
    from stages.stage_4 import chatterbox_tts as cb
    venv = tmp_path / ".venv-chatterbox"
    monkeypatch.setenv("CHATTERBOX_VENV", str(venv))
    assert cb.available() is False
    _make(venv, "Scripts", "python.exe")
    assert cb.available() is True
