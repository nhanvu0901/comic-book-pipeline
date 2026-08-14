"""ACE-Step music worker — runs in .venv-acestep, NOT the pipeline venv.

Kept dependency-free of the repo for the same reason as _chatterbox_worker.py: ACE-Step
pulls its own torch, transformers 4.50, diffusers and gradio, and installing that over the
pipeline venv would move huggingface_hub under Magi (Stage 2 panel detection, which works).
It talks over a JSON file and a wav on disk.

Protocol
--------
  argv[1] = job JSON:
      {"prompt": str,          # ACE-Step tag list, passed VERBATIM
       "seconds": float,
       "out": str,
       "steps": int, "guidance": float, "seed": int}
  writes  out (wav)
  prints  {"ok": true, "seconds": float, "elapsed": float}  or  {"error": "..."}

Two things learned the hard way, both load-bearing:
  * dtype MUST be bfloat16. Forcing float32 converts 7.8GB of weights element-wise on CPU
    and the load does not finish inside ten minutes; bfloat16 loads in ~12s.
  * torchaudio 2.11 dropped its own backends and routes save() through torchcodec, whose
    DLL does not load on Windows (it wants FFmpeg shared libs). soundfile writes the same
    wav, so save is patched at the boundary rather than by editing ACE-Step's source.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path


def main() -> int:
    job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    try:
        import soundfile as sf
        import torchaudio

        torchaudio.save = lambda p, t, sample_rate=44100, **_k: sf.write(
            str(p), t.detach().float().cpu().numpy().T, int(sample_rate))

        from acestep.pipeline_ace_step import ACEStepPipeline

        pipe = ACEStepPipeline(dtype="bfloat16")
        pipe.load_checkpoint(pipe.checkpoint_dir)

        t0 = time.time()
        pipe(
            format="wav",
            audio_duration=float(job["seconds"]),
            prompt=job["prompt"],
            lyrics="[instrumental]",
            infer_step=int(job.get("steps", 27)),
            guidance_scale=float(job.get("guidance", 15.0)),
            scheduler_type="euler",
            cfg_type="apg",
            omega_scale=10.0,
            manual_seeds=[int(job.get("seed", 42))],   # fixed seed → same brief, same track
            save_path=job["out"],
            batch_size=1,
        )
        print(json.dumps({"ok": True, "seconds": float(job["seconds"]),
                          "elapsed": round(time.time() - t0, 1)}), flush=True)
        return 0
    except Exception as exc:  # noqa: BLE001 — the caller turns any failure into "no music"
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}), flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
