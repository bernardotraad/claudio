"""The Premiere panel, loaded in Chromium with Premiere mocked: layout holds at narrow widths, messages never overlap,
Markdown from a reply can't inject HTML, and the change detector flags edits to clips that were already there."""
import pathlib

import pytest

from conftest import needs_browser

PANEL = pathlib.Path(__file__).resolve().parents[1] / 'src' / 'claudio' / 'panel_premiere'

MOCK = r"""<script>
const _path={basename:(p,e)=>{let b=String(p).split(/[\\/]/).pop();return e&&b.endsWith(e)?b.slice(0,-e.length):b;},
  extname:p=>{const m=String(p).match(/\.[^.\\/]+$/);return m?m[0]:'';},dirname:p=>String(p).replace(/[\\/][^\\/]*$/,''),
  join:(...a)=>a.join('\\'),delimiter:';'};
const _fs={existsSync:()=>false,readdirSync:()=>[],readFileSync:()=>'{}',writeFileSync:()=>{},renameSync:()=>{},mkdirSync:()=>{},statSync:()=>({mtimeMs:0}),unlinkSync:()=>{}};
window.require=m=>({path:_path,fs:_fs,os:{homedir:()=>'C:\\Users\\x'},child_process:{}})[m];
window.process={platform:'win32',env:{PATH:''}};
window.__adobe_cep__={evalScript:(s,cb)=>cb(JSON.stringify({project:'E:\\job\\#projeto\\um projeto com nome bem comprido.prproj',
  sequence:'Synced Sequence com nome comprido',frame_size:'3840x2160',playhead_s:3725.4,selection:[]}))};
</script>"""

REPLY = """Achei **2 câmeras**. <script>window.HACKED=1</script><img src=x onerror="window.HACKED=1">

| câmera | pasta | cor |
|---|---|---|
| CAM A — A7III | `E:\\\\Fotos\\\\2026\\\\uma pasta bem comprida\\\\01 HRCS` | Cerulean |

1. Drift do Zoom: **−11,6 ppm**, em `ZOOM_drift_corrected+0.079.wav`
2. Conferi 3 momentos."""


@pytest.fixture(scope='module')
def page(tmp_path_factory):
    from playwright.sync_api import sync_playwright
    d = tmp_path_factory.mktemp('panel')
    html = (PANEL / 'index.html').read_text(encoding='utf-8').replace('<script src="main.js"></script>', MOCK + '<script src="main.js"></script>')
    (d / 'index.html').write_text(html, encoding='utf-8')
    (d / 'main.js').write_text((PANEL / 'main.js').read_text(encoding='utf-8'), encoding='utf-8')
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={'width': 300, 'height': 640})
        errs = []
        pg.on('pageerror', lambda e: errs.append(str(e)))
        pg.goto((d / 'index.html').as_uri())
        pg.wait_for_timeout(400)
        pg.evaluate("""(r)=>{for(let k=0;k<3;k++){add('user','Multicam do show, por favor. O Zoom tá na pasta AUDIO.',false);
            add('tool','claudio › library',false);add('tool','claudio › index_status',false);add('claude',r,false);}}""", REPLY)
        pg.wait_for_timeout(200)
        yield pg, errs
        b.close()


@needs_browser
def test_no_js_errors(page):
    assert page[1] == []


@needs_browser
def test_reply_html_is_escaped(page):
    pg, _ = page
    assert pg.evaluate('window.HACKED') is None
    assert pg.evaluate("document.querySelectorAll('#log script, #log img').length") == 0
    assert pg.evaluate("document.querySelectorAll('#log table').length") == 3


@needs_browser
@pytest.mark.parametrize('width', [300, 380])
def test_layout_holds(page, width):
    pg, _ = page
    pg.set_viewport_size({'width': width, 'height': 640})
    pg.wait_for_timeout(100)
    gaps = pg.evaluate("""()=>{const m=[...document.querySelectorAll('#log > .msg')];const g=[];
        for(let i=1;i<m.length;i++) g.push(m[i].getBoundingClientRect().top-m[i-1].getBoundingClientRect().bottom); return g}""")
    assert min(gaps) >= 0, gaps                                            # no message drawn over another
    assert pg.evaluate('document.documentElement.scrollWidth') <= width     # nothing pushes the panel sideways
    heights = pg.evaluate("[...document.querySelectorAll('.msg.tool')].map(t=>t.getBoundingClientRect().height)")
    assert min(heights) > 10                                                # tool lines are not squeezed to 0


@needs_browser
def test_change_detector(page):
    pg, _ = page
    r = pg.evaluate("""()=>{
      const b={tracks:[{track:'V1',clips:[{name:'A',start:0,end:5,in:1,out:6}]},{track:'V2',clips:[{name:'Adjustment Layer',start:0,end:564,in:0,out:564}]},{track:'V3',clips:[]}]};
      const newTrack={tracks:[b.tracks[0],b.tracks[1],{track:'V3',clips:[{name:'legenda.mov',start:0,end:250,in:0,out:250}]}]};
      const over={tracks:[b.tracks[0],{track:'V2',clips:[{name:'legenda.mov',start:0,end:250,in:0,out:250},{name:'Adjustment Layer',start:250,end:564,in:250,out:564}]}]};
      return [changedClips(b,newTrack).length, changedClips(b,over)]}""")
    assert r[0] == 0 and len(r[1]) == 1 and 'Adjustment Layer' in r[1][0]
