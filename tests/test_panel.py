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
        pg.evaluate("""()=>showImage('image/png','iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=')""")
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
    assert pg.evaluate("document.querySelectorAll('#log script, #log .msg:not(.shot) img').length") == 0
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


@needs_browser
def test_tool_images_shown(page):
    pg, _ = page
    assert pg.evaluate("document.querySelectorAll('#log .msg.shot img').length") == 1


@needs_browser
def test_local_buttons_and_model(page):
    pg, _ = page
    loc = pg.evaluate("[...document.querySelectorAll('#quick button.loc')].map(b=>b.dataset.q)")
    assert set(loc) == {'index', 'qc', 'stereo', 'flatten', 'status', 'multicam', 'describe', 'clean', 'sfx', 'captions_mogrt', 'stt'}
    # music / montar: a local picker first (sequences / recipes), then the AI — not marked as "sem IA"
    assert pg.evaluate("Object.keys(LOCAL).sort().join(',')") == 'captions_mogrt,clean,describe,flatten,index,montar,multicam,music,qc,sfx,status,stereo,stt'
    ai = {q for q in pg.evaluate("[...document.querySelectorAll('#quick button:not(.loc)')].map(b=>b.dataset.q)") if not q.startswith('recipe:')}
    assert ai <= set(pg.evaluate("Object.keys(QUICK)")) | {'music', 'montar'}
    assert pg.evaluate("document.querySelector('#model').value") == 'sonnet'
    pg.evaluate("add('local','**3 clipe(s)** com áudio de um lado só',false)")
    assert 'sem IA' in pg.evaluate("[...document.querySelectorAll('.msg.local .hd b')].pop().textContent")


@needs_browser
def test_scribe_menu_marks_the_captioned_whisper_sequences(page):
    """Button 14: sequences with speech listed as they are now; captioned ones still on Whisper come checked."""
    pg, _ = page
    pg.evaluate("""()=>sttMenu([{id:'1',name:'INC - 01 Robson (limpo)'},{id:'2',name:'INC - 01 Robson (bruto)'},{id:'3',name:'padrão'}],
        {'1':['E:\\\\C0008.MP4'],'2':['E:\\\\C0008.MP4','E:\\\\C0011.MP4'],'3':[]},
        {stt:'whisper',captioned:{'INC - 01 Robson (limpo)':21},clips:{'E:\\\\C0008.MP4':{speech:true,engine:'whisper',duration:38},
         'E:\\\\C0011.MP4':{speech:true,engine:'elevenlabs',duration:15}}})""")
    rows = pg.evaluate("[...document.querySelectorAll('.msg.local label')].map(l=>[l.textContent.trim(), l.querySelector('input').checked])")
    assert rows == [['INC - 01 Robson (limpo) — 1 clipe(s) com fala, 1 pelo Whisper · legendada (21 blocos)', True],
                    ['INC - 01 Robson (bruto) — 2 clipe(s) com fala, 1 pelo Whisper', False]]
    btns = pg.evaluate("[...document.querySelectorAll('.msg.local')].pop().querySelectorAll('button').length")
    assert btns == 2
    assert '(1 clipe(s), ~0.6 min de áudio)' in pg.evaluate("[...document.querySelectorAll('.msg.local button.primary')].pop().textContent")


@needs_browser
def test_messages_use_the_premiere_bridge_when_it_is_up(page):
    """With the bridge up, every message talks to the one Premiere MCP that holds the UXP port (HTTP), not a new copy."""
    pg, _ = page
    cfg = pg.evaluate("""()=>{const fs=require('fs'), r=fs.readFileSync, w=fs.writeFileSync; let out=null;
        fs.readFileSync=()=>JSON.stringify({mcpServers:{claudio:{command:'wsl.exe'},'premiere-pro':{command:'node.exe',env:{PREMIERE_UXP_TOKEN:'x'}}}});
        fs.writeFileSync=(f,t)=>{out=JSON.parse(t).mcpServers};
        os.tmpdir=()=>'C:\\tmp'; const was=bridgeUp; connectorsOn=false;
        bridgeUp=true; mcpArgs('monta a abertura'); const up=out['premiere-pro'];
        bridgeUp=false; mcpArgs('monta a abertura'); const down=out['premiere-pro'];
        bridgeUp=was; fs.readFileSync=r; fs.writeFileSync=w; return [up, down];}""")
    assert cfg[0] == {'type': 'http', 'url': 'http://127.0.0.1:7788/mcp'}
    assert cfg[1]['command'] == 'node.exe'                   # no bridge: the per-message copy, as before


@needs_browser
def test_context_carries_xml_and_tracks(page):
    """Claude reads the sequence from the XML the panel already exported, instead of asking the Premiere MCP."""
    pg, _ = page
    ctx = pg.evaluate("""()=>contextBlock({tracks:[{track:'V1',clips:[1,2]},{track:'V2',clips:[]},{track:'A1',clips:[1]}]},
        'E:\\\\job\\\\claudio_backups\\\\seq.xml')""")
    assert 'XML da sequência ativa, exportado agora pelo painel: E:\\job\\claudio_backups\\seq.xml' in ctx
    assert 'V1: 2 · V2: vazia | A1: 1' in ctx
    assert 'XML da sequência' not in pg.evaluate('()=>contextBlock(null, null)')


@needs_browser
def test_connectors_toggle(page):
    """Vault·Stock off: only claudio + Premiere load unless the message asks; on: the full setup every time."""
    pg, _ = page
    pg.evaluate("()=>{connectorsOn=false;document.querySelector('#conn').setAttribute('aria-pressed','false')}")
    assert pg.evaluate("()=>mcpArgs('salva isso no vault')") == []
    pg.click('#conn')
    assert pg.get_attribute('#conn', 'aria-pressed') == 'true'
    assert pg.evaluate("()=>mcpArgs('monta a abertura')") == []
    pg.click('#conn')
    assert pg.get_attribute('#conn', 'aria-pressed') == 'false'


@needs_browser
def test_multicam_tracks_reach_claude_as_json(page):
    """Multicam clips come out of FCP XML empty: the panel saves its own full read (+ the multicam source sequence)
    next to the backup and the context points Claude to it."""
    pg, _ = page
    r = pg.evaluate("""async ()=>{
      const written = {};
      const fs0 = require('fs').writeFileSync;
      require('fs').writeFileSync = (p, d) => { written[p] = d; };
      const eh = window.__adobe_cep__.evalScript;
      window.__adobe_cep__.evalScript = (s, cb) => {
        if (s.startsWith('claudioSeqIdByName')) return cb('src-1');
        if (s.startsWith('claudioSnapshot("src-1")')) return cb(JSON.stringify({ok:true,id:'src-1',name:'MC fonte',tracks:[
          {track:'V1',clips:[{name:'CAM A',start:0,end:200,in:0,out:200,media:'E:\\\\v\\\\A.mp4'}]},
          {track:'A1',clips:[{name:'musica.wav',start:0,end:200,in:0,out:200,media:'E:\\\\m\\\\musica.wav'}]}]}));
        return eh(s, cb);
      };
      const snap = {ok:true,id:'s1',name:'multicam valendo',tracks:[
        {track:'V1',clips:[{name:'MC fonte',start:0,end:4.2,in:10,out:14.2,media:'',multicam:true,source:'MC fonte'}]},
        {track:'V2',clips:[]}]};
      await saveTracks(snap, 'E:\\\\job\\\\claudio_backups\\\\seq.xml');
      window.__adobe_cep__.evalScript = eh; require('fs').writeFileSync = fs0;
      return {json: snap.json, data: JSON.parse(written[snap.json]), ctx: contextBlock(snap, 'E:\\\\job\\\\claudio_backups\\\\seq.xml')};
    }""")
    assert r['json'] == 'E:\\job\\claudio_backups\\seq — trilhas.json'
    assert r['data']['multicam_sources']['MC fonte']['tracks'][1]['clips'][0]['media'].endswith('musica.wav')
    assert 'Trilhas COMPLETAS' in r['ctx'] and 'MULTICAM' in r['ctx'] and r['json'] in r['ctx']


@needs_browser
def test_prompts_use_the_profile_folders(page):
    """The panel spells no folder: neutral defaults until claudio-run answers, then the editor's perfil.json."""
    pg, _ = page
    r = pg.evaluate("""async()=>{await readContext(); const saved=PROF; const out={job: jobFolder(), neutral: QUICK.captions_mogrt()};
      PROF={...PROF, pastas:{...PROF.pastas, legendas:'#legendas', projeto:'#projeto'}};
      out.mine=QUICK.captions_mogrt(); out.sync=QUICK.multicam(); PROF=saved; return out;}""")
    assert r['job'] == 'E:\\job'                                  # <job>/#projeto/x.prproj → <job>
    assert 'E:\\job\\legendas' in r['neutral'] and '#legendas' not in r['neutral']
    assert 'E:\\job\\#legendas' in r['mine']
    assert 'E:\\job\\#projeto\\claudio_sync.xml' in r['sync']


@needs_browser
def test_montar_buttons_come_from_the_recipes(page):
    """MONTAR: "Montar com receita" opens the list in the chat; recipes with a "botao" become shortcut buttons, and the
    numbers follow the order on screen (no WSL here: the built-in fallback is the recap)."""
    pg, _ = page
    r = pg.evaluate("""async()=>{
      const nums=()=>[...document.querySelectorAll('#quick button')].map(b=>b.querySelector('i').textContent+' '+b.querySelector('span').textContent);
      const before=nums();
      renderRecipeButtons([{nome:'recap-evento',titulo:'Aftermovie',quando:'evento',botao:{rotulo:'Recap de evento'}},
                           {nome:'podcast',titulo:'Podcast em vídeo',quando:'conversa',botao:{rotulo:'Podcast'}},
                           {nome:'depoimento',titulo:'Depoimento',quando:'fala'}]);
      const after=nums(); await readContext();
      const prompt=QUICK.recipe({nome:'podcast',titulo:'Podcast em vídeo'});
      renderRecipeButtons(RECIPES);
      return {before, after, prompt};}""")
    assert r['before'][7:11] == ['08 Montar com receita', '09 Selects', '10 Corte por quem fala', '11 Recap de evento']
    assert r['before'][-1] == '21 Registrar no vault'
    assert r['after'][10:12] == ['11 Recap de evento', '12 Podcast'] and r['after'][-1] == '22 Registrar no vault'
    assert 'recipe("podcast")' in r['prompt'] and 'E:\\job\\projeto' in r['prompt']
    pg.click('#quick button[data-q="montar"]')
    pg.wait_for_function("document.querySelectorAll('input[name=claudio-recipe]').length > 0")
    assert pg.evaluate("document.querySelectorAll('input[name=claudio-recipe]').length") == 1     # fallback list
