import json
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

import config
from stages.screen_pipeline import run_screen_pipeline, _parse_args


def test_screen_pipeline_cli_arg_parser():
    args = _parse_args(["--question", "How did Avengers time travel?", "--project", "demo_screen_test"])
    assert args.question == "How did Avengers time travel?"
    assert args.project == "demo_screen_test"


def test_screen_pipeline_end_to_end_mock(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROJECTS_ROOT", tmp_path)
    import stages.review_gate
    monkeypatch.setattr(stages.review_gate, "PROJECTS_ROOT", tmp_path)
    proj_dir = tmp_path / "mock_screen_pipeline"
    proj_dir.mkdir(parents=True)

    # 1. Mock research
    mock_res = {
        "work_title": "Avengers: Endgame",
        "release_year": "2019",
        "citation": "Avengers: Endgame (2019)",
        "summary": "Quantum Realm time travel.",
        "items": [{"title": "Beat 1", "description": "Desc 1", "clip_query": "query 1"}]
    }

    # 2. Mock narration
    mock_nar = {
        "title": "Time Travel in Endgame",
        "mode": "screen_qa",
        "scenes": [
            {
                "scene_id": 1,
                "text": "In Avengers: Endgame (2019), they used Quantum particles.",
                "visual_beats": ["In Avengers: Endgame (2019),", "they used Quantum particles."]
            }
        ]
    }

    # 3. Mock TTS result
    mock_tts = MagicMock(audio_duration_seconds=2.0)

    # Create dummy audio.wav
    audio_wav = proj_dir / "audio.wav"
    audio_wav.write_bytes(b"RIFFdummywav")

    with patch("stages.stage_1.screen_research._call_screen_research_llm", return_value=mock_res), \
         patch("stages.stage_3.screen_narration._call_screen_narration_llm", return_value=mock_nar), \
         patch("stages.stage_4.pipeline.synthesize_project", return_value=mock_tts), \
         patch("stages.screen_pipeline._fetch_screen_clips", return_value=1), \
         patch("stages.stage_5.screen_shots.render_screen_shot", side_effect=lambda shot, out_p, **kw: (out_p.write_bytes(b"mp4"), out_p)[1]), \
         patch("stages.stage_5.screen_shots.assemble_screen_video", side_effect=lambda sl, sp, out_v, **kw: (out_v.write_bytes(b"mp4"), out_v)[1]), \
         patch("stages.screen_pipeline._merge_audio_video", side_effect=lambda v, a, out_f: (out_f.write_bytes(b"final"), out_f)[1]):

        final_mp4 = run_screen_pipeline("How did Avengers time travel?", "mock_screen_pipeline")
        assert final_mp4.exists()
        assert final_mp4.name == "final.mp4"
        assert (proj_dir / "screen_context.json").exists()
        assert (proj_dir / "narration.json").exists()
