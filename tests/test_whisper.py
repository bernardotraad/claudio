"""End to end with real speech (Whisper tiny, CPU in CI): word timing, repeated takes, the benchmark's hook, learning
from a delivered video and phrase-cut captions — the parts that need a transcript."""
import pytest

from conftest import needs_ffmpeg, needs_whisper
from claudio import bench, core, learn, motion, server

pytestmark = [needs_ffmpeg, needs_whisper]


def test_words_and_repeated_takes(speech_clip):
    w = core.load_words(core.build_index(str(speech_clip), 'pt'))
    assert len(w) >= 12 and 0.8 <= w[0]['s'] <= 1.4                    # 1 s of silence before "Olá"
    g = server.takes(str(speech_clip))['groups']
    assert g and g[0]['attempts'] == 2 and g[0]['best_take'] == 2       # the same line twice: the last complete one wins


@pytest.fixture
def seq_xml(speech_clip, tmp_path):
    out = tmp_path / 'fala.xml'
    core.export_fcp7({'name': 'fala', 'fps': '25', 'clips': [{'path': str(speech_clip), 'in': 0.0, 'out': 13.0}]}, str(out))
    return out


def test_benchmark_hook(seq_xml, tmp_path, monkeypatch):
    p = tmp_path / 'benchmark.json'
    monkeypatch.setenv('CLAUDIO_BENCHMARK', str(p))
    base = {'receita': 'depoimento', 'xml': str(seq_xml), 'plataforma': None, 'aspect': '16:9', 'duracao': [10, 15]}
    p.write_text(__import__('json').dumps({'casos': [{**base, 'gancho_s': 1.5}, {**base, 'gancho_s': 0.5}]}), encoding='utf-8')
    ok, late = bench.run(save=False)['resultados']
    hook = lambda r: next(c for c in r['checks'] if c['nome'] == 'gancho')
    assert ok['passou'] and hook(ok)['ok'] is True and not late['passou'] and hook(late)['ok'] is False


def test_learn_measures_speech(speech_clip):
    m = learn.measure(str(speech_clip))
    assert 0.8 <= m['primeira_fala_s'] <= 1.4 and m['fala_pct'] > 40 and 0.5 <= m['vinheta_final_s'] <= 2.5
    assert m['w'] == 320 and m['legenda'] is None                       # no burned-in captions in a test pattern


def test_captions_by_phrase(seq_xml, tmp_path):
    out = tmp_path / 'fala.srt'
    motion.captions_srt(str(seq_xml), str(out))
    cues = motion.parse_srt(str(out))
    assert len(cues) >= 2 and cues[0][0] >= 0.8                          # blocks start on the voice, not at 0
