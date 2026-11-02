"""The panel's search box, without AI: speech (fuzzy, takes apart), logs (accents ignored, the editor's notes first)
and Match Similar (the moment itself left out, at most 2 per clip)."""
import json

import numpy as np

from claudio import core, visual
from claudio.search import log_hits, phrase_hits


def _idx(tmp_path, name, words=()):
    d = tmp_path / name
    d.mkdir()
    (d / 'words.json').write_text(json.dumps([{'w': w, 's': s, 'e': s + 0.4} for w, s in words]))
    return {'cache': str(d), 'info': {'fps': '24', 'duration': 60, 'start_tc': None, 'nle_path': f'E:\\job\\{name}.mp4'}}


def test_phrase_finds_each_take(tmp_path):
    idx = _idx(tmp_path, 'a', [('o', 1), ('apartamento', 1.5), ('tem', 2), ('vista', 2.5), ('ok', 10),
                               ('o', 20), ('apartamento', 20.5), ('tem', 21), ('vista', 21.5)])
    hits = phrase_hits([idx], 'apartamento tem vista', min_ratio=0.75)
    assert sorted(h['s'] for h in hits) == [1.5, 20.5]
    assert all(h['similarity'] == 1.0 for h in hits)
    assert not phrase_hits([idx], 'piscina', min_ratio=0.8)


def test_logs_match_without_accents(tmp_path):
    idx = _idx(tmp_path, 'b')
    core.annotate(idx, 0, 4, 'close de rosto, plano médio', ['auto'])
    core.annotate(idx, 8, 12, 'Estela fala da área de lazer, plano aberto')
    assert [h['s'] for h in log_hits([idx], 'medio')] == [0]
    assert [h['s'] for h in log_hits([idx], 'AREA lazer')] == [8]
    assert [h['auto'] for h in log_hits([idx], 'plano')] == [False, True]     # the editor's note before the automatic one


def test_similar_skips_the_reference_and_spreads_over_clips(monkeypatch):
    rng = np.random.default_rng(0)
    base = rng.normal(size=512)
    def emb(n, noise):
        e = base + noise * rng.normal(size=(n, 512))
        return e / np.linalg.norm(e, axis=1, keepdims=True)
    clips = {'/a.mp4': emb(20, 0.1), '/b.mp4': emb(20, 0.3), '/c.mp4': emb(20, 3.0)}
    monkeypatch.setattr(visual, 'embed_clip', lambda p: {'times': [2.0 * k for k in range(20)], 'emb': clips[p]})
    monkeypatch.setattr(visual, 'is_embedded', lambda p: True)
    monkeypatch.setattr(core, 'local_path', lambda p: p)
    monkeypatch.setattr(core, 'nle_path', lambda p: p)
    hits = visual.similar(list(clips), '/a.mp4', 10.0, top=8)
    assert not any(h['path'] == '/a.mp4' and abs(h['peak'] - 10) < 6 for h in hits)
    assert max(sum(h['path'] == p for h in hits) for p in clips) <= 2
    assert hits[0]['path'] in ('/a.mp4', '/b.mp4') and hits[-1]['score'] <= hits[0]['score']


def test_search_finds_plurals_and_gender_but_keeps_short_words_apart(tmp_path):
    from claudio.search import stem
    for a, b in [('apartamento', 'apartamentos'), ('opção', 'opções'), ('parcela', 'Parcelas'), ('imóvel', 'imóveis'),
                 ('vez', 'vezes'), ('mulher', 'mulheres'), ('real', 'reais')]:
        assert stem(a) == stem(b), (a, b)
    for a, b in [('mais', 'mas'), ('casa', 'caso'), ('a', 'as')]:
        assert stem(a) != stem(b), (a, b)
    idx = _idx(tmp_path, 'c', [('o', 1), ('seu', 1.4), ('primeiro', 1.8), ('apartamentos', 2.3), ('ok', 5)])
    assert [h['said'] for h in phrase_hits([idx], 'apartamento', min_ratio=0.8)] == ['apartamentos']
    assert phrase_hits([idx], 'primeiro apartamento', min_ratio=0.75)[0]['similarity'] == 1.0
    core.annotate(idx, 0, 4, 'Cadeira e mesas no salão de festas', ['auto'])
    assert [h['s'] for h in log_hits([idx], 'mesa salões')] == [0]
