"""claudio.bench — a local benchmark: did a recipe's result come out right? Checked WITHOUT AI on the sequence it produced.

  ~/.config/claudio/benchmark.json   ($CLAUDIO_BENCHMARK), outside the repo — it points at the editor's own jobs:

  {"casos": [{"receita": "depoimento", "xml": "D:\\\\job\\\\#projeto\\\\INC — Robson.xml", "nota": "INC 9 Robson",
              "duracao": [25, 45], "plataforma": "reels", "gancho_s": 1.0}]}

  xml: the sequence the recipe delivered (a folder = its newest .xml). duracao / plataforma / gancho_s override what the
  recipe declares (padroes.duracao ±35 %, verificar.plataforma, verificar.gancho_s).

  Checks: formato (aspect of the sequence) · duração · QC (no errors: flash frames, black gaps, offline media,
  overlaps…) · loudness (within ±3 dB of the platform target — the QC estimate's own accuracy) · sem aninhadas ·
  gancho (first spoken word within gancho_s of the start, for recipes with speech). Each run is appended to
  ~/.config/claudio/benchmark-results.jsonl, so a recipe change can be compared with the last runs.
"""
import datetime, glob, json, os
from fractions import Fraction

from claudio import core, recipes

ASPECTS = {'9:16': 9 / 16, '16:9': 16 / 9, '1:1': 1.0, '4:5': 4 / 5}


def path():
    return os.path.expanduser(core.env('BENCHMARK') or '~/.config/claudio/benchmark.json')


def results_path():
    return os.path.join(os.path.dirname(path()), 'benchmark-results.jsonl')


def load():
    try:
        return json.load(open(path(), encoding='utf-8')).get('casos', [])
    except (OSError, ValueError):
        return []


def _xml(p):
    p = core.local_path(p)
    if os.path.isdir(p):
        xs = sorted(glob.glob(os.path.join(p, '*.xml')), key=os.path.getmtime)
        return xs[-1] if xs else None
    return p if os.path.isfile(p) else None


def check(case):
    """One case → {receita, nota, xml, checks: [{nome, ok (True/False/None = só informação), detalhe}], passou}."""
    from claudio import motion, nle_read, qc
    out = {'receita': case.get('receita'), 'nota': case.get('nota', ''), 'checks': []}
    add = lambda nome, ok, detalhe: out['checks'].append({'nome': nome, 'ok': ok, 'detalhe': detalhe})
    try:
        meta = recipes.load(case['receita'])['meta']
    except KeyError as e:
        add('receita', False, str(e.args[0])); out['passou'] = False
        return out
    padroes, ver = meta.get('padroes') or {}, meta.get('verificar') or {}
    x = _xml(case.get('xml') or '')
    out['xml'] = core.nle_path(x) if x else case.get('xml')
    if not x:
        add('xml', False, f'não achei o XML: {case.get("xml")}'); out['passou'] = False
        return out
    seq = nle_read.read_fcp7(x)

    want = case.get('aspect') or padroes.get('aspect')
    if want in ASPECTS:
        got = seq['width'] / seq['height']
        add('formato', abs(got - ASPECTS[want]) / ASPECTS[want] < 0.01, f'{seq["width"]}×{seq["height"]} (receita: {want})')

    d, rng = seq['duration'], case.get('duracao')
    if not rng and isinstance(padroes.get('duracao'), (int, float)):
        rng = [padroes['duracao'] * 0.65, padroes['duracao'] * 1.35]
    add('duração', (rng[0] <= d <= rng[1]) if rng else None,
        f'{d:.1f} s' + (f' (esperado {rng[0]:.0f}–{rng[1]:.0f} s)' if rng else ' (a receita não fixa duração)'))

    plat = case['plataforma'] if 'plataforma' in case else ver.get('plataforma')     # null in the case = don't measure
    q = qc.qc_timeline(x, plat)
    errs = [i for i in q['issues'] if i['severity'] == 'erro']
    add('QC', not errs, f'{q["summary"]["erros"]} erro(s), {q["summary"]["avisos"]} aviso(s)'
        + (': ' + '; '.join(f'{i["tc"]} {i["issue"]}' for i in errs[:4]) if errs else ''))

    if plat in qc.TARGETS:
        try:
            a = qc.audio_qc(x, plat)
            add('loudness', abs(a['lufs'] - a['platform_target_lufs']) <= 3,
                f'{a["lufs"]} LUFS (alvo {a["platform_target_lufs"]}, pico {a["true_peak_db"]} dB)')
        except Exception as e:
            add('loudness', None, f'não medi: {str(e)[:80]}')

    add('sem aninhadas', not seq['nested'], f'{len(seq["nested"])} sequência(s) aninhada(s)' if seq['nested'] else 'ok')

    g = case.get('gancho_s', ver.get('gancho_s'))
    if g is not None:
        try:
            ws = motion.timeline_words(x)['words']
            first = ws[0]['s'] if ws else None
            add('gancho', first is not None and first <= g,
                f'primeira palavra em {first:.2f} s (limite {g} s)' if first is not None else 'nenhuma fala transcrita na A1')
        except Exception as e:
            add('gancho', None, f'não medi (material indexado?): {str(e)[:80]}')

    out['passou'] = all(c['ok'] is not False for c in out['checks'])
    return out


def run(receita=None, save=True):
    cases = [c for c in load() if not receita or c.get('receita') == receita]
    res = [check(c) for c in cases]
    if save and res:
        stamp = datetime.datetime.now().isoformat(timespec='seconds')
        with open(results_path(), 'a', encoding='utf-8') as f:
            for r in res:
                f.write(json.dumps({'quando': stamp, **r}, ensure_ascii=False) + '\n')
    return {'arquivo': core.nle_path(path()), 'casos': len(cases), 'passaram': sum(r['passou'] for r in res), 'resultados': res}
