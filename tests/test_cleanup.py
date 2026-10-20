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
