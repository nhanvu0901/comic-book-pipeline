"""Chatterbox TTS worker — runs in .venv-chatterbox, NOT the pipeline venv.

Kept dependency-free of the repo on purpose: chatterbox-tts drags in transformers 5,
huggingface_hub 1.x, gradio and its own torch, and installing that over the pipeline venv
would move huggingface_hub 0.36 -> 1.26 under Magi (Stage 2 panel detection, which works).
So it lives in a separate venv and talks over a JSON file + a directory of WAVs.

Protocol
--------
  argv[1] = job JSON:
      {"chunks": [{"text": str, "exaggeration": float, "cfg_weight": float}, ...],
       "out_dir": str,
       "audio_prompt": str | null,      # reference wav = the voice to clone
       "temperature": float,
       "device": "mps" | "cuda" | "cpu"}
  writes  out_dir/chunk_<i>.wav  (one per chunk, model's native sample rate)
  prints  one JSON line per finished chunk: {"i": int, "sec": float, "sr": int}
          so the caller can show progress on a 380-chunk longform run, and a
          {"error": ...} line if a chunk fails.

`exaggeration` is Chatterbox's emotion knob (0.25 flat … 2.0 histrionic, 0.5 neutral) and
`cfg_weight` its pacing/adherence knob — lower reads slower and looser. They are per-chunk
so a narration can carry emotion per scene.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    job = json.loads(Path(sys.argv[1]).read_text())
    out_dir = Path(job["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    import wave

    import torch
    from chatterbox.tts import ChatterboxTTS

    def _save_wav(path: Path, wav, sr: int) -> None:
        """Write float32 [-1,1] as 16-bit PCM. torchaudio.save is NOT used: torchaudio 2.11
        routes it through TorchCodec, which is another native dependency to install and
        keep matched to torch — for mono 16-bit the stdlib does the whole job."""
        x = wav.detach().cpu().reshape(-1).clamp(-1.0, 1.0)
        pcm = (x * 32767.0).round().to(torch.int16).numpy().tobytes()
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(int(sr))
            wf.writeframes(pcm)

    device = job.get("device") or ("mps" if torch.backends.mps.is_available() else "cpu")
    model = ChatterboxTTS.from_pretrained(device=device)
    print(json.dumps({"ready": True, "device": device, "sr": int(model.sr)}), flush=True)

    prompt = job.get("audio_prompt") or None
    temperature = float(job.get("temperature", 0.8))
    for i, ch in enumerate(job["chunks"]):
        try:
            wav = model.generate(
                ch["text"],
                audio_prompt_path=prompt,
                exaggeration=float(ch.get("exaggeration", 0.5)),
                cfg_weight=float(ch.get("cfg_weight", 0.5)),
                temperature=temperature,
            )
            path = out_dir / f"chunk_{i:05d}.wav"
            _save_wav(path, wav, model.sr)
            sec = wav.shape[-1] / float(model.sr)
            print(json.dumps({"i": i, "sec": round(sec, 4), "sr": int(model.sr)}), flush=True)
        except Exception as exc:                      # one bad chunk must not lose the run
            print(json.dumps({"i": i, "error": repr(exc)}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
