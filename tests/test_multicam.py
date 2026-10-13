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
