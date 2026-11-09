import xml.etree.ElementTree as ET
from collections import Counter

from conftest import needs_ffmpeg
from claudio import multicam


@needs_ffmpeg
def test_sync_offsets_and_labels(multicam_shoot, tmp_path):
    root = multicam_shoot
    res = multicam.sync_cameras({'CAM A': str(root / 'A'), 'CAM B': str(root / 'B')}, [str(root / 'SOM')], None, False)
    t0 = {n['cam']: n['t0'] for n in res['nodes']}
    assert abs(t0['CAM A'] - 2.0) < 1 / 25 and abs(t0['CAM B'] - 6.0) < 1 / 25 and abs(t0['AUDIO']) < 1 / 25
    x = multicam.export_xml(res, str(tmp_path / 'sync.xml'), labels={'cam b': 'rose'})
    assert x['labels'] == {'CAM A': 'Cerulean', 'CAM B': 'Rose', 'AUDIO': 'Caribbean'}
    r = ET.parse(tmp_path / 'sync.xml').getroot()
    lab = Counter((ci.findtext('name'), ci.findtext('labels/label2')) for ci in r.iter('clipitem') if ci.find('labels') is not None)
    assert lab[('A001.mp4', 'Cerulean')] == 3 and lab[('B001.mp4', 'Rose')] == 3 and lab[('ZOOM.wav', 'Caribbean')] == 2


def test_camera_labels_palette():
    c = multicam.camera_labels(['A', 'B', 'AUDIO', 'C'], {'c': 'yellow', 'b': 'not-a-colour'})
    assert c['C'] == 'Yellow' and c['AUDIO'] == 'Caribbean' and len(set(c.values())) == 4


@needs_ffmpeg
def test_music_video_takes_against_guide(tmp_path):
    """A guide track whose chorus repeats; takes play parts of it from a speaker (with room noise): each lands where it
    plays in the song, the chorus-only take is flagged with the other place it fits, overlapping takes get own tracks."""
    import numpy as np, subprocess
    from conftest import ff
    sr = 48000
    rng = np.random.default_rng(7)
    seg = lambda s: rng.standard_normal(int(s * sr)) * np.repeat(rng.uniform(0.05, 0.6, int(s * 8)), sr // 8)[:int(s * sr)]
    intro, verse, chorus, verse2 = seg(8), seg(14), seg(10), seg(14)
    song = np.concatenate([intro, verse, chorus, verse2, chorus]).astype(np.float32) * 0.3     # chorus at 22 s and 46 s
    (tmp_path / 'AUDIO').mkdir(); (tmp_path / 'VIDEOS').mkdir()
    guide = tmp_path / 'AUDIO' / 'guia.mpeg'
    raw = tmp_path / 'song.f32'
    song.tofile(raw)
    ff('-f', 'f32le', '-ar', str(sr), '-ac', '1', '-i', str(raw), '-c:a', 'mp2', '-f', 'mpeg', str(guide))
    def take(name, a, b):
        x = song[int(a * sr):int(b * sr)] + 0.02 * rng.standard_normal(int((b - a) * sr)).astype(np.float32)
        p = tmp_path / f'{name}.f32'; x.astype(np.float32).tofile(p)
        ff('-f', 'lavfi', '-i', f'testsrc2=s=160x90:r=25:d={b - a}', '-f', 'f32le', '-ar', str(sr), '-ac', '1', '-i', str(p),
           '-shortest', '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac', str(tmp_path / 'VIDEOS' / f'{name}.mp4'))
    take('T1_verso', 10, 28)        # verse + start of chorus: unique
    take('T2_refrao', 47, 55)       # inside the 2nd chorus only: also fits at 23 s
    take('T3_inteira', 0, 56)
    res = multicam.sync_cameras({'CAM': str(tmp_path / 'VIDEOS')}, [str(tmp_path / 'AUDIO')], None, False, music=True)
    t0 = {n['name']: n['t0'] for n in res['nodes']}
    alt = {n['name']: n.get('alternatives') for n in res['nodes']}
    assert abs(t0['T1_verso.mp4'] - 10) < 0.1 and abs(t0['T3_inteira.mp4'] - 0) < 0.1
    assert min(abs(t0['T2_refrao.mp4'] - 47), abs(t0['T2_refrao.mp4'] - 23)) < 0.1      # one of the two choruses…
    assert alt['T2_refrao.mp4'] and any(abs(a - (23 if abs(t0['T2_refrao.mp4'] - 47) < 1 else 47)) < 0.2 for a in alt['T2_refrao.mp4'])  # …and the other is offered
    x = multicam.export_xml(res, str(tmp_path / 'sync.xml'))
    # stacked only where takes overlap: the full take on its own track; verse and chorus share one unless both sit at ~23 s
    assert x['tracks_video'] == (3 if abs(t0['T2_refrao.mp4'] - 23) < 1 else 2)


@needs_ffmpeg
def test_show_cameras_in_sequence_then_songs(tmp_path):
    """A show, the way PluralEyes syncs it: three cameras each recorded several clips one after the other (their
    clocks good to ~1 s, each in its own time zone), different mics; the board recorded one file per song. Clips land
    in recording order to a fraction of a frame, then each song is found in the cameras' sound."""
    import datetime
    import numpy as np
    from conftest import ff
    sr, dur = 48000, 200
    rng = np.random.default_rng(3)
    t = np.arange(int(dur * sr)) / sr
    show = np.zeros_like(t, dtype=np.float32)
    pos = 0.0
    while pos < dur:                                                # chords of uneven length, drum hits off the grid
        step = rng.uniform(0.3, 0.9)
        a, b = int(pos * sr), min(len(t), int((pos + step) * sr))
        for f in 110 * 2 ** (rng.integers(0, 24, 3) / 12):
            show[a:b] += 0.15 * np.sin(2 * np.pi * f * t[a:b])
        for h in pos + np.sort(rng.uniform(0, step, rng.integers(1, 3))):
            i = int(h * sr)
            hit = rng.standard_normal(int(0.04 * sr)) * np.exp(-np.arange(int(0.04 * sr)) / (0.008 * sr))
            show[i:i + len(hit)] += 0.6 * hit[:len(show) - i]
        pos += step
    show *= np.repeat(rng.uniform(0.3, 1.0, dur // 10 + 1), 10 * sr)[:len(show)]      # louder and quieter songs

    def mic(x, k):                                                  # every camera hears it through its own mic
        y = np.convolve(x, np.ones(k) / k, 'same') + 0.03 * rng.standard_normal(len(x))
        return y.astype(np.float32)

    def clip(path, x, clock):
        raw = tmp_path / (path.stem + '.f32'); x.tofile(raw)
        ct = datetime.datetime.fromtimestamp(clock, datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000000Z')
        ff('-f', 'lavfi', '-i', f'testsrc2=s=160x90:r=25:d={len(x) / sr:.3f}', '-f', 'f32le', '-ar', str(sr), '-ac', '1',
           '-i', str(raw), '-shortest', '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac',
           '-metadata', f'creation_time={ct}', str(path))

    truth = {}
    cams = {'A': ([(3.0, 70.0), (72.4, 150.0), (151.7, 196.0)], 1, 1_700_000_000),
            'B': ([(8.2, 95.0), (96.5, 190.0)], 9, 1_700_090_000),
            'C': ([(0.0, 120.0), (121.3, 199.0)], 25, 1_699_990_000)}
    for cam, (segs, k, base) in cams.items():
        (tmp_path / cam).mkdir()
        heard = mic(show, k)
        for i, (a, b) in enumerate(segs):
            p = tmp_path / cam / f'{cam}{i + 1:03d}.mp4'
            clip(p, heard[int(a * sr):int(b * sr)], base + int(a))   # the clock only has whole seconds
            truth[p.name] = a
    (tmp_path / 'MESA').mkdir()
    for name, (a, b) in {'01 - abertura.wav': (20.0, 60.0), '02 - final.wav': (130.0, 185.0)}.items():
        raw = tmp_path / 'm.f32'; show[int(a * sr):int(b * sr)].tofile(raw)
        ff('-f', 'f32le', '-ar', str(sr), '-ac', '1', '-i', str(raw), str(tmp_path / 'MESA' / name))
        truth[name] = a
    res = multicam.sync_cameras({c: str(tmp_path / c) for c in cams}, [str(tmp_path / 'MESA')], '25', False)
    t0 = {n['name']: n['t0'] for n in res['nodes']}
    assert not res['unplaced']
    shift = t0['C001.mp4'] - truth['C001.mp4']
    for name, a in truth.items():
        assert abs(t0[name] - shift - a) < 0.02, (name, t0[name] - shift, a)       # half a frame at 25 fps
    assert all('sequência' in n['method'] for n in res['nodes'] if n['kind'] == 'video')
