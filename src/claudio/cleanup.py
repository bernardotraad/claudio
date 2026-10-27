"""Tighten the speech of a sequence the editor already cut: dead air between words and hesitation sounds come out
(ripple across every track, so picture and b-roll stay in sync), music beds keep playing untouched. Deterministic,
no AI: the result is a NEW sequence next to the editor's one, with the list of cuts so nothing is silent."""
import re

from claudio import core, nle_read

# pure hesitation sounds: always a filler
HESITATIONS = {'ãh', 'ã', 'ãã', 'ah', 'ahn', 'ahm', 'hã', 'hãn', 'hum', 'hmm', 'hm', 'uh', 'uhm', 'eh', 'ehh', 'ehm',
               'éé', 'ééé', 'éh', 'mmm', 'mm'}
# also real words ("é" = is, "e" = and, "um" = a — the English "um" took the article out of "um empreendimento"):
# a filler only when drawn out and set apart by pauses
AMBIGUOUS = {'é', 'e', 'a', 'o', 'um'}


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
              for w in core.words_in(core.load_words(idx), it['src_in'], it['src_out'], idx)]
        if ws:
            spans.append({'a': it['tl_in'], 'b': it['tl_out'], 'words': ws, 'clip': it['name']})
    return spans


# how much air a cut leaves: a pause longer than max_pause becomes keep s, 60 % after the last word, 40 % before the
# next one (video-editor-skills' vlog-pacing: 0.5/0.3 was loved on a class and "popping" on a vlog)
PACE = {'apertado': (0.45, 0.25), 'medio': (0.7, 0.4), 'solto': (1.0, 0.6)}

# notes said on camera that are not part of the video
EDITOR_WORDS = {'editor', 'editora', 'edição', 'edicao'}
MAYBE_NOTE = re.compile(r'\b(aqui entra|entra aqui|(p[õo]e|coloca|bota|insere) .{0,30}aqui|corta aqui|daqui pra frente)\b', re.I)
RESTART = re.compile(r'^(vou de novo|vamos de novo|de novo|deixa eu (repetir|falar de novo|come[çc]ar de novo)|vou repetir|'
                     r'repete|repetindo|perd[ãa]o|desculpa|desculpe|errei|pera|pera[íi]|opa|calma|come[çc]a de novo)\W*$', re.I)


def _same_start(a, b):
    """Two attempts at the same line: the restart opens like the abandoned one ("Você que paga…" / "Você que paga…")."""
    import difflib
    x = [_bare(w['w']) for w in a][:5]
    y = [_bare(w['w']) for w in b][:6]
    return bool(x and y) and (x[:2] == y[:2] or difflib.SequenceMatcher(None, x, y[:len(x)]).ratio() >= 0.6)


def spoken_notes(W, gap=0.5):
    """What was said FOR the editor, not the audience, as [{'i': word indices, 'kind', 'text', 'cut', 'marker'}]:
    a set command on its own ("Ação.", "três, dois, um, valendo") → cut; an utterance that talks to the editor
    ("editor, põe o drone aqui") → cut, with a marker carrying what was said; an announced restart ("vou de novo",
    "errei") → cut, with the abandoned attempt before it when the next one opens the same way (else only flagged);
    "aqui entra…", "põe … aqui" without the word editor may be the speech itself → a marker only."""
    from claudio import captions
    out = []
    slate = set(captions.set_commands(W))
    if slate:
        for b, e in captions.utterances(W):
            if b in slate:
                out.append({'i': list(range(b, e)), 'kind': 'claquete', 'text': ' '.join(w['w'] for w in W[b:e]), 'cut': True})
    U = [(b, e) for b, e in captions.utterances(W, gap) if b not in slate]
    taken = set()
    for k, (b, e) in enumerate(U):
        if k in taken:
            continue
        text = ' '.join(w['w'] for w in W[b:e])
        bare = [_bare(w['w']) for w in W[b:e]]
        if any(x in EDITOR_WORDS for x in bare):
            if e - b <= 2 and k + 1 < len(U):              # "Editor," … "põe o drone aqui": the call and what it asks
                taken.add(k + 1)
                e = U[k + 1][1]
                text = ' '.join(w['w'] for w in W[b:e])
            out.append({'i': list(range(b, e)), 'kind': 'nota', 'text': text, 'cut': True,
                        'marker': ('Nota pro editor', text)})
        elif e - b <= 5 and RESTART.match(core.clean_text(text).strip()):
            prev = U[k - 1] if k else None
            nxt = U[k + 1] if k + 1 < len(U) else None
            if prev and nxt and _same_start(W[prev[0]:prev[1]], W[nxt[0]:nxt[1]]):
                pt = ' '.join(w['w'] for w in W[prev[0]:prev[1]])
                out.append({'i': list(range(prev[0], e)), 'kind': 'recomeço', 'text': f'{pt} / {text}', 'cut': True,
                            'marker': ('Recomeço removido', f'saiu: "{pt}" + "{text}"')})
            else:
                out.append({'i': list(range(b, e)), 'kind': 'recomeço', 'text': text, 'cut': True,
                            'marker': ('Aviso de recomeço (conferir)', f'saiu "{text}"; a tentativa de antes ficou')})
        elif MAYBE_NOTE.search(text):
            out.append({'i': [], 'at': W[b]['s'], 'kind': 'talvez nota', 'text': text, 'cut': False,
                        'marker': ('Nota pro editor? (conferir)', text)})
    return out


def plan_cuts(spans, max_pause=0.45, pad_in=0.10, pad_out=0.18, min_cut=0.12, fillers=True, extra_fillers=(), keep=None,
              notes=True):
    """Timeline ranges to remove: inside each speech clip, pauses longer than max_pause (keep s of air left, 60 %
    after the last word and 40 % before the next; without keep, pad_out/pad_in), hesitation sounds with the pause
    around them, and (notes) what was said for the editor (spoken_notes). Returns (cuts, markers, notes cut)."""
    if keep is not None:
        pad_out, pad_in = keep * 0.6, keep * 0.4
    cuts, markers, noted = [], [], []
    extra = {_bare(x) for x in extra_fillers}
    for sp in spans:
        W = sp['words']
        edges = [sp['a']] + [w['e'] for w in W]                 # end of what came before each word
        nexts = [w['s'] for w in W] + [sp['b']]
        gone = set()
        for n in (spoken_notes(W) if notes else []):
            if n['cut'] and n['i']:
                b, e = n['i'][0], n['i'][-1]
                cuts.append((max(edges[b] + (pad_out if b else 0), W[b]['s'] - 0.03), min(nexts[e + 1] - pad_in, W[e]['e'] + 0.03),
                             n['kind'], n['text']))
                noted.append({'at': round(W[b]['s'], 2), 'what': n['kind'], 'said': n['text']})
                gone.update(n['i'])
            if n.get('marker'):
                markers.append({'s': W[n['i'][0]]['s'] if n['i'] else n['at'], 'name': n['marker'][0], 'comment': n['marker'][1],
                                'color': 'red' if n['cut'] else 'yellow'})
        kept = []
        for k, w in enumerate(W):
            if k in gone:
                continue
            if fillers and is_filler(w, edges[k], nexts[k + 1], extra):
                cuts.append((max(edges[k] + (pad_out if k else 0), w['s'] - 0.03), min(nexts[k + 1] - pad_in, w['e'] + 0.03), 'vício', w['w']))
            else:
                kept.append(w)
        if not kept:
            continue
        bounds = [(sp['a'], kept[0]['s'], True)] + [(a['e'], b['s'], False) for a, b in zip(kept, kept[1:])] + [(kept[-1]['e'], sp['b'], True)]
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
            keep_m = m[2] != 'pausa'                              # the more telling label wins over "pausa"
            merged[-1] = (m[0], max(m[1], b), m[2] if keep_m else k, m[3] if keep_m else x)
        else:
            merged.append((a, b, k, x))
    return merged, markers, noted


def pacing_report(spans, fillers=True):
    """What each pace would do to this sequence, before choosing: cuts and seconds saved."""
    out = {}
    for name, (mp, kp) in PACE.items():
        cuts, _, _ = plan_cuts(spans, max_pause=mp, keep=kp, fillers=fillers)
        out[name] = {'pausa_max_s': mp, 'fica_s': kp, 'cortes': len(cuts), 'economiza_s': round(sum(b - a for a, b, *_ in cuts), 1)}
    return out


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


JUMP_ZOOM = 1.075      # every other piece of one shot after a cut: reads as a second camera, not a jump
PUNCH_ZOOM = 1.15      # the line that lands, for that line only


def _split(pieces, at, min_len=0.3):
    """Split at the punch bounds, but never leave a sliver: a bound that lands a few frames from a join
    would flash 115 % for 2 frames — that piece just stays whole."""
    out = []
    for a, b in pieces:
        cs = sorted(t for t in at if a + min_len <= t <= b - min_len)
        for x, y in zip([a] + cs, cs + [b]):
            out.append((x, y))
    return out


def clean_speech(nle_xml, out_xml=None, max_pause=None, fillers=True, extra_fillers=(), keep_tracks=None, dialogue_track='A1',
                 pace='apertado', notes=True, crossfade=True, zoom=False, punch=None, preview=False):
    """pace: apertado · medio · solto (PACE) — max_pause overrides its pause. preview: only the pacing report.
    notes: cut set commands / notes to the editor / announced restarts (spoken_notes), markers where they were.
    crossfade: a 2-frame audio crossfade on every join a cut made (no click). zoom: pieces of one shot after a cut
    alternate 100 % / 107.5 %. punch: [[from, to], …] seconds on THIS sequence's clock (a line from caption_sentences)
    — that line pushed in to 115 %, back after it."""
    tl = nle_read.read_fcp7(nle_xml)
    spans = speech_spans(tl, dialogue_track)
    if not spans:
        return {'error': f'nenhuma fala transcrita na trilha {dialogue_track} — indexe os clipes (index_media) ou indique a trilha de diálogo'}
    if preview:
        return {'timeline': tl['name'], 'duration_s': round(tl['duration'], 2), 'ritmos': pacing_report(spans, fillers),
                'how': 'escolha o ritmo e rode de novo com pace=<nome> (preview=False)'}
    mp, kp = PACE.get(pace or 'apertado', PACE['apertado'])
    cuts, markers, noted = plan_cuts(spans, max_pause=max_pause or mp, keep=kp, fillers=fillers, extra_fillers=extra_fillers, notes=notes)
    root, top, layers, ctx = nle_read.flat_timeline(nle_xml)
    F = nle_read._rate(top)
    speech_paths = {i['path'] for i in tl['items'] if i['track'] == dialogue_track and i['path']}
    keep = _music_tracks(layers, ctx, speech_paths)
    if keep_tracks:
        keep = {int(t[1:]) - 1 for t in keep_tracks if t.upper().startswith('A')}
    old_dur = tl['duration'] or max((e for k in layers.values() for _, its in k for _, _, e in its), default=0)
    new_dur = _shift(old_dur, cuts)
    punch = [(float(a), float(b)) for a, b in (punch or [])]
    out, zoomed = {}, {'jump': 0, 'punch': 0}
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
                talking = kind == 'video' and _clip_path(c, ctx) in speech_paths
                if talking and punch:
                    pieces = _split(pieces, [t for ab in punch for t in ab])
                alt = 0
                for k, (pa, pb) in enumerate(pieces):
                    uid = None if len(pieces) == 1 else f'p{int(round(_shift(pa, cuts) * 1000))}'
                    cc = nle_read.trim_clip(c, s, e, pa, pb, uid)
                    if talking:
                        in_punch = any(a - 0.05 <= pa and pb <= b + 0.05 for a, b in punch)
                        jump = k and pa - pieces[k - 1][1] > 1e-3      # a cut removed something between the two
                        alt = (alt + 1) % 2 if jump else alt
                        f = PUNCH_ZOOM if in_punch else (JUMP_ZOOM if zoom and alt else 1.0)
                        if f != 1.0 and nle_read.scale_clip(cc, f):
                            zoomed['punch' if in_punch else 'jump'] += 1
                    items.append((cc, _shift(pa, cuts), _shift(pb, cuts)))
            new_layers.append((tpl, items))
        out[kind] = new_layers
    joins = sorted({int(round(_shift(a, cuts) * F)) for a, b, *_ in cuts}) if crossfade else []
    name = f'{tl["name"]} (limpo)'
    out_xml = core.local_path(out_xml) if out_xml else re.sub(r'\.xml$', '', core.local_path(nle_xml), flags=re.I) + ' (limpo).xml'
    mk = [{**m, 's': _shift(m['s'], cuts)} for m in markers]
    res = nle_read.write_layers(root, top, out, ctx, out_xml, name, 'sequence-limpo', duration=new_dur, markers=mk,
                                audio_xfade=(joins, 2), shift=lambda t: _shift(t, cuts))
    by = lambda k: [c for c in cuts if c[2] == k]
    vic = by('vício')
    res.update(pace=pace, before_s=round(old_dur, 2), after_s=round(new_dur, 2), removed_s=round(old_dur - new_dur, 2),
               pauses_cut=len(by('pausa')), fillers_cut=[{'at': round(a, 2), 'word': x} for a, b, k, x in vic],
               notes_cut=noted,
               markers=[{'at': round(m['s'], 2), 'name': m['name'], 'comment': m['comment']} for m in mk],
               crossfades=res.get('crossfades', 0), zoom=zoomed,
               music_tracks_kept=[f'A{n + 1}' for n in sorted(keep)],
               cuts=[{'from': round(a, 2), 'to': round(b, 2), 'what': k} for a, b, k, x in cuts],
               note=('Whisper costuma não transcrever "é…"/"ãh": vícios que não estão no texto não são achados'
                     if not vic else None))
    return res


def _clip_path(c, ctx):
    f = ctx['files'].get(c.find('file').get('id')) if c.find('file') is not None else None
    return nle_read._path_from_url(f.findtext('pathurl')) if f is not None and f.findtext('pathurl') else None
