"""Learning a client from delivered videos, without AI: caption blocks found from a band that changes alone, numbers
aggregated, the closest recipe chosen, the client's recipe and profile written — never over the editor's own recipe or
the profile's chosen one. (measure() needs Whisper: the end-to-end test feeds it measured numbers.)"""
import json, os

import pytest

from conftest import ff, needs_ffmpeg
from claudio import learn, recipes


def _m(name, dur=35.0, fala=56, first=0.1, w=1080, h=1920, cap=True, lufs=-12.0):
    return {'video': name, 'path': name, 'w': w, 'h': h, 'fps': '30000/1001', 'duracao_s': dur, 'planos': 10,
            'corte_a_cada_s': 3.2, 'fala_pct': fala, 'primeira_fala_s': first, 'vinheta_final_s': 5.7,
            'musica_de_fundo': True, 'lufs': lufs, 'true_peak_db': -2.9, 'rosto_pct': 50,
            'legenda': {'faixa_y': 0.59, 'trocas': 40, 'bloco_s': 0.6, 'primeira_s': 0.3, 'cobertura_pct': 80} if cap else None}


@needs_ffmpeg
def test_caption_blocks_from_a_band_that_changes_alone(tmp_path):
    # grain everywhere (like footage), and a white box in the lower-middle band that swaps every 0.7 s
    v = tmp_path / 'cap.mp4'
    ff('-f', 'lavfi', '-i', 'color=c=gray:s=270x480:r=30:d=12', '-vf',
       "noise=alls=12:allf=t,drawbox=x=30:y=270:w=210:h=40:color=white:t=fill:enable='lt(mod(t,1.4),0.7)'",
       '-pix_fmt', 'yuv420p', str(v))
    c = learn.captions(str(v), 12.0)
    assert c and 0.5 <= c['faixa_y'] <= 0.68 and c['bloco_s'] == pytest.approx(0.7, abs=0.11) and c['cobertura_pct'] > 80


@needs_ffmpeg
def test_no_captions_in_plain_footage(tmp_path):
    v = tmp_path / 'plain.mp4'
    ff('-f', 'lavfi', '-i', 'color=c=gray:s=270x480:r=30:d=8', '-vf', 'noise=alls=12:allf=t', '-pix_fmt', 'yuv420p', str(v))
    assert learn.captions(str(v), 8.0) is None


def test_aggregate_and_base_recipe():
    ms = [_m(f'v{k}.mp4', dur=27 + k, first=0.1 * k) for k in range(10)]
    a = learn.aggregate(ms)
    assert a['aspect'] == '9:16' and a['formato'] == '1080x1920 (100%)' and a['duracao_s']['mediana'] == 31.5
    assert a['legenda']['com_legenda_pct'] == 100 and a['musica_de_fundo_pct'] == 100
    assert learn.base_for(a) == 'depoimento'
    assert learn.base_for(learn.aggregate([_m('h.mp4', w=1920, h=1080)])) == 'depoimento-horizontal'
    assert learn.base_for(learn.aggregate([_m('p.mp4', w=1920, h=1080, dur=3600)])) == 'podcast'
    assert learn.base_for(learn.aggregate([_m('b.mp4', fala=5, cap=False)])) == 'beat-edit'


def test_videos_in_newest_first_and_skips_proxies(tmp_path):
    for k, n in enumerate(['a.mp4', 'b.mov', 'Proxy/c.mp4', 'd.txt', 'sub/e.mp4']):
        p = tmp_path / n; p.parent.mkdir(exist_ok=True); p.write_bytes(b'x'); os.utime(p, (k, k))
    assert [os.path.basename(p) for p in learn.videos_in(str(tmp_path), 2)] == ['e.mp4', 'b.mov']


@pytest.fixture
def editor(tmp_path, monkeypatch):
    monkeypatch.setenv('CLAUDIO_RECIPES', str(tmp_path / 'receitas'))
    monkeypatch.setenv('CLAUDIO_CLIENTS', str(tmp_path / 'clientes'))
    monkeypatch.setattr(learn, 'measure', lambda p, progress=None: _m(os.path.basename(p)))
    return tmp_path


def test_learn_writes_the_clients_recipe_and_profile(editor):
    from claudio import clients
    r = learn.learn('Cliente Novo', paths=['/x/a.mp4', '/x/b.mp4', '/x/c.mp4'])
    assert r['base'] == 'depoimento' and r['receita_nome'] == 'cliente-novo'
    got = recipes.load('cliente-novo')
    assert got['meta']['padroes']['aspect'] == '9:16' and got['meta']['padroes']['duracao'] == 35
    assert got['meta']['verificar'] == {'plataforma': 'reels', 'gancho_s': 0.5}
    assert '## Medido nos vídeos prontos' in got['text'] and '## Etapas' in got['text']    # numbers + the base's steps
    prof = clients.load('Cliente Novo')
    assert prof['receita'] == 'cliente-novo' and prof['house_style']['medido_videos']['videos'] == 3


def test_learn_never_overwrites_the_editors_recipe_or_choice(editor):
    from claudio import clients
    clients.save('INC', {'receita': 'depoimento'})
    learn.learn('INC', paths=['/x/a.mp4'])                              # inc.md is new: written
    r = learn.learn('INC', paths=['/x/a.mp4'])                          # inc.md exists: a dated draft beside it
    assert r['receita_nome'].startswith('inc-rascunho-') and recipes.load(r['receita_nome'])['nome'] == r['receita_nome']
    assert clients.load('INC')['receita'] == 'depoimento'               # the profile's choice stays


def test_save_recipe_tool(editor):
    from claudio import server
    assert 'error' in server.save_recipe('x', '# sem frontmatter')
    md = '---\nnome: inc\ntitulo: INC — reels de corretor\nquando: corretor INC falando\n---\n## Etapas\n1. ...\n'
    r = server.save_recipe('INC', md)
    assert r['nome'] == 'inc' and r['sections'] == ['etapas'] and recipes.load('inc')['origem'] == 'editor'
