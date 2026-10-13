"""Loudness of the mix and ducking from the timeline's clips."""
import numpy as np

from claudio import qc


def test_loudness_of_a_known_tone():
    t = np.arange(qc.MSR_MIX * 10) / qc.MSR_MIX
    x = 0.1 * np.sin(2 * np.pi * 1000 * t)                     # −20 dBFS sine, dual mono ≈ −20 LUFS
    L = qc.loudness(x)
    assert abs(L['lufs'] - (-20.0)) < 1.0 and L['true_peak_db'] < -19


def test_ducking_flags_music_over_speech():
    n = qc.MSR_MIX * 12
    rng = np.random.default_rng(0)
    speech = np.zeros(n, np.float32); speech[qc.MSR_MIX * 2:qc.MSR_MIX * 8] = 0.05 * rng.standard_normal(qc.MSR_MIX * 6)
    music = 0.05 * rng.standard_normal(n).astype(np.float32)   # as loud as the speech: must be lowered
    d = qc.ducking({'speech': speech, 'music': music}, 24)
    assert len(d) == 1 and 1.5 <= d[0]['start'] <= 2.5 and 7.5 <= d[0]['end'] <= 8.5 and d[0]['lower_music_by_db'] >= 10
    quiet = music * 0.01                                       # 40 dB under: fine
    assert qc.ducking({'speech': speech, 'music': quiet}, 24) == []
