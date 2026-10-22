"""Overlays render with alpha inside the editor's #projeto folder, fast."""
import json, subprocess

import pytest

from conftest import needs_browser, needs_ffmpeg
from claudio import motion

CODE = """const DUR=4;
const t1=div(stage,'title','Teste #projeto');
function seek(t){ t1.style.opacity = t<1 ? 0 : (t<2 ? 1 : 0); }"""


STUB_CORE = """<!doctype html><html><head><meta charset="utf-8"><style>:root{--bg:#F7F7F5;}
html,body{margin:0;padding:0;background:var(--bg);overflow:hidden}
#stage{position:relative;width:100vw;height:100vh}.title{position:absolute;left:10%;top:40%;font:700 64px sans-serif;color:#fff}</style>
</head><body><div id="stage"></div><script>
const Q=new URLSearchParams(location.search);const stage=document.getElementById('stage');
function div(p,cls,txt){const e=document.createElement('div');e.className=cls;e.textContent=txt||'';p.appendChild(e);return e;}
// the two lines of the real Captions() that motion._CUE_PATCH hooks into (reviewed .srt cues)
function Captions(p,WORDS){const chunks=[],win=[];
  const next=i=>WORDS[i+1]?WORDS[i+1].s:1e9;
  const els=chunks.map(c=>c.map(i=>{const sp=document.createElement('span');return sp;}));
  return {els,win,next};}
// ================================================================= TIMELINE
"""


@pytest.fixture
def skill(tmp_path, monkeypatch):
    """The real motion-designer skill when installed, else a minimal core with the same contract (CI)."""
    try:
        motion.skill_dir()
        return
    except FileNotFoundError:
        d = tmp_path / 'motion-designer' / 'templates'
        d.mkdir(parents=True)
        (d / 'timeline.html').write_text(STUB_CORE, encoding='utf-8')
        monkeypatch.setenv('CLAUDIO_MOTION_SKILL', str(d.parent))


@needs_ffmpeg
@needs_browser
def test_render_in_hash_folder(tmp_path, skill):
    out = tmp_path / "ANR N' ROLL" / '#projeto' / 'claudio_overlays'
    r = motion.render_overlay(CODE, 'teste', str(out), ar='16x9', fps='24')
    assert r['frames'] == 96 and 'PNG' in r['alpha']
    mov = out / 'teste.mov'
    info = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_name,pix_fmt,nb_frames',
                                      '-of', 'json', str(mov)], capture_output=True, text=True).stdout)['streams'][0]
    assert info['codec_name'] == 'png' and info['pix_fmt'] == 'rgba' and int(info['nb_frames']) == 96
    assert mov.stat().st_size < 3_000_000          # three distinct pictures, the rest are repeats
    import struct
    b = mov.read_bytes()
    e = b.find(b'stsd') + 12
    depth = struct.unpack('>H', b[e + 8 + 6 + 2 + 16 + 4 + 4 + 4 + 4 + 2 + 32:][:2])[0]
    assert b[e + 4:e + 8] == b'png ' and depth == 32   # 24 makes Premiere ignore the alpha (black background)


def test_codecs_listed():
    assert set(motion.CODECS) == {'png', 'prores'}


def test_overlay_follows_the_sequence_resolution():
    from claudio import motion
    assert motion.fit_sequence(None, 3840, 2160) == ('16x9', 2.0)          # 4K UHD: the 1080 layout, twice the pixels
    assert motion.fit_sequence(None, 1920, 1080) == ('16x9', 1.0)
    assert motion.fit_sequence(None, 2160, 3840) == ('9x16', 2.0)          # vertical 4K
    assert motion.fit_sequence('16x9', 3840, 2160) == ('16x9', 2.0)
    assert motion.fit_sequence('9x16', 3840, 2160) == ('9x16', 1)           # another delivery format: its own size
    assert motion.fit_sequence(None, None, None) == ('9x16', 1)


def test_karaoke_is_its_own_look():
    from claudio import motion
    W = [{'w': 'Chorando,', 's': 0.0, 'e': 1.2}, {'w': 'ouvindo', 's': 1.2, 'e': 1.8}]
    k, s = motion.caption_code(W, 'karaoke'), motion.caption_code(W, 'social')
    assert 'clipPath' in k and 'nxt' in k and 'clipPath' not in s      # word fill + next line, not the social pop-in


def test_karaoke_lines_hold_until_the_next():
    from claudio import motion
    W = [{'w': 'a', 's': 0.0, 'e': 1.0}, {'w': 'b', 's': 3.0, 'e': 4.0}]
    code = motion.caption_code(W, 'karaoke', cues=[[0, 0, 0.0, 1.2, 'a'], [1, 1, 3.0, 4.2, 'b']])
    assert code.rstrip().endswith('WIN[k][1]=Math.max(WIN[k][1],WIN[k+1][0]);')           # after the editor's cue times
    assert 'inset(0 100% 0 0)' not in code                                             # fill reaches past the glyph box
