"""Verify that the isolated ACE-Step runtime exposes a DirectML device."""

import torch
import torch_directml
import soundfile
import torchaudio

from acestep.pipeline_ace_step import ACEStepPipeline


def main() -> None:
    device = torch_directml.device()
    device_name = str(device)
    assert device_name.startswith(
        "privateuseone"
    ), f"Expected a DirectML privateuseone device, got {device_name!r}"

    print(f"torch={torch.__version__}")
    print(f"soundfile={soundfile.__version__}")
    print(f"torchaudio={torchaudio.__version__}")
    print(f"pipeline={ACEStepPipeline.__module__}.{ACEStepPipeline.__name__}")
    print(f"directml_device={device_name}")


if __name__ == "__main__":
    main()
