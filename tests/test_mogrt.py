"""Caption MOGRTs: the blocks match the overlay's, the AE script and every expression are valid JavaScript."""
import json

import pytest

from conftest import needs_browser
from claudio import mogrt, motion

W = [{'w': 'A', 's': 0.2, 'e': 0.5}, {'w': 'Cidade', 's': 0.6, 'e': 0.9}, {'w': 'Alta', 's': 1.0, 'e': 1.3},
     {'w': 'tem', 's': 1.5, 'e': 1.7}, {'w': 'entrada', 's': 1.8, 'e': 2.2}, {'w': 'de', 's': 2.25, 'e': 2.3},
     {'w': 'R$750.', 's': 2.4, 'e': 3.0}]
LOOK = {'font': 'Gilroy-Medium', 'bg': '#ee3c07', 'color': '#ffffff', 'size': 80, 'y': .578, 'dynamic': True, 'max_words': 3}


def _skill():
    try:
        motion.skill_dir()
    except FileNotFoundError:
        pytest.skip('motion-designer skill not installed')


@needs_browser
@pytest.mark.parametrize('style,look', [('social', None), ('bold', None), ('marca', LOOK)])
def test_blocks_cover_every_word(style, look):
    _skill()
    bl = mogrt.blocks([dict(w) for w in W], style, look)
    assert ' '.join(b['text'] for b in bl) == ' '.join(w['w'] for w in W)
    for b, nb in zip(bl, bl[1:]):
        assert b['end'] <= nb['start'] + 1e-6                    # blocks never overlap on the track
    for b in bl:
        rel, em, d = b['tempos'].split('|')
        assert len(rel.split(',')) == len(b['text'].split()) and abs(float(d) - (b['end'] - b['start'])) < 1e-3
    if style == 'marca':
        assert any(b['tempos'].split('|')[1] for b in bl)        # entrada / R$750 marked as keywords


@needs_browser
def test_blocks_follow_reviewed_srt():
    _skill()
    cues = [[0, 2, 0.2, 1.4], [3, 6, 1.4, 3.2]]
    bl = mogrt.blocks([dict(w) for w in W], 'social', None, cues=cues)
    assert [(b['start'], b['end']) for b in bl] == [(0.199, 1.4), (1.399, 3.2)]
    assert [b['text'] for b in bl] == ['A Cidade Alta', 'tem entrada de R$750.']


@needs_browser
def test_ae_script_and_expressions_compile(tmp_path):
    from playwright.sync_api import sync_playwright
    specs = [(mogrt.template_name(mogrt.style_spec(s, '9x16', look=LOOK), '9x16'), mogrt.style_spec(s, '9x16', look=LOOK))
             for s in list(motion.STYLES)]
    src = mogrt.jsx(specs, str(tmp_path), str(tmp_path / 'r.json'))
    assert 'marca_dinamica' in src and src.count('"name": "Claudio legenda') == len(specs)
    # measured in Premiere 26.5: a protected region squeezes the comp into the clip (captions vanish after a frame)
    assert 'protectedRegion = true' not in src
    # the non-Adobe-Fonts warning would stop the export: the pref is turned off for it and restored
    assert "savePrefAsBool(TK[0], TK[1], false)" in src and "savePrefAsBool(TK[0], TK[1], tkBefore)" in src
    exprs = []
    for _, sp in specs:
        exprs += list(mogrt.expressions(sp).values())
        if sp['box']:
            exprs += list(mogrt.box_expressions(sp).values())
        for k in range(1, (sp['chips'] or {}).get('n', 0) + 1):
            exprs += list(mogrt.chip_expressions(sp, k).values())
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        bad = pg.evaluate("""([js, ex]) => { const out = [];
            try { new Function(js); }                                  // syntax only, never run
            catch (e) { out.push('jsx: ' + e.message); }
            ex.forEach((e, i) => { try { new Function('thisComp', 'thisLayer', 'time', 'textIndex', 'return eval(arguments[4])'); new Function(e); }
                                   catch (er) { out.push(i + ': ' + er.message + ' :: ' + e.slice(-120)); } });
            return out; }""", [src, exprs])
        # evaluate the dynamic brand helpers on a fake comp: words appear in order, keywords marked
        sample = mogrt.expressions(dict(specs[-1][1]))['legenda_text']
        vis = pg.evaluate("""(e) => { const mk = (s) => ({text: {sourceText: s}});
            const comp = {layer: (n) => n === 'TEMPOS' ? mk('0,0.4,0.9|3|1.5') : mk('tem entrada de')};
            return [0.1, 0.5, 1.0].map((t) => (new Function('thisComp', 'time', 'return eval(arguments[2])'))(comp, t, e)); }""", sample)
        b.close()
    assert bad == []
    assert vis == ['tem', 'tem entrada', 'tem entrada de']


def test_blocks_work_inside_the_servers_asyncio_loop():
    # MCP tools run inside the server's event loop; the sync Playwright API refuses to start there
    import asyncio
    pytest.importorskip('playwright')
    words = [{'w': w, 's': k * 0.4, 'e': k * 0.4 + 0.3} for k, w in enumerate('Vai ver que eu só preciso de outra'.split())]

    async def tool():
        return mogrt.blocks(words, 'karaoke')
    bl = asyncio.run(tool())
    assert bl and ' '.join(b['text'] for b in bl).lower().startswith('vai ver')
