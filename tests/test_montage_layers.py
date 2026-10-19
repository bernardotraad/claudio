"""INC-style timeline: b-roll on separate layers with an empty one kept for adjustment layers, a logo still."""
import xml.etree.ElementTree as ET

from PIL import Image

from conftest import ff, needs_ffmpeg
from claudio import montage


@needs_ffmpeg
def test_layers_and_still(tmp_path):
    a, b = tmp_path / 'fala.mp4', tmp_path / 'render.mp4'
    for p in (a, b):
        ff('-f', 'lavfi', '-i', 'testsrc2=s=320x568:r=30:d=8', '-f', 'lavfi', '-i', 'sine=f=300:d=8', '-shortest',
           '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac', str(p))
    logo = tmp_path / 'logo.png'
    Image.new('RGBA', (200, 80), (255, 255, 255, 255)).save(logo)
    edit = {'aspect': '9:16', 'cut_on': 'none', 'name': 'inc teste',
            'shots': [{'path': str(a), 'in': 0, 'out': 6, 'audio': 'dialogue'}],
            'overlays': [{'from': 1.5, 'to': 3.0, 'layer': 2, 'clips': [{'path': str(b), 'in': 0, 'out': 2}]},
                         {'from': 3.5, 'to': 4.5, 'layer': 4, 'clips': [{'path': str(b), 'in': 3, 'out': 5}]}],
            'stills': [{'path': str(logo), 'from': 0, 'to': 6, 'layer': 5}]}
    fit = montage.fit_edit(edit)
    out = tmp_path / 'e.xml'
    montage.export_edit_fcp7(fit, str(out))
    v = ET.parse(out).getroot().findall('.//video/track')
    names = [[c.findtext('name') for c in t.findall('clipitem')] for t in v]
    assert len(v) == 5 and names[2] == [] and names[4] == ['logo.png']      # V3 kept empty for adjustment layers
    assert names[1] == ['render.mp4'] and names[3] == ['render.mp4']
