"""Stretches for the automatic shot descriptions."""
import numpy as np

from claudio import describe


def _u(v):
    return v / np.linalg.norm(v)


def test_segments_follow_picture_changes_and_cuts():
    rng = np.random.default_rng(0)
    a, b = _u(rng.normal(0, 1, 512)), _u(rng.normal(0, 1, 512))
    times = [0, 2, 4, 6, 8, 10, 12, 14]
    emb = np.array([a, a, a, b, b, b, b, b], np.float32)              # the picture changes at 6 s
    segs = describe._segments(times, emb, 16.0, cuts=[11.0])            # and the index saw a cut at 11 s
    assert segs[0] == (0.0, 6) and segs[1][0] == 6 and any(abs(s[0] - 12) < 1e-6 for s in segs)


def test_long_static_shot_is_split():
    v = _u(np.ones(512))
    times = list(range(0, 40, 2))
    segs = describe._segments(times, np.array([v] * len(times), np.float32), 40.0, cuts=[])
    assert all(b - a <= describe.MAX_LEN + 2 for a, b in segs) and len(segs) >= 3
