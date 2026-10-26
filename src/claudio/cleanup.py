"""Tighten the speech of a sequence the editor already cut: dead air between words and hesitation sounds come out
(ripple across every track, so picture and b-roll stay in sync), music beds keep playing untouched. Deterministic,
no AI: the result is a NEW sequence next to the editor's one, with the list of cuts so nothing is silent."""
import re

from claudio import core, nle_read

# pure hesitation sounds: always a filler
HESITATIONS = {'ãh', 'ã', 'ãã', 'ah', 'ahn', 'ahm', 'hã', 'hãn', 'hum', 'hmm', 'hm', 'uh', 'uhm', 'um', 'eh', 'ehh', 'ehm',
               'éé', 'ééé', 'éh', 'mmm', 'mm'}
# also real words ("é" = is, "e" = and): a filler only when drawn out and set apart by pauses
AMBIGUOUS = {'é', 'e', 'a', 'o'}


def _bare(w):
    return re.sub(r'[^\wãáâàéêíóôõúç]', '', (w or '').lower())


def is_filler(w, prev_e, next_s, extra=()):
    b = _bare(w['w'])
    if b in HESITATIONS or b in extra:
        return True
    return b in AMBIGUOUS and w['e'] - w['s'] >= 0.35 and w['s'] - prev_e >= 0.15 and next_s - w['e'] >= 0.15


def speech_spans(tl, track='A1'):
    """The speech clips of the dialogue track (clips that hold transcript words) and their words on the timeline."""
    spans = []
    for it in sorted([i for i in tl['items'] if i['track'] == track and i['path'] and i['enabled']], key=lambda i: i['tl_in']):
        try:
            idx = core.build_index(it['path'])
        except Exception:
            continue
        ws = [{'w': core.clean_text(w['w']), 's': it['tl_in'] + w['s'] - it['src_in'], 'e': it['tl_in'] + w['e'] - it['src_in']}
              for w in core.words_in(core.load_words(idx), it['src_in'], it['src_out'])]
        if ws:
            spans.append({'a': it['tl_in'], 'b': it['tl_out'], 'words': ws, 'clip': it['name']})
    return spans


def plan_cuts(spans, max_pause=0.45, pad_in=0.10, pad_out=0.18, min_cut=0.12, fillers=True, extra_fillers=()):
    """Timeline ranges to remove: inside each speech clip, pauses longer than max_pause (keeping pad_in before the
    next word and pad_out after the last one) and, if fillers, hesitation sounds with the pause around them."""
    cuts = []
    extra = {_bare(x) for x in extra_fillers}
    for sp in spans:
        W = sp['words']
        edges = [sp['a']] + [w['e'] for w in W]                 # end of what came before each word
        nexts = [w['s'] for w in W] + [sp['b']]
        keep = []
        for k, w in enumerate(W):
            if fillers and is_filler(w, edges[k], nexts[k + 1], extra):
                cuts.append((max(edges[k] + (pad_out if k else 0), w['s'] - 0.03), min(nexts[k + 1] - pad_in, w['e'] + 0.03), 'vício', w['w']))
            else:
                keep.append(w)
        if not keep:
            continue
        bounds = [(sp['a'], keep[0]['s'], True)] + [(a['e'], b['s'], False) for a, b in zip(keep, keep[1:])] + [(keep[-1]['e'], sp['b'], True)]
        for a, b, edge in bounds:
            if b - a > max_pause:
                ca = a if edge and a == sp['a'] else a + pad_out
                cb = b if edge and b == sp['b'] else b - pad_in
                cuts.append((ca, cb, 'pausa', round(b - a, 2)))
    cuts = sorted((a, b, k, x) for a, b, k, x in cuts if b - a >= min_cut)
    merged = []
    for a, b, k, x in cuts:
        if merged and a <= merged[-1][1] + 0.02:
            m = merged[-1]
            merged[-1] = (m[0], max(m[1], b), m[2] if m[2] == 'vício' else k, m[3] if m[2] == 'vício' else x)
        else:
            merged.append((a, b, k, x))
    return merged


def _minus(s, e, cuts):
    out, cur = [], s
    for a, b, *_ in cuts:
        if b <= cur or a >= e:
            continue
        if a > cur:
            out.append((cur, a))
        cur = max(cur, b)
    if cur < e:
        out.append((cur, e))
    return [(a, b) for a, b in out if b - a > 1e-3]


def _shift(t, cuts):
    return t - sum(min(b, t) - a for a, b, *_ in cuts if a < t)


def _music_tracks(layers, ctx, speech_paths):
    """Audio tracks that only hold audio files that are not speech (music, SFX): they keep playing, uncut."""
    keep = set()
    for n, (tpl, its) in enumerate(layers.get('audio', [])):
        if not its:
            continue
        ok = True
        for c, _, _ in its:
            f = ctx['files'].get(c.find('file').get('id'))
            path = nle_read._path_from_url(f.findtext('pathurl')) if f is not None else None
            if not path or path in speech_paths or f.find('media/video') is not None:
                ok = False
                break
        if ok:
            keep.add(n)
    return keep


def clean_speech(nle_xml, out_xml=None, max_pause=0.45, fillers=True, extra_fillers=(), keep_tracks=None, dialogue_track='A1'):
    tl = nle_read.read_fcp7(nle_xml)
    spans = speech_spans(tl, dialogue_track)
    if not spans:
        return {'error': f'nenhuma fala transcrita na trilha {dialogue_track} — indexe os clipes (index_media) ou indique a trilha de diálogo'}
    cuts = plan_cuts(spans, max_pause=max_pause, fillers=fillers, extra_fillers=extra_fillers)
    root, top, layers, ctx = nle_read.flat_timeline(nle_xml)
    speech_paths = {i['path'] for i in tl['items'] if i['track'] == dialogue_track and i['path']}
    keep = _music_tracks(layers, ctx, speech_paths)
    if keep_tracks:
        keep = {int(t[1:]) - 1 for t in keep_tracks if t.upper().startswith('A')}
    old_dur = tl['duration'] or max((e for k in layers.values() for _, its in k for _, _, e in its), default=0)
    new_dur = _shift(old_dur, cuts)
    out = {}
    for kind in ('video', 'audio'):
        new_layers = []
        for n, (tpl, its) in enumerate(layers.get(kind, [])):
            items = []
            for c, s, e in its:
                if kind == 'audio' and n in keep:                 # music bed: as it was, ending with the new cut
                    if s < new_dur:
                        items.append((nle_read.trim_clip(c, s, e, s, min(e, new_dur)), s, min(e, new_dur)))
                    continue
                pieces = _minus(s, e, cuts)
                for pa, pb in pieces:
                    uid = None if len(pieces) == 1 else f'p{int(round(_shift(pa, cuts) * 1000))}'
                    items.append((nle_read.trim_clip(c, s, e, pa, pb, uid), _shift(pa, cuts), _shift(pb, cuts)))
            new_layers.append((tpl, items))
        out[kind] = new_layers
    name = f'{tl["name"]} (limpo)'
    out_xml = core.local_path(out_xml) if out_xml else re.sub(r'\.xml$', '', core.local_path(nle_xml), flags=re.I) + ' (limpo).xml'
    res = nle_read.write_layers(root, top, out, ctx, out_xml, name, 'sequence-limpo', duration=new_dur)
    pauses = [c for c in cuts if c[2] == 'pausa']
    vic = [c for c in cuts if c[2] == 'vício']
    res.update(before_s=round(old_dur, 2), after_s=round(new_dur, 2), removed_s=round(old_dur - new_dur, 2),
               pauses_cut=len(pauses), fillers_cut=[{'at': round(a, 2), 'word': x} for a, b, k, x in vic],
               music_tracks_kept=[f'A{n + 1}' for n in sorted(keep)],
               cuts=[{'from': round(a, 2), 'to': round(b, 2), 'what': k} for a, b, k, x in cuts],
               note=('Whisper costuma não transcrever "é…"/"ãh": vícios que não estão no texto não são achados'
                     if not vic else None))
    return res
