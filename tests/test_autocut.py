"""Multicam cut by who is speaking, from each person's mic (with the other one bleeding in)."""
import wave

import numpy as np

from conftest import needs_ffmpeg
from claudio import autocut, nle_read

R25 = '<rate><timebase>25</timebase><ntsc>FALSE</ntsc></rate>'
SR = 16000
DUR = 36


def _talk(spans, seed):
    rng = np.random.default_rng(seed)
    x = np.zeros(SR * DUR, np.float32)
    for a, b in spans:
        t = np.arange(int((b - a) * SR)) / SR
        syll = (0.55 + 0.45 * np.sin(2 * np.pi * 4 * t)) * (0.3 * rng.standard_normal(len(t)))   # speech-ish 4 Hz bursts
        x[int(a * SR):int(a * SR) + len(t)] += syll
    return x


def _wav(p, x):
    with wave.open(str(p), 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())


def _xml(tmp_path):
    ana = [(0.5, 6), (10, 12), (16, 34)]          # 16–34: one long monologue → a cutaway
    rob = [(6.2, 10), (10.3, 12)]                 # 10–12 both at once → wide
    a, r = _talk(ana, 1), _talk(rob, 2)
    _wav(tmp_path / 'ana.wav', a + 0.12 * r + 0.002 * np.random.default_rng(3).standard_normal(len(a)))
    _wav(tmp_path / 'rob.wav', r + 0.12 * a + 0.002 * np.random.default_rng(4).standard_normal(len(a)))
    url = lambda n: f'file://{tmp_path / n}'
    fe = lambda fid, n, v: (f'<file id="{fid}"><name>{n}</name><pathurl>{url(n)}</pathurl>{R25}<duration>{DUR * 25}</duration>'
                            f'<media>{"<video/>" if v else ""}<audio/></media></file>')
    c = lambda cid, fid, n, v: (f'<clipitem id="{cid}"><name>{n}</name>{R25}<start>0</start><end>{DUR * 25}</end><in>0</in>'
                                f'<out>{DUR * 25}</out>{fe(fid, n, v)}</clipitem>')
    x = (f'<?xml version="1.0"?><xmeml version="4"><sequence id="s"><name>Podcast</name>{R25}<duration>{DUR * 25}</duration><media>'
         f'<video><track>{c("va", "f1", "camA.mp4", True)}</track><track>{c("vb", "f2", "camB.mp4", True)}</track>'
         f'<track>{c("vw", "f3", "wide.mp4", True)}</track></video>'
         f'<audio><track>{c("ma", "f4", "ana.wav", False)}</track><track>{c("mr", "f5", "rob.wav", False)}</track></audio>'
         '</media></sequence></xmeml>')
    p = tmp_path / 'Podcast.xml'
    p.write_text(x, encoding='utf-8')
    return p


@needs_ffmpeg
def test_auto_cut_follows_the_speaker(tmp_path):
    r = autocut.auto_cut(str(_xml(tmp_path)), {'V1': ['Ana'], 'V2': ['Robson'], 'V3': []}, {'Ana': 'A1', 'Robson': 'A2'})
    assert 'error' not in r, r
    cam_at = lambda t: next(s['camera'] for s in r['cut_list'] if s['from'] <= t < s['to'])
    assert cam_at(3) == 'V1' and cam_at(8.5) == 'V2'          # each on their own camera, despite the bleed
    assert cam_at(11.5) == 'V3'                                # crosstalk → wide
    assert 'V3' in [cam_at(t) for t in np.arange(26, 33, 0.25)]   # long monologue gets a cutaway…
    assert cam_at(33.5) == 'V1'                                # …and comes back to her
    assert all(s['to'] - s['from'] >= 2.0 - 1e-6 for s in r['cut_list'][:-1])   # never shorter than min_hold
    tl = nle_read.read_fcp7(r['path'])
    top = [i for i in tl['items'] if i['track'] == 'V4']
    assert top and all(i['enabled'] for i in top)
    assert not any(i['enabled'] for i in tl['items'] if i['track'] in ('V1', 'V2', 'V3'))
    assert abs(sum(i['tl_out'] - i['tl_in'] for i in top) - DUR) < 0.1   # the cut covers the whole sequence, no gaps
