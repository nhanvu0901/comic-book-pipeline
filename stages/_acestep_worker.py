"""ACE-Step music worker — runs in .venv-acestep-directml, NOT the pipeline venv.

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
  prints  {"ok": true, "backend": "directml", "device": str, ...} or
          {"error": str, "traceback": str, "backend": "directml", "device": str}

Two things learned the hard way, both load-bearing:
  * The installed ACE-Step API has no device constructor argument. The worker therefore
    assigns the DirectML device to ``pipe.device`` before loading checkpoints; this is the
    attribute used by ACE-Step's load/inference code.
  * torchaudio 2.11 dropped its own backends and routes save() through torchcodec, whose
    DLL does not load on Windows (it wants FFmpeg shared libs). soundfile writes the same
    wav, so save is patched at the boundary rather than by editing ACE-Step's source.
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path


def _directml_device():
    import torch_directml

    device = torch_directml.device()
    if not str(device).lower().startswith("privateuseone"):
        raise RuntimeError(f"DirectML returned an unexpected device: {device}")
    return device


def _build_pipeline(device):
    import torch
    from acestep.pipeline_ace_step import ACEStepPipeline

    # ACE-Step 0.2.0 accepts dtype but not a device object; its float16 string currently
    # resolves to float32, so set the actual dtype after construction as well.
    pipe = ACEStepPipeline(dtype="float16", cpu_offload=False)
    pipe.device = device
    pipe.dtype = torch.float16
    return pipe


def main() -> int:
    device = None
    try:
        job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
        import soundfile as sf
        import torchaudio

        torchaudio.save = lambda p, t, sample_rate=44100, **_k: sf.write(
            str(p), t.detach().float().cpu().numpy().T, int(sample_rate))

        device = _directml_device()
        pipe = _build_pipeline(device)
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
        print(json.dumps({"ok": True, "backend": "directml", "device": str(device),
                          "seconds": float(job["seconds"]),
                          "elapsed": round(time.time() - t0, 1)}), flush=True)
        return 0
    except Exception as exc:  # noqa: BLE001 — the caller turns any failure into "no music"
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}",
                          "traceback": traceback.format_exc(),
                          "backend": "directml", "device": str(device) if device else None}),
              flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
