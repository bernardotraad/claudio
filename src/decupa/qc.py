"""decupa.qc — check a timeline before the editor exports it. Reports only; never changes the timeline.

  qc_timeline(nle_xml, platform)  reads the timeline as it is NOW (FCP7 XML exported by Premiere/Resolve) and lists
  issues with timeline timecode and severity, ready to be dropped as markers by the NLE MCP:
      flash frames · gaps in V1 (black) · overlapping clips · offline media · disabled clips · clips past the end of
      their media · the same take used twice · jump cuts · speech level jumps between cuts · clipped audio ·
      sequence size / fps / duration vs the delivery platform
"""
import os
from fractions import Fraction

import numpy as np

from decupa import core

PLATFORMS = {
    'reels':   {'size': (1080, 1920), 'max_s': 90, 'label': 'Instagram Reels'},
    'tiktok':  {'size': (1080, 1920), 'max_s': 600, 'label': 'TikTok'},
    'shorts':  {'size': (1080, 1920), 'max_s': 60, 'label': 'YouTube Shorts'},
    'feed':    {'size': (1080, 1350), 'max_s': 60, 'label': 'Feed 4:5'},
    'youtube': {'size': (1920, 1080), 'max_s': None, 'label': 'YouTube 16:9'},
    'youtube4k': {'size': (3840, 2160), 'max_s': None, 'label': 'YouTube 4K'},
}


def _tc(sec, fps):
    f = int(round(sec * float(fps)))
    fi = round(float(fps))
    return f'{f // (fi * 3600):02d}:{f // (fi * 60) % 60:02d}:{f // fi % 60:02d}:{f % fi:02d}'


def _speech_db(path, s, e):
    """Median level of the loud part (speech) of a source range, from the index (no decoding)."""
    try:
        d = core.cache_dir(core.local_path(path))
        p = os.path.join(d, 'rms_db.npy')
        if not os.path.exists(p):
            return None, None
        db = np.load(p)
        v = db[int(s / core.HOP): int(e / core.HOP) + 1]
        if len(v) < 5:
            return None, None
        loud = v[v >= np.percentile(v, 60)]
        return float(np.median(loud)), float(v.max())
    except Exception:
        return None, None


def qc_timeline(nle_xml, platform=None, seq_size=None):
    from decupa import nle_read
    tl = nle_read.read_fcp7(nle_xml)
    fps = Fraction(tl['fps']); F = float(fps)
    issues = []

    def add(sev, t, what, detail=''):
        issues.append({'severity': sev, 'tl': round(t, 3), 'tc': _tc(t, fps), 'issue': what, 'detail': detail})

    vids = [i for i in tl['items'] if i['track'].startswith('V')]
    auds = [i for i in tl['items'] if i['track'].startswith('A')]
    by_track = {}
    for i in tl['items']:
        by_track.setdefault(i['track'], []).append(i)
    for tr, items in by_track.items():
        items.sort(key=lambda i: i['tl_in_f'])
        for a, b in zip(items, items[1:]):
            if b['tl_in_f'] < a['tl_out_f']:
                add('erro', b['tl_in'], f'clipes sobrepostos em {tr}', f"{a['name']} / {b['name']}")
    v1 = sorted(by_track.get('V1', []), key=lambda i: i['tl_in_f'])
    if v1 and v1[0]['tl_in_f'] > 0:
        add('aviso', 0, 'preto no começo (V1 vazia)', f"{v1[0]['tl_in']:.2f}s")
    covered = sorted([(i['tl_in_f'], i['tl_out_f']) for i in vids if i['enabled']])
    end = 0
    for s, e in covered:
        if s > end + 0 and end > 0:
            add('erro', end / F, 'buraco na imagem (preto)', f'{(s - end) / F:.2f}s sem nenhum vídeo')
        end = max(end, e)
    for i in tl['items']:
        n = i['tl_out_f'] - i['tl_in_f']
        if i['track'].startswith('V') and 0 < n <= 3:
            add('erro', i['tl_in'], 'flash frame', f"{i['name']}: {n} frame(s)")
        if not i['enabled']:
            add('aviso', i['tl_in'], 'clipe desativado', f"{i['track']} {i['name']}")
        if i['path'] and not os.path.exists(i['path']):
            add('erro', i['tl_in'], 'mídia offline', core.nle_path(i['path']))
        elif i['path']:
            try:
                dur = core.light_index(i['path'])['info']['duration']
                if i['src_out'] > dur + 1 / F:
                    add('erro', i['tl_in'], 'clipe passa do fim da mídia', f"{i['name']}: out {i['src_out']:.2f}s > {dur:.2f}s")
            except Exception:
                pass
    # the same take twice / jump cuts on the spine
    vv = [i for i in vids if i['path'] and i['enabled']]
    for k, a in enumerate(vv):
        for b in vv[k + 1:]:
            if a['path'] == b['path']:
                ov = min(a['src_out'], b['src_out']) - max(a['src_in'], b['src_in'])
                if ov > 0.5 * min(a['src_out'] - a['src_in'], b['src_out'] - b['src_in']):
                    add('aviso', b['tl_in'], 'mesmo trecho usado duas vezes', f"{a['name']} em {a['tl_in']:.1f}s e {b['tl_in']:.1f}s")
    for a, b in zip(v1, v1[1:]):
        if a['path'] and a['path'] == b['path'] and b['tl_in_f'] == a['tl_out_f'] and 0 < b['src_in'] - a['src_out'] < 1.0:
            covered_by_broll = any(i['track'] != 'V1' and i['tl_in'] <= b['tl_in'] < i['tl_out'] for i in vids)
            if not covered_by_broll:
                add('aviso', b['tl_in'], 'jump cut (mesmo plano, pulo curto)', f"{a['name']}: pula {b['src_in'] - a['src_out']:.2f}s — cobrir com b-roll?")
    # speech level jumps between consecutive dialogue clips + clipped audio
    a1 = sorted([i for i in auds if i['track'] == 'A1' and i['path']], key=lambda i: i['tl_in_f'])
    levels = []
    for i in a1:
        lv, pk = _speech_db(i['path'], i['src_in'], i['src_out'])
        if lv is not None:
            levels.append((i, lv))
        if pk is not None and pk > -0.5:
            add('aviso', i['tl_in'], 'áudio estourando na fonte', f"{i['name']}: pico {pk:.1f} dBFS")
    for (a, la), (b, lb) in zip(levels, levels[1:]):
        if abs(la - lb) > 6:
            add('aviso', b['tl_in'], 'salto de volume entre falas', f"{a['name']} {la:.0f} dB → {b['name']} {lb:.0f} dB (ajustar ganho)")
    # delivery
    if platform:
        pf = PLATFORMS.get(platform.lower())
        if pf:
            if seq_size and tuple(seq_size) != pf['size']:
                add('erro', 0, f"formato da sequência ≠ {pf['label']}", f"{seq_size[0]}x{seq_size[1]} (esperado {pf['size'][0]}x{pf['size'][1]})")
            if pf['max_s'] and tl['duration'] > pf['max_s']:
                add('aviso', tl['duration'], f"mais longo que o ideal para {pf['label']}", f"{tl['duration']:.1f}s > {pf['max_s']}s")
    issues.sort(key=lambda x: ({'erro': 0, 'aviso': 1}.get(x['severity'], 2), x['tl']))
    return {'timeline': tl['name'], 'duration': round(tl['duration'], 2), 'fps': tl['fps'],
            'summary': {'erros': sum(x['severity'] == 'erro' for x in issues), 'avisos': sum(x['severity'] == 'aviso' for x in issues)},
            'issues': issues,
            'markers_hint': 'add each issue as a marker in the NLE (Premiere add_marker / Resolve add_marker) so the editor sees it in place'}
