"""The Basic Motion centre is written in Premiere's units: the picture's offset in sequence pixels divided by the file's
own width/height (±2 px of slack: crop_box rounds the crop to even pixels). Numbers measured on Premiere 2026-10-30 (export_frame + Motion › Position after importing Cláudio's
XML): with the old "±1 = half frame" a reframed b-roll landed entirely off screen."""
import pytest

from claudio import montage


def _center(sw, sh, rot, aspect, x, y, seq):
    ow, oh = (sh, sw) if rot in (90, 270) else (sw, sh)
    cx, cy, cw, ch = montage.crop_box(ow, oh, aspect, x, y, 1.0)
    W, H = seq
    scale = max(W / cw, H / ch) * 100
    return scale, montage.fcp_center(ow, oh, cx, cy, cw, ch, scale, {'width': sw, 'height': sh})


@pytest.mark.parametrize('x, horiz', [(0.0, 0.6169), (1.0, -0.6151), (0.5, 0.0)])
def test_broll_in_a_vertical_sequence(x, horiz):
    # 1928×1072 render in 9:16: Premiere put x=0 with the picture's left edge on the frame's left edge
    scale, (h, v) = _center(1928, 1072, 0, '9:16', x, 0.5, (1080, 1920))
    assert round(scale, 1) == 179.4 and h == pytest.approx(horiz, abs=1e-3) and v == pytest.approx(0, abs=1e-3)
    left = 1080 / 2 + h * 1928 - 1928 * scale / 100 / 2       # where Premiere draws it (Position = centre × file width)
    assert left == pytest.approx({0.0: 0, 1.0: 1080 - 1928 * scale / 100, 0.5: (1080 - 1928 * scale / 100) / 2}[x], abs=4)


@pytest.mark.parametrize('y, vert', [(0.0, 0.5408), (1.0, -0.5400)])
def test_sideways_camera_in_a_horizontal_sequence(y, vert):
    # Sony shot on its side (3840×2160 file, rotation 90): Premiere multiplies vert by the FILE's height (2160)
    scale, (h, v) = _center(3840, 2160, 90, '16:9', 0.5, y, (1920, 1080))
    assert round(scale, 2) == 88.96 and v == pytest.approx(vert, abs=1e-3) and h == pytest.approx(0, abs=1e-3)
