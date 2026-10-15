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
