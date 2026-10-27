"""Speech cleanup of an edited sequence: pauses and hesitations out (ripple), music untouched."""
from claudio import cleanup, core, nle_read

R24 = '<rate><timebase>24</timebase><ntsc>FALSE</ntsc></rate>'
WORDS = [{'w': 'Olá', 's': 1.0, 'e': 1.4}, {'w': 'pessoal,', 's': 1.45, 'e': 2.0},   # 2.0 → 4.0: long pause
         {'w': 'hoje', 's': 4.0, 'e': 4.3}, {'w': 'ãh', 's': 4.6, 'e': 5.0}, {'w': 'eu', 's': 5.3, 'e': 5.5},
         {'w': 'é', 's': 5.55, 'e': 5.65}, {'w': 'vou', 's': 5.7, 'e': 6.0}, {'w': 'mostrar.', 's': 6.05, 'e': 6.8}]


def _xml(tmp_path):
    f = lambda fid, name, video=True: (f'<file id="{fid}"><name>{name}</name><pathurl>file://localhost/E%3a/job/{name}</pathurl>{R24}'
                                       f'<duration>9999</duration><media>{"<video/>" if video else ""}<audio/></media></file>')
    c = lambda cid, st, en, i, o, fe, extra='': (f'<clipitem id="{cid}"><name>x</name>{R24}<start>{st}</start><end>{en}</end>'
                                                 f'<in>{i}</in><out>{o}</out>{fe}{extra}</clipitem>')
    link = '<link><linkclipref>v1</linkclipref></link><link><linkclipref>a1</linkclipref></link>'
    x = (f'<?xml version="1.0"?><xmeml version="4"><sequence id="s"><name>Estela</name>{R24}<duration>240</duration><media>'
         f'<video><track>{c("v1", 0, 240, 0, 240, f("f1", "A.mp4"), link)}</track></video>'
         f'<audio><track>{c("a1", 0, 240, 0, 240, "<file id=\"f1\"/>", link)}</track>'
         f'<track>{c("m1", 0, 240, 0, 240, f("f2", "song.wav", video=False))}</track></audio></media></sequence></xmeml>')
    p = tmp_path / 'Estela.xml'
    p.write_text(x, encoding='utf-8')
    return p


def test_clean_speech(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'build_index', lambda p, *a, **k: {'p': p})
    monkeypatch.setattr(core, 'load_words', lambda idx: [dict(w) for w in WORDS] if idx['p'].endswith('A.mp4') else [])
    r = cleanup.clean_speech(str(_xml(tmp_path)))
    assert [x['word'] for x in r['fillers_cut']] == ['ãh']           # the short "é" between words is a real word
    assert r['music_tracks_kept'] == ['A2']
    assert r['pauses_cut'] >= 2 and 0 < r['after_s'] < r['before_s']
    tl = nle_read.read_fcp7(r['path'])
    assert tl['name'] == 'Estela (limpo)'
    v = sorted([i for i in tl['items'] if i['track'] == 'V1'], key=lambda i: i['tl_in'])
    a = sorted([i for i in tl['items'] if i['track'] == 'A1'], key=lambda i: i['tl_in'])
    assert [(x['tl_in_f'], x['src_in_f']) for x in v] == [(x['tl_in_f'], x['src_in_f']) for x in a]   # picture and speech in sync
    assert all(x['tl_out_f'] == y['tl_in_f'] for x, y in zip(v, v[1:]))                               # rippled: no gaps
    assert v[0]['src_in'] >= 0.85 and abs(v[-1]['tl_out'] - r['after_s']) < 0.05
    m = [i for i in tl['items'] if i['track'] == 'A2']
    assert len(m) == 1 and m[0]['src_in_f'] == 0 and abs(m[0]['tl_out'] - r['after_s']) < 0.05      # music plays on, uncut
    # nothing spoken was lost: every kept word's source time is inside a kept piece
    kept = [(x['src_in'], x['src_out']) for x in v]
    for w in WORDS:
        if w['w'] != 'ãh':
            assert any(a_ - 0.01 <= w['s'] and w['e'] <= b_ + 0.01 for a_, b_ in kept), w


def _run(tmp_path, monkeypatch, words, **kw):
    monkeypatch.setattr(core, 'build_index', lambda p, *a, **k: {'p': p})
    monkeypatch.setattr(core, 'load_words', lambda idx: [dict(w) for w in words] if idx['p'].endswith('A.mp4') else [])
    return cleanup.clean_speech(str(_xml(tmp_path)), **kw)


def _w(text, t0=0.5, step=0.3, pauses=()):
    out, t = [], t0
    for i, x in enumerate(text.split()):
        t += dict(pauses).get(i, 0)
        out.append({'w': x, 's': round(t, 3), 'e': round(t + step - 0.05, 3)})
        t += step
    return out


def test_the_article_um_is_not_a_filler():
    W = _w('tem um empreendimento novo')
    assert not cleanup.is_filler(W[1], W[0]['e'], W[2]['s'])


def test_set_commands_notes_and_restarts_come_out_with_markers(tmp_path, monkeypatch):
    W = _w('Ação. Você que paga aluguel errei Você que paga aluguel e sente que vai embora. '
           'Editor põe o drone aqui. Clique no link.', t0=0.3, step=0.22, pauses={1: 0.6, 5: 0.6, 6: 0.6, 16: 0.6, 21: 0.6})
    r = _run(tmp_path, monkeypatch, W)
    kinds = [n['what'] for n in r['notes_cut']]
    assert 'claquete' in kinds and 'nota' in kinds and 'recomeço' in kinds
    names = [m['name'] for m in r['markers']]
    assert 'Nota pro editor' in names and 'Recomeço removido' in names
    assert '<marker>' in open(r['path'], encoding='utf-8').read()
    said = ' '.join(n['said'] for n in r['notes_cut'])
    assert 'Você que paga aluguel / errei' in said and 'drone' in said


def test_maybe_note_is_only_flagged():
    notes = cleanup.spoken_notes(_w('e aqui entra a varanda gourmet com churrasqueira'))
    assert notes and not notes[0]['cut'] and notes[0]['marker'][0].startswith('Nota pro editor?')


def test_joins_get_a_crossfade_and_jump_cuts_zoom(tmp_path, monkeypatch):
    r = _run(tmp_path, monkeypatch, WORDS, zoom=True)
    x = open(r['path'], encoding='utf-8').read()
    assert r['crossfades'] >= 1 and x.count('<transitionitem>') == r['crossfades']
    assert 'KGAudioTransCrossFade3dB' in x
    assert r['zoom']['jump'] >= 1 and '<value>107.50</value>' in x
    r2 = _run(tmp_path, monkeypatch, WORDS, crossfade=False)
    assert '<transitionitem>' not in open(r2['path'], encoding='utf-8').read()


def test_punch_in_holds_one_line(tmp_path, monkeypatch):
    r = _run(tmp_path, monkeypatch, WORDS, punch=[[5.2, 6.9]])
    assert r['zoom']['punch'] == 1 and '<value>115.00</value>' in open(r['path'], encoding='utf-8').read()


def test_pacing_presets_and_preview(tmp_path, monkeypatch):
    r = _run(tmp_path, monkeypatch, WORDS, preview=True)
    assert set(r['ritmos']) == {'apertado', 'medio', 'solto'}
    assert r['ritmos']['apertado']['economiza_s'] >= r['ritmos']['solto']['economiza_s']
    tight = _run(tmp_path, monkeypatch, WORDS, pace='apertado')
    loose = _run(tmp_path, monkeypatch, WORDS, pace='solto')
    assert tight['after_s'] < loose['after_s']


def test_punch_split_leaves_no_sliver():
    from claudio.cleanup import _split
    assert _split([(0, 5), (6, 10)], [4.9, 6.07]) == [(0, 5), (6, 10)]
    assert _split([(0, 5)], [2, 3]) == [(0, 2), (2, 3), (3, 5)]
