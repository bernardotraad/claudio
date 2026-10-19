"""Captions reviewed in the NLE: the editor's .srt (text and block timing) drives the overlay."""
import os

import pytest

from conftest import needs_browser
from claudio import montage, motion

SRT = ('﻿1\r\n00:00:00,500 --> 00:00:01,400\r\nE só a INC\r\n\r\n'
       '2\r\n00:00:01,400 --> 00:00:03,000\r\ntem um <i>empreendimento</i>\r\nprivilegiado\r\n\r\n'
       '3\r\n00:00:05,000 --> 00:00:06,000\r\nTexto novo\r\n')
SPOKEN = [{'w': 'E', 's': 0.5, 'e': 0.6}, {'w': 'só', 's': 0.62, 'e': 0.8}, {'w': 'a', 's': 0.82, 'e': 0.9},
          {'w': 'Enki', 's': 0.92, 'e': 1.3}, {'w': 'tem', 's': 1.45, 'e': 1.6}, {'w': 'um', 's': 1.62, 'e': 1.7},
          {'w': 'empreendimento', 's': 1.72, 'e': 2.5}, {'w': 'privilegiado', 's': 2.55, 'e': 3.4}]


@pytest.fixture
def srt(tmp_path, monkeypatch):
    p = tmp_path / 'revisada.srt'
    p.write_bytes(SRT.encode('utf-8'))
    monkeypatch.setattr(motion, 'timeline_words', lambda x: {'timeline': 'INC — Estela v2', 'duration': 8.0,
                                                             'fps': '30000/1001', 'words': [dict(w) for w in SPOKEN]})
    return str(p)


def test_parse_srt_premiere_export(srt):
    cues = motion.parse_srt(srt)
    assert [c[2] for c in cues] == ['E só a INC', 'tem um empreendimento privilegiado', 'Texto novo']
    assert cues[0][:2] == (0.5, 1.4) and cues[2][:2] == (5.0, 6.0)


def test_srt_words_keep_editor_text_and_blocks(srt):
    tw = motion.srt_timeline_words('x.xml', srt, remember=False)
    W, cues = tw['words'], tw['cues']
    assert ' '.join(w['w'] for w in W) == 'E só a INC tem um empreendimento privilegiado Texto novo'
    assert [c[2:] for c in cues] == [[0.5, 1.4], [1.4, 3.0], [5.0, 6.0]]
    for a, b, s, e in cues:                      # every word inside its block, in order
        ws = W[a:b + 1]
        assert ws[0]['s'] == s and all(s <= w['s'] <= w['e'] <= e for w in ws)
        assert all(x['s'] <= y['s'] for x, y in zip(ws, ws[1:]))
    assert W[4]['s'] == 1.4                      # block 2 starts where the editor put it, not at the spoken 1.45
    assert W[7]['e'] <= 3.0                      # "privilegiado" squeezed into the shortened block (spoken until 3.4)


def test_caption_code_pins_cues_every_style():
    W = [{'w': 'a', 's': 0, 'e': .2}, {'w': 'b', 's': .3, 'e': .5}]
    for st in ('social', 'minimal', 'marca'):
        code = motion.caption_code(W, st, look={'dynamic': st == 'marca'}, cues=[[0, 0, 0, .25], [1, 1, .25, .9]])
        assert code.startswith('const CUES=[[0, 0, 0, 0.25], [1, 1, 0.25, 0.9]];window.CUES=CUES;')
    assert 'CUES' not in motion.caption_code(W, 'social')


def test_core_patch_applies():
    pytest.importorskip('PIL')
    try:
        head = motion._core_html()
    except FileNotFoundError:
        pytest.skip('motion-designer skill not installed')
    assert 'if(window.CUES){chunks.length=0;' in head and 'win[k]=[c[2]-.001,c[3]]' in head


@needs_browser
def test_overlay_shows_editor_blocks(srt, tmp_path):
    try:
        motion.skill_dir()
    except FileNotFoundError:
        pytest.skip('motion-designer skill not installed')
    tw = motion.srt_timeline_words('x.xml', srt, remember=False)
    html = motion.build_html(motion.caption_code(tw['words'], 'minimal', cues=tw['cues'], dur=8), str(tmp_path / 'h'))
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={'width': 1080, 'height': 1920})
        pg.goto('file://' + html + '?t=0')
        pg.wait_for_function('window.ready===true')
        txt = lambda t: pg.evaluate(f'(seek({t}), [...document.querySelectorAll("[data-role=caption]")].pop().textContent)')
        assert txt(1.0) == 'EsóaINC'                       # spans, no spaces in textContent
        assert txt(2.9) == 'temumempreendimentoprivilegiado'
        assert txt(4.0) == ''                               # the gap the editor left stays empty
        assert txt(5.5) == 'Textonovo'
        b.close()


def test_sequence_name_and_file():
    assert montage.sequence_name({'name': 'INC — Estela'}, 3) == 'INC — Estela v3'
    assert montage.sequence_name({'name': 'INC — Estela v2'}, 3) == 'INC — Estela v2'
    assert montage.sequence_name({}, 1) == 'Cláudio — montagem v1'
    assert montage.safe_filename('INC — Robson: take 2/final?') == 'INC — Robson- take 2-final-'
    assert os.sep not in montage.safe_filename('a/b\\c')
