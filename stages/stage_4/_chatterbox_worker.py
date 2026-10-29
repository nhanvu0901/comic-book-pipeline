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
import inspect
import sys
from pathlib import Path


def enable_windows_pread_loader(tts_module, load_file) -> None:
    """Keep Chatterbox model weights off safetensors' Windows mmap path.

    Torch can terminate with a native access violation while indexing mmap-backed safetensors on
    Windows.  safetensors 0.8 introduced ``backend=\"pread\"``, which avoids that path.  Patch the
    loader reference captured by Chatterbox, rather than changing its provider or model files.
    """
    if sys.platform != "win32":
        return
    if "backend" not in inspect.signature(load_file).parameters:
        raise RuntimeError(
            "Windows Chatterbox requires safetensors >= 0.8 for backend='pread'; "
            "repair the isolated .venv-chatterbox, not the pipeline venv.")

    def _pread_load_file(filename, device="cpu"):
        return load_file(filename, device=device, backend="pread")

    tts_module.load_file = _pread_load_file


def main() -> int:
    job = json.loads(Path(sys.argv[1]).read_text())
    out_dir = Path(job["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    import wave

    import torch
    import chatterbox.tts as chatterbox_tts
    from chatterbox.tts import ChatterboxTTS
    from safetensors.torch import load_file as safetensors_load_file

    enable_windows_pread_loader(chatterbox_tts, safetensors_load_file)

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

    import random
    import numpy as np

    def _set_seed(seed_val: int) -> None:
        torch.manual_seed(seed_val)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed_val)
        np.random.seed(seed_val)
        random.seed(seed_val)

    prompt = job.get("audio_prompt") or None
    temperature = float(job.get("temperature", 0.8))
    for i, ch in enumerate(job["chunks"]):
        seed = ch.get("seed")
        if seed is None and job.get("seed") is not None:
            seed = int(job["seed"]) + i * 1000
        if seed is not None:
            _set_seed(int(seed))
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
