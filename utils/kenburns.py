"""
Ken Burns effect engine.
Generates slow zoom/pan animations from static images using MoviePy.
"""
import numpy as np
from moviepy.editor import ImageClip
from PIL import Image


def apply_kenburns(
    image_path: str,
    duration: float,
    effect: str = "slow_zoom_in",
    video_size: tuple = (1920, 1080),
    fps: int = 30,
    zoom_range: tuple = (1.0, 1.15),
) -> ImageClip:
    """
    Apply Ken Burns effect to a single image.

    Args:
        image_path: Path to the image file
        duration: Duration in seconds
        effect: One of: slow_zoom_in, slow_zoom_out, pan_left, pan_right,
                pan_up, pan_down, static
        video_size: Output (width, height)
        fps: Frames per second
        zoom_range: (start_zoom, end_zoom) as multipliers

    Returns:
        MoviePy clip with the Ken Burns effect applied
    """
    w, h = video_size

    # Load image oversized for zooming/panning headroom
    # We scale to ~130% of target so we have room to move
    img_pil = Image.open(image_path).convert("RGB")
    scale_factor = 1.3
    oversized = (int(w * scale_factor), int(h * scale_factor))
    img_pil = _crop_to_aspect(img_pil, w, h)
    img_pil = img_pil.resize(oversized, Image.LANCZOS)
    img_array = np.array(img_pil)

    ow, oh = oversized  # oversized width/height

    def make_frame(t):
        progress = t / max(duration, 0.001)  # 0.0 → 1.0
        progress = _ease_in_out(progress)     # smooth easing

        if effect == "slow_zoom_in":
            zoom = zoom_range[0] + (zoom_range[1] - zoom_range[0]) * progress
            cx, cy = ow / 2, oh / 2

        elif effect == "slow_zoom_out":
            zoom = zoom_range[1] - (zoom_range[1] - zoom_range[0]) * progress
            cx, cy = ow / 2, oh / 2

        elif effect == "pan_left":
            zoom = (zoom_range[0] + zoom_range[1]) / 2
            cx = ow / 2 + (ow * 0.08) * (1 - progress)
            cy = oh / 2

        elif effect == "pan_right":
            zoom = (zoom_range[0] + zoom_range[1]) / 2
            cx = ow / 2 - (ow * 0.08) * (1 - progress)
            cy = oh / 2

        elif effect == "pan_up":
            zoom = (zoom_range[0] + zoom_range[1]) / 2
            cx = ow / 2
            cy = oh / 2 + (oh * 0.08) * (1 - progress)

        elif effect == "pan_down":
            zoom = (zoom_range[0] + zoom_range[1]) / 2
            cx = ow / 2
            cy = oh / 2 - (oh * 0.08) * (1 - progress)

        else:  # static
            zoom = 1.05  # slight zoom so it doesn't look completely dead
            cx, cy = ow / 2, oh / 2

        # Calculate crop window
        crop_w = int(w / zoom)
        crop_h = int(h / zoom)

        # Clamp center so we don't go out of bounds
        x1 = int(max(0, min(cx - crop_w / 2, ow - crop_w)))
        y1 = int(max(0, min(cy - crop_h / 2, oh - crop_h)))
        x2 = x1 + crop_w
        y2 = y1 + crop_h

        # Crop and resize to output dimensions
        cropped = img_array[y1:y2, x1:x2]

        # Fast resize using numpy/PIL
        frame_pil = Image.fromarray(cropped).resize((w, h), Image.LANCZOS)
        return np.array(frame_pil)

    clip = ImageClip(image_path).set_duration(duration).set_fps(fps)
    clip = clip.fl(lambda gf, t: make_frame(t))
    clip = clip.set_duration(duration)

    return clip


def _crop_to_aspect(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """Center-crop image to match target aspect ratio."""
    img_w, img_h = img.size
    target_ratio = target_w / target_h
    img_ratio = img_w / img_h

    if img_ratio > target_ratio:
        new_w = int(img_h * target_ratio)
        left = (img_w - new_w) // 2
        img = img.crop((left, 0, left + new_w, img_h))
    elif img_ratio < target_ratio:
        new_h = int(img_w / target_ratio)
        top = (img_h - new_h) // 2
        img = img.crop((0, top, img_w, top + new_h))

    return img


def _ease_in_out(t: float) -> float:
    """Smooth ease-in-out curve (cubic)."""
    if t < 0.5:
        return 4 * t * t * t
    else:
        return 1 - pow(-2 * t + 2, 3) / 2
