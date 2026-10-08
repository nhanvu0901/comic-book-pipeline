import numpy as np
import pytest
from PIL import Image

from stages.stage_5.pipeline import _shift_up, _shift_from_below
from stages.stage_5.shots import OUTPUT_W, OUTPUT_H


def _create_vertical_gradient_image() -> Image.Image:
    """Create an image with a vertical gradient so every row has distinct colors."""
    # Gradient along height: row y has color (y % 256, (y * 2) % 256, (y * 3) % 256)
    arr = np.zeros((OUTPUT_H, OUTPUT_W, 3), dtype=np.uint8)
    for y in range(OUTPUT_H):
        arr[y, :, 0] = y % 256
        arr[y, :, 1] = (y * 2) % 256
        arr[y, :, 2] = (y * 3) % 256
    return Image.fromarray(arr, mode="RGB")


def test_shift_up_mirrors_edge_band_without_stretching():
    img = _create_vertical_gradient_image()
    px = 40
    shifted = _shift_up(img, px)
    arr = np.array(shifted)

    # In shifted image:
    # rows [0, OUTPUT_H - px) contain img[px:OUTPUT_H]
    # rows [OUTPUT_H - px, OUTPUT_H) contain the mirrored gap
    gap = arr[OUTPUT_H - px : OUTPUT_H, :, :]

    # 1. Under stretching, every row in the gap was identical (variance along axis 0 is 0).
    # Under mirroring, rows must NOT be identical; row-to-row variance along y must be > 0.
    row_variance = np.var(gap[:, 0, 0].astype(float))
    assert row_variance > 0, "Gap must not be a single stretched row (zero variance along y)"

    # 2. Verify exact mirroring:
    # Row OUTPUT_H - px of canvas should mirror row OUTPUT_H - 1 of img
    # Row OUTPUT_H - px + k of canvas should mirror row OUTPUT_H - 1 - k of img
    orig_arr = np.array(img)
    for k in range(px):
        canvas_y = OUTPUT_H - px + k
        expected_orig_y = OUTPUT_H - 1 - k
        np.testing.assert_array_equal(
            arr[canvas_y, :, :],
            orig_arr[expected_orig_y, :, :],
            err_msg=f"Row {canvas_y} must mirror original row {expected_orig_y}",
        )


def test_shift_from_below_mirrors_edge_band_without_stretching():
    img = _create_vertical_gradient_image()
    px = 40
    shifted = _shift_from_below(img, px)
    arr = np.array(shifted)

    # In shifted image:
    # rows [0, px) contain the mirrored gap
    # rows [px, OUTPUT_H) contain img[0:OUTPUT_H - px]
    gap = arr[0:px, :, :]

    # 1. Row-to-row variance along y must be > 0 (not stretched 1 row)
    row_variance = np.var(gap[:, 0, 0].astype(float))
    assert row_variance > 0, "Gap must not be a single stretched row (zero variance along y)"

    # 2. Verify exact mirroring:
    # Row px - 1 of canvas should mirror row 0 of img
    # Row px - 1 - k of canvas should mirror row k of img
    orig_arr = np.array(img)
    for k in range(px):
        canvas_y = px - 1 - k
        expected_orig_y = k
        np.testing.assert_array_equal(
            arr[canvas_y, :, :],
            orig_arr[expected_orig_y, :, :],
            err_msg=f"Row {canvas_y} must mirror original row {expected_orig_y}",
        )


def test_shift_zero_pixels():
    img = _create_vertical_gradient_image()
    shifted_up = _shift_up(img, 0)
    shifted_below = _shift_from_below(img, 0)

    np.testing.assert_array_equal(np.array(shifted_up), np.array(img))
    np.testing.assert_array_equal(np.array(shifted_below), np.array(img))
