"""Overlays render with alpha inside the editor's #projeto folder, fast."""
import json, subprocess

from conftest import needs_browser, needs_ffmpeg
from claudio import motion

CODE = """const DUR=4;
const t1=div(stage,'title','Teste #projeto');
function seek(t){ t1.style.opacity = t<1 ? 0 : (t<2 ? 1 : 0); }"""


@needs_ffmpeg
@needs_browser
def test_render_in_hash_folder(tmp_path):
    out = tmp_path / "ANR N' ROLL" / '#projeto' / 'claudio_overlays'
    r = motion.render_overlay(CODE, 'teste', str(out), ar='16x9', fps='24')
    assert r['frames'] == 96 and 'PNG' in r['alpha']
    mov = out / 'teste.mov'
    info = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_name,pix_fmt,nb_frames',
                                      '-of', 'json', str(mov)], capture_output=True, text=True).stdout)['streams'][0]
    assert info['codec_name'] == 'png' and info['pix_fmt'] == 'rgba' and int(info['nb_frames']) == 96
    assert mov.stat().st_size < 3_000_000          # three distinct pictures, the rest are repeats


def test_codecs_listed():
    assert set(motion.CODECS) == {'png', 'prores'}
