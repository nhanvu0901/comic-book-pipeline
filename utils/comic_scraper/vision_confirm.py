"""
GLM Vision panel confirmation — score how well a comic page matches a scene description.

Uses the GLM vision model to evaluate whether a given image matches the expected
visual content of a scene. Optional feature, controlled by ENABLE_VISION_CONFIRM.
"""
import base64
import json
from pathlib import Path

import requests

from config import GLM_API_KEY, GLM_BASE_URL, GLM_VISION_MODEL


def _encode_image(image_path: Path) -> str:
    """Encode an image file to base64."""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def confirm_panel(
    image_path: str | Path,
    scene_description: str,
    api_key: str | None = None,
) -> float:
    """
    Score how well an image matches a scene's visual description.

    Args:
        image_path: Path to the comic page image.
        scene_description: The visual_description from the scene.
        api_key: GLM API key (defaults to config).

    Returns:
        Confidence score 0.0 to 1.0.
    """
    api_key = api_key or GLM_API_KEY
    if not api_key:
        return 0.0

    image_path = Path(image_path)
    if not image_path.exists():
        return 0.0

    b64_image = _encode_image(image_path)

    prompt = (
        f"You are evaluating whether this comic book page matches the following scene description.\n\n"
        f"Scene description: \"{scene_description}\"\n\n"
        f"Rate how well this image matches the description on a scale from 0.0 to 1.0, where:\n"
        f"- 0.0 = completely unrelated\n"
        f"- 0.3 = same comic but wrong scene\n"
        f"- 0.6 = related scene but not exact match\n"
        f"- 0.8 = very close match\n"
        f"- 1.0 = exact match\n\n"
        f"Respond with ONLY a JSON object: {{\"score\": 0.X, \"reason\": \"brief explanation\"}}"
    )

    try:
        resp = requests.post(
            f"{GLM_BASE_URL}/v1/messages",
            headers={
                "x-api-key": api_key,
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": GLM_VISION_MODEL,
                "max_tokens": 200,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/jpeg",
                                    "data": b64_image,
                                },
                            },
                            {
                                "type": "text",
                                "text": prompt,
                            },
                        ],
                    }
                ],
            },
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()

        # Extract text content from response
        text = ""
        for block in result.get("content", []):
            if block.get("type") == "text":
                text = block["text"]
                break

        # Parse score from JSON response
        parsed = json.loads(text)
        score = float(parsed.get("score", 0.0))
        return max(0.0, min(1.0, score))

    except Exception as e:
        print(f"Vision confirm failed: {e}")
        return 0.0


def rank_pages(
    image_paths: list[Path],
    scene_description: str,
    api_key: str | None = None,
) -> list[tuple[Path, float]]:
    """
    Score all pages against a scene description and return sorted by confidence.

    Args:
        image_paths: List of comic page image paths.
        scene_description: The visual_description from the scene.
        api_key: GLM API key.

    Returns:
        List of (path, score) tuples sorted by score descending.
    """
    scored = []
    for path in image_paths:
        score = confirm_panel(path, scene_description, api_key)
        scored.append((path, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored
