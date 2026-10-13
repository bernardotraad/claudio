"""The beat grid lands on the beat: no drift against the real tempo and no constant lag."""
import numpy as np
import pytest

from conftest import ff, needs_ffmpeg
from claudio import montage


@needs_ffmpeg
@pytest.mark.parametrize('bpm,expr', [
    (128, "if(lt(mod(t\\,60/128)\\,0.002)\\,sin(2*PI*2000*t)\\,0)"),                       # short click
    (128, "if(lt(mod(t\\,60/128)\\,0.03)\\,exp(-mod(t\\,60/128)*120)*(random(0)-0.5)\\,0)"),  # snare-like burst
    (97, "if(lt(mod(t\\,60/97)\\,0.02)\\,sin(2*PI*1000*t)\\,0)"),
])
def test_beat_grid_on_clicks(tmp_path, bpm, expr):
    wav = tmp_path / 'clicks.wav'
    ff('-f', 'lavfi', '-i', f"aevalsrc='{expr}':s=44100:d=20", '-ac', '2', str(wav))
    m = montage.music_map(str(wav))
    b = np.array(m['beats'])
    p = 60 / bpm
    err = (b - np.round(b / p) * p) * 1000
    assert abs(m['bpm'] - bpm) < 0.6
    assert np.median(np.abs(err)) < 6 and np.abs(err).max() < 15, err.round(1)
