"""Soundtrack for a cut that is already on the timeline: the AI chooses the track with the editor (Storyblocks or a file
of theirs); everything after that is deterministic — download into <job>/#trilhas, fit the song to the sequence (its
real ending lands on the end of the cut, the start on a downbeat), set the level from measured loudness and duck it
under speech with Volume keyframes. The panel lays it on a NEW audio track of each live sequence (claudioPlaceMusic),
never on a track that has clips, never through an XML round trip (MOGRT captions would not survive it).

  brief(nle_xml)          what the cut needs: length, how much of it is speech, pace, music already in it
  fetch(url, job, title)  download (or copy) the chosen track into <job>/#trilhas
  fit(path, duration)     segments of the song on the timeline (+ fades)
  plan(job, sequences)    one .claudio-trilha.jsx for all the sequences, applied by the panel in one go
"""
import datetime, json, os, re, shutil, urllib.parse, urllib.request

import numpy as np

from claudio import core

UNDER_SPEECH_LU = 18.0      # music this far below the speech while someone talks
GAP_LU = 6.0                # …and this far below it in the breathing room between lines
ENDS_LU = 2.0               # opening / closing card with no speech (≥ ENDS_MIN s): the music comes up near the voice
ENDS_MIN = 3.0
NO_SPEECH_LUFS = -14.0      # a cut without speech: the music is the mix (social platforms' target)
MIN_GAP = 2.5               # a pause shorter than this stays ducked (no pumping between sentences)
PRE, DOWN, POST, UP = 0.15, 0.35, 0.25, 0.8    # duck lands PRE s before the voice over DOWN s; comes back over UP s
SILENT_DB = -45.0
MAX_GAIN = 15.0             # Premiere's clip Volume tops out at +15 dB
FLOOR_DB = -40.0            # level at the outer ends of a fade
ENTRY_SNAP = 0.25           # a downbeat this close after the needed start is taken (music starts that late)


def _safe(s):
    return re.sub(r'\s+', ' ', re.sub(r'[\\/:*?"<>|\x00-\x1f]', '-', str(s))).strip(' .-')[:90] or 'trilha'


# ───────────────────────────── what the cut needs ─────────────────────────────
def _has_speech_index(path):
    d = core.cache_dir(core.local_path(path))
    return os.path.exists(os.path.join(d, 'words.json'))


def speech_spans(nle_xml, tl=None, words=None, join=0.6):
    """Timeline stretches where someone speaks: the edit's words (A1, lyrics excluded) joined across pauses < `join` s.
    A video clip on A1 that was never indexed counts as speech from end to end (better ducked than buried), and is
    listed so the editor knows."""
    from claudio import motion, nle_read
    tl = tl or nle_read.read_fcp7(nle_xml)
    auto = words is None
    if auto:
        try:
            words = [w for w in motion.timeline_words(nle_xml)['words'] if not w.get('lyrics')]
        except Exception:
            words = []
    iv = [(w['s'], w['e']) for w in words]
    unindexed = []
    for it in tl['items']:
        if it['track'] != 'A1' or not it['enabled'] or not it['path']:
            continue
        if os.path.splitext(it['path'])[1].lower() in core.VIDEO_EXT and auto and not _has_speech_index(it['path']):
            iv.append((it['tl_in'], it['tl_out']))
            unindexed.append(os.path.basename(it['path']))
    out = []
    for a, b in sorted(iv):
        if out and a - out[-1][1] <= join:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(round(a, 3), round(b, 3)) for a, b in out], sorted(set(unindexed))


def music_items(tl):
    """Audio clips already in the cut that are music (audio files, or anything off A1 with no indexed speech)."""
    out = []
    for it in tl['items']:
        if not it['track'].startswith('A') or not it['enabled'] or not it['path']:
            continue
        ext = os.path.splitext(it['path'])[1].lower()
        if ext in core.AUDIO_EXT and not (it['track'] == 'A1' and _has_speech_index(it['path'])):
            out.append({'track': it['track'], 'name': os.path.basename(it['path']), 'tl_in': round(it['tl_in'], 2),
                        'tl_out': round(it['tl_out'], 2)})
    seen, uniq = set(), []
    for m in out:                                       # a stereo pair is one clip
        k = (m['name'], m['tl_in'])
        if k not in seen:
            seen.add(k); uniq.append(m)
    return uniq


def brief(nle_xml, words=None):
    """What a soundtrack for this cut must be: exact length, how much is speech (→ instrumental, lower BPM, a bed),
    cut pace (→ energy), the music already in it, and the Storyblocks search constraints that follow."""
    from claudio import nle_read
    tl = nle_read.read_fcp7(nle_xml)
    dur = tl['duration'] or max((i['tl_out'] for i in tl['items']), default=0)
    spans, unindexed = speech_spans(nle_xml, tl, words)
    talk = sum(b - a for a, b in spans)
    cov = talk / dur if dur else 0
    starts = sorted({round(i['tl_in'], 2) for i in tl['items'] if i['track'].startswith('V') and i['enabled']})
    cpm = len(starts) / dur * 60 if dur else 0
    kind = 'fala' if cov > 0.35 else 'misto' if cov > 0.1 else 'sem fala'
    if kind == 'fala':
        bpm, energy = (80, 110), 'baixa a média: uma cama atrás da fala, sem melodia que dispute com a voz'
    elif cpm > 30:
        bpm, energy = (115, 140), 'alta: o corte é rápido, a música puxa'
    else:
        bpm, energy = (95, 125), 'média'
    w, h = tl.get('width'), tl.get('height')
    return {'sequence': tl['name'], 'duration': round(dur, 2), 'aspect': f'{w}x{h}' if w else None,
            'speech_ratio': round(cov, 2), 'speech_spans': len(spans), 'kind': kind, 'cuts_per_min': round(cpm, 1),
            'music_already': music_items(tl), 'not_indexed_speech': unindexed,
            'search': {'instrumental': kind != 'sem fala', 'bpm': bpm, 'energy': energy,
                       'min_duration': round(dur, 1),
                       'note': 'prefira a versão (edit) da música mais próxima da duração, com final de verdade; '
                               'o Cláudio encaixa o final da música no fim da sequência'}}


# ───────────────────────────── download ─────────────────────────────
EXT_BY_TYPE = {'audio/mpeg': '.mp3', 'audio/mp3': '.mp3', 'audio/wav': '.wav', 'audio/x-wav': '.wav', 'audio/wave': '.wav',
               'audio/aiff': '.aif', 'audio/x-aiff': '.aif', 'audio/mp4': '.m4a', 'audio/x-m4a': '.m4a'}


def fetch(url, job, title=None):
    """Put the chosen track in <job>/#trilhas: download a URL (Storyblocks' signed link) or copy a local file. The same
    file is not fetched twice. Returns its path as the NLE sees it, plus duration."""
    d = os.path.join(core.local_path(job), '#trilhas')
    os.makedirs(d, exist_ok=True)
    src_local = None if re.match(r'^https?://', url or '', re.I) else core.local_path(url)
    if src_local:
        if not os.path.isfile(src_local):
            return {'error': f'arquivo não encontrado: {url}'}
        ext = os.path.splitext(src_local)[1].lower()
        dst = os.path.join(d, _safe(title or os.path.splitext(os.path.basename(src_local))[0]) + ext)
        if os.path.abspath(src_local) != os.path.abspath(dst) and not (os.path.exists(dst) and os.path.getsize(dst) == os.path.getsize(src_local)):
            shutil.copyfile(src_local, dst)
    else:
        req = urllib.request.Request(url, headers={'User-Agent': 'claudio/1.0'})
        with urllib.request.urlopen(req, timeout=120) as r:
            ctype = (r.headers.get('Content-Type') or '').split(';')[0].strip().lower()
            ext = os.path.splitext(urllib.parse.urlparse(r.geturl()).path)[1].lower()
            if ext not in core.AUDIO_EXT:
                ext = EXT_BY_TYPE.get(ctype, '.mp3')
            if ctype.startswith(('text/', 'application/json')):
                return {'error': f'o link não devolveu áudio ({ctype}) — gere um link de download novo'}
            name = _safe(title or os.path.splitext(os.path.basename(urllib.parse.urlparse(url).path))[0])
            dst = os.path.join(d, name + ext)
            size = int(r.headers.get('Content-Length') or 0)
            if not (os.path.exists(dst) and size and os.path.getsize(dst) == size):
                with open(dst + '.part', 'wb') as f:
                    shutil.copyfileobj(r, f, 1 << 20)
                os.replace(dst + '.part', dst)
    try:
        info = core.probe(dst)
    except Exception as e:
        return {'error': f'baixou, mas não é um áudio legível: {e}', 'path': core.nle_path(dst)}
    if not info.get('audio_streams'):
        return {'error': 'o arquivo baixado não tem áudio', 'path': core.nle_path(dst)}
    return {'path': core.nle_path(dst), 'duration': round(info['duration'], 2), 'folder': core.nle_path(d)}


# ───────────────────────────── fitting the song to the cut ─────────────────────────────
def audible(path, sr=8000, win=0.05):
    """(first, last) second where the song is above SILENT_DB — trailing silence/reverb tails cut off."""
    from claudio import montage
    x = montage._decode_mono(core.local_path(path), sr)
    k = int(sr * win)
    m = len(x) // k
    if not m:
        return 0.0, len(x) / sr
    db = 20 * np.log10(np.sqrt((x[:m * k].reshape(m, k) ** 2).mean(1)) + 1e-9)
    on = np.where(db > SILENT_DB)[0]
    if not len(on):
        return 0.0, len(x) / sr
    return float(on[0] * win), float(min(len(x) / sr, (on[-1] + 1) * win + 0.3))


def fit(path, duration, mode='auto', start=None):
    """Where the song plays on a cut of `duration` s, as segments {src_in, src_out, tl_in} (+ fade_in/fade_out):
      fim     its real ending lands on the end of the cut; it enters at 0 s (on a downbeat if one is that close,
              else faded in over 1 s)
      inicio  from the top of the song, faded out at the end of the cut
      janela  the best stretch (music_windows: downbeats, drop inside, ends on a section change), faded out
      auto    fim when the song is at most ~2.5× the cut, else janela
    A song shorter than the cut is chained from its first downbeat, the last pass ending on the cut's end.
    `start` (seconds of the song) forces where it starts."""
    from claudio import montage
    m = montage.music_map(path)
    a0, a1 = audible(path)
    db = [d for d in m['downbeats'] if a0 - 0.05 <= d < a1]
    D = a1 - a0
    if mode == 'auto':
        mode = 'fim' if D <= 2.5 * duration else 'janela'
    if start is not None:
        s = float(start)
        e = min(a1, s + duration)
        return {'mode': 'manual', 'segments': [{'src_in': round(s, 3), 'src_out': round(e, 3), 'tl_in': 0.0}],
                'fade_in': 0.05, 'fade_out': 1.5 if e < a1 - 0.5 else 0.0}
    if D < duration:                                     # chained passes: the last one ends the cut with the song's ending
        L = db[0] if db else a0
        P = a1 - L
        segs, t_end = [], duration
        while t_end > 0.01:
            tl_in = max(0.0, t_end - P)
            segs.insert(0, {'src_in': round(a1 - (t_end - tl_in), 3), 'src_out': round(a1, 3), 'tl_in': round(tl_in, 3)})
            t_end = tl_in
        return {'mode': 'repetida', 'segments': segs, 'fade_in': 1.0 if segs[0]['src_in'] > L + 0.05 else 0.05, 'fade_out': 0.0,
                'joins': [s['tl_in'] for s in segs[1:]]}
    if mode == 'fim':
        need = a1 - duration
        # a downbeat right at the needed start → enter on it; otherwise from 0 s with a 1 s fade-in (waiting for the next
        # downbeat left the first 2 s of the INC 9 Robson reel without music)
        cand = [d for d in db if need - 0.02 <= d <= need + ENTRY_SNAP]
        s = cand[0] if cand else max(a0, need)
        return {'mode': 'fim', 'segments': [{'src_in': round(s, 3), 'src_out': round(a1, 3), 'tl_in': round(s - need, 3)}],
                'fade_in': 0.05 if cand or s <= a0 + 0.05 else 1.0, 'fade_out': 0.0}
    if mode == 'inicio':
        s = a0
    else:
        w = montage.music_windows(m, duration, 1)
        s = w[0]['start'] if w else a0
    e = min(a1, s + duration)
    return {'mode': mode, 'segments': [{'src_in': round(s, 3), 'src_out': round(e, 3), 'tl_in': 0.0}],
            'fade_in': 0.05, 'fade_out': 2.0 if e < a1 - 0.5 else 0.0}


# ───────────────────────────── level + ducking ─────────────────────────────
def _lufs(x):
    from claudio import qc
    if x is None or not len(x) or not np.any(np.abs(x) > 1e-4):
        return None
    return qc.loudness(x)['lufs']


def levels(path, segs, tl, has_speech, speech_lufs=None):
    """Clip levels in dB: (under the speech, in the gaps, on an opening/closing card). Measured loudness of the part of the song that plays vs the
    speech in the cut; without speech the song alone goes to NO_SPEECH_LUFS."""
    from claudio import qc
    mus = np.concatenate([qc._clip_audio(path, s['src_in'], s['src_out']) for s in segs])
    mu = _lufs(mus)
    if speech_lufs is None and has_speech:
        try:
            _, stems = qc.mix_audio(tl)
            speech_lufs = _lufs(stems['speech'])
        except Exception:
            speech_lufs = None
    if mu is None:
        return 0.0, 0.0, 0.0, {'music_lufs': None, 'speech_lufs': speech_lufs}
    if not has_speech:
        g = min(MAX_GAIN, NO_SPEECH_LUFS - mu)
        return round(g, 1), round(g, 1), round(g, 1), {'music_lufs': mu, 'speech_lufs': None}
    sp = speech_lufs if speech_lufs is not None else -16.0          # speech not measurable (not indexed): a normal voice
    under = min(MAX_GAIN, sp - UNDER_SPEECH_LU - mu)
    up = min(MAX_GAIN, sp - GAP_LU - mu)
    ends = min(MAX_GAIN, sp - ENDS_LU - mu)
    return round(under, 1), round(up, 1), round(ends, 1), {'music_lufs': mu, 'speech_lufs': speech_lufs}


def envelope(spans, t0, t1, under, up, ends=None, cut=None):
    """Volume keyframes [(timeline s, dB)] between t0 and t1: `up` in the gaps, `under` while someone speaks; pauses
    shorter than MIN_GAP stay down. Before the first line / after the last one, when that stretch of the cut (`cut` =
    its start, end) is at least ENDS_MIN s — the opening, the closing card — the music goes to `ends`."""
    merged = []
    for a, b in spans:
        if b < t0 or a > t1:
            continue
        if merged and a - merged[-1][1] < MIN_GAP:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    if under == up or not merged:
        return [(t0, up), (t1, up)] if not merged else [(t0, under), (t1, under)]
    c0, c1 = cut or (t0, t1)
    head = ends if ends is not None and merged[0][0] - c0 >= ENDS_MIN else up
    tail = ends if ends is not None and c1 - merged[-1][1] >= ENDS_MIN else up
    k = []
    for n, (a, b) in enumerate(merged):
        if n == 0 and a - PRE - DOWN <= c0 + 0.3:          # the cut opens on the voice: down from the first frame
            k += [(c0, under)]
        else:
            k += [(a - PRE - DOWN, head if n == 0 else up), (a - PRE, under)]
        k += [(b + POST, under), (b + POST + UP, tail if n == len(merged) - 1 else up)]
    pts = [(t0, _at(k, t0, head))] + [(t, v) for t, v in k if t0 < t < t1] + [(t1, _at(k, t1, tail))]
    out = []
    for t, v in pts:                                    # drop the middle of a flat run
        if len(out) >= 2 and out[-1][1] == v and out[-2][1] == v:
            out[-1] = (t, v)
        else:
            out.append((t, v))
    return [(round(t, 3), round(v, 1)) for t, v in out]


def _at(keys, t, default):
    if not keys or t <= keys[0][0]:
        return keys[0][1] if keys and t >= keys[0][0] else default
    for (ta, va), (tb, vb) in zip(keys, keys[1:]):
        if ta <= t <= tb:
            return va if tb == ta else va + (vb - va) * (t - ta) / (tb - ta)
    return keys[-1][1]


def with_fades(keys, t0, t1, fi, fo):
    """Fade in over `fi` s from t0 and out over `fo` s to t1 on top of the duck keys."""
    if fi <= 0.06 and fo <= 0.06:
        return keys
    inner = [(t, v) for t, v in keys if t0 + fi < t < t1 - fo]
    a = [(t0, FLOOR_DB), (t0 + fi, _at(keys, t0 + fi, keys[0][1]))] if fi > 0.06 else [(t0, keys[0][1])]
    b = [(t1 - fo, _at(keys, t1 - fo, keys[-1][1])), (t1, FLOOR_DB)] if fo > 0.06 else [(t1, keys[-1][1])]
    return [(round(t, 3), round(v, 1)) for t, v in a + inner + b]


# ───────────────────────────── the plan the panel applies ─────────────────────────────
def plan_sequence(nle_xml, music, mode='auto', start=None, sequence_id=None, words=None, duck=True, gain_db=None):
    from claudio import nle_read
    tl = nle_read.read_fcp7(nle_xml)
    dur = tl['duration'] or max((i['tl_out'] for i in tl['items']), default=0)
    path = core.local_path(music)
    f = fit(path, dur, mode, start)
    spans, unindexed = speech_spans(nle_xml, tl, words) if duck else ([], [])
    under, up, ends, meas = levels(path, f['segments'], tl, bool(spans))
    if gain_db is not None:                              # the editor's own level: same duck depth around it
        g = float(gain_db)
        under, ends, up = round(g - (up - under), 1), round(min(MAX_GAIN, g + ends - up), 1), g
    segs = []
    for k, s in enumerate(f['segments']):
        t0, t1 = s['tl_in'], s['tl_in'] + s['src_out'] - s['src_in']
        keys = envelope(spans, t0, t1, under, up, ends, (0.0, dur))
        keys = with_fades(keys, t0, t1, f['fade_in'] if k == 0 else 0, f['fade_out'] if k == len(f['segments']) - 1 else 0)
        segs.append({**s, 'tl_out': round(t1, 3), 'keys': keys})
    ducked = sum(1 for a, b in spans if b > 0 and a < dur)
    return {'sequence': tl['name'], 'id': sequence_id, 'music': core.nle_path(path), 'title': os.path.splitext(os.path.basename(path))[0],
            'mode': f['mode'], 'segments': segs, 'joins': f.get('joins', []), 'under_db': under, 'gaps_db': up, 'ends_db': ends,
            'speech_lufs': meas['speech_lufs'], 'music_lufs': meas['music_lufs'], 'speech_spans': ducked,
            'not_indexed_speech': unindexed, 'duration': round(dur, 2)}


def plan(job, sequences, words=None):
    """One plan for several sequences: [{nle_xml, music, mode?, start?, sequence_id?, duck?, gain_db?}] →
    <job>/#trilhas/trilha <stamp>.claudio-trilha.jsx (the panel places it: new audio track per sequence, backups first)."""
    out = [plan_sequence(s['nle_xml'], s['music'], s.get('mode') or 'auto', s.get('start'), s.get('sequence_id'),
                         words, s.get('duck', True), s.get('gain_db')) for s in sequences]
    d = os.path.join(core.local_path(job), '#trilhas')
    os.makedirs(d, exist_ok=True)
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    p = os.path.join(d, f'trilha {stamp}.claudio-trilha.jsx')
    open(p, 'w', encoding='utf-8').write('var CLAUDIO_MUSIC = ' + json.dumps({'sequences': out}, ensure_ascii=False) + ';\n')
    summary = [{k: s[k] for k in ('sequence', 'title', 'mode', 'under_db', 'gaps_db', 'ends_db', 'speech_spans', 'joins', 'not_indexed_speech')}
               | {'starts_at': s['segments'][0]['tl_in'], 'song_from': s['segments'][0]['src_in']} for s in out]
    return {'plan': core.nle_path(p), 'sequences': summary,
            'next': 'escreva o caminho do plano na resposta: o painel coloca a música numa trilha de áudio NOVA de cada '
                    'sequência (backup antes), com o ducking em keyframes de Volume'}
