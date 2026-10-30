"""The local benchmark checks a recipe's result without AI: format, duration, QC, nesting — and remembers each run.
(Gancho and loudness need the transcribed index, so these cases switch them off; the real cases run on the editor's
machine.)"""
import json, os

import pytest

from conftest import ff, needs_ffmpeg
from claudio import bench, core


@pytest.fixture
def seq(tmp_path):
    """A 4 s 16:9 sequence of a synthetic clip, as an FCP7 XML."""
    clip = tmp_path / 'A001.mp4'
    ff('-f', 'lavfi', '-i', 'testsrc2=s=320x180:r=25:d=6', '-f', 'lavfi', '-i', 'sine=f=440:d=6', '-shortest',
       '-pix_fmt', 'yuv420p', str(clip))
    out = tmp_path / 'resultado' / 'corte v1.xml'
    out.parent.mkdir()
    core.export_fcp7({'name': 'corte', 'fps': '25', 'clips': [{'path': str(clip), 'in': 1.0, 'out': 5.0}]}, str(out))
    return out


@pytest.fixture
def cases(tmp_path, monkeypatch):
    p = tmp_path / 'benchmark.json'
    monkeypatch.setenv('CLAUDIO_BENCHMARK', str(p))
    def use(*cs):
        p.write_text(json.dumps({'casos': list(cs)}), encoding='utf-8')
    return use


def _checks(r):
    return {c['nome']: c['ok'] for c in r['checks']}


def test_no_benchmark_file(cases):
    assert bench.run(save=False)['casos'] == 0


@needs_ffmpeg
def test_a_good_result_passes_and_is_remembered(seq, cases):
    cases({'receita': 'podcast', 'nota': 'sintético', 'xml': str(seq.parent), 'plataforma': None})   # a folder = newest .xml
    r = bench.run()
    c = r['resultados'][0]
    assert r['passaram'] == 1 and c['passou']
    assert _checks(c) == {'formato': True, 'duração': None, 'QC': True, 'sem aninhadas': True}   # podcast: no fixed length
    lines = open(bench.results_path(), encoding='utf-8').read().splitlines()
    assert json.loads(lines[-1])['receita'] == 'podcast' and 'quando' in json.loads(lines[-1])


@needs_ffmpeg
def test_wrong_format_and_length_fail(seq, cases):
    cases({'receita': 'depoimento', 'xml': str(seq), 'plataforma': None, 'gancho_s': None})
    c = bench.run(save=False)['resultados'][0]
    assert not c['passou'] and _checks(c)['formato'] is False and _checks(c)['duração'] is False   # 16:9, 4 s vs 9:16, ~60 s
    cases({'receita': 'depoimento', 'xml': str(seq), 'plataforma': None, 'gancho_s': None, 'aspect': '16:9', 'duracao': [3, 5]})
    assert bench.run(save=False)['resultados'][0]['passou']                                  # the case overrides the recipe


def test_missing_xml_and_unknown_recipe_fail(tmp_path, cases):
    cases({'receita': 'depoimento', 'xml': str(tmp_path / 'nada.xml')}, {'receita': 'casamento', 'xml': 'x'})
    r = bench.run(save=False)
    assert r['passaram'] == 0 and [c['checks'][0]['nome'] for c in r['resultados']] == ['xml', 'receita']
    assert not os.path.exists(bench.results_path())
