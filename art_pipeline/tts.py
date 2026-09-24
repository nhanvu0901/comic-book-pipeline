"""A5: thin wrapper over comic Stage 4 (Cartesia TTS).

Stage 4 resolves paths via its module-level PROJECTS_ROOT; we point that at
ART_PROJECTS_ROOT at call time (runtime attribute override — comic files are
never edited; spec §2/§6.3). The art CLI/app runs in its own process, so the
override never affects a running comic pipeline.

`calm=True` (default) gives the soothing "chill / easy to fall asleep" voice:
calm emotion + slower pace passed to Stage 4 as kwargs, plus a length-preserving
frequency-shaping pass on the finished WAV (audio_fx). Comic stays untouched."""
from . import config as C
from .config import ART_PROJECTS_ROOT


def _apply_calm_audio(project_name: str, log=print) -> None:
    from .audio_fx import apply_calm_filters
    apply_calm_filters(ART_PROJECTS_ROOT / project_name / "audio.wav",
                       lowpass_hz=C.ART_CALM_LOWPASS_HZ,
                       bass_gain_db=C.ART_CALM_BASS_GAIN_DB,
                       deess_gain_db=C.ART_CALM_DEESS_GAIN_DB,
                       lufs=C.ART_CALM_LUFS, log=log)


def synthesize_art(project_name: str, *, calm: bool = True, **kwargs):
    import stages.stage_4.pipeline as s4
    # Save/restore like longform_tts.synthesize_longform: PROJECTS_ROOT is a shared
    # module attribute, so a raise here must not leave it pointed at art_projects/
    # for the rest of the process (the next comic TTS call would read/write it).
    prev_root = s4.PROJECTS_ROOT
    try:
        s4.PROJECTS_ROOT = ART_PROJECTS_ROOT
        if calm:
            # caller kwargs win; otherwise apply the calm-voice defaults
            kwargs.setdefault("emotion", C.ART_VOICE_EMOTION)
            kwargs.setdefault("speed", C.ART_VOICE_SPEED)
            kwargs.setdefault("volume", C.ART_VOICE_VOLUME)
            kwargs.setdefault("post_atempo", C.ART_POST_ATEMPO)
        # Only (re)shape audio that was actually (re)generated — never double-apply
        # the frequency pass onto a reused WAV.
        audio_existed = (ART_PROJECTS_ROOT / project_name / "audio.wav").exists()
        regenerated = bool(kwargs.get("force")) or not audio_existed
        result = s4.synthesize_project(project_name, **kwargs)
    finally:
        s4.PROJECTS_ROOT = prev_root
    if calm and C.ART_CALM_AUDIO and regenerated:
        _apply_calm_audio(project_name)
    return result
