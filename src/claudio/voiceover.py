"""claudio.voiceover — an ElevenLabs narration and what the edit does with it, all as tools (the Premiere panel's chat
has no shell). Lessons from a documentary job where the narration was redone several times:

  take(text, ...)          one natural take PER PARAGRAPH (block) through /with-timestamps: <name>.mp3 + <name>_words.json
                           (word timings from the character alignment), a short <break> after every sentence, speed 1.0
                           (below 1.0 the voice chokes and runs sentences together — warned).
  fix_line(take, n, text)  one sentence generated again (previous/next text as context) and spliced into the take, in the
                           pauses around it; the words after it shift by the difference → <take>_fixN.wav + words.
  retime_plan(old, new, clips)  the PICTURE follows the new words (never chop the voice to fit the old cuts): each cut
                           keeps its anchor word and the same lead before it, snapped to the frame grid.
  clean_gaps(audio, words) mouth clicks ("tsc") and breaths silenced in the pauses between sentences, words untouched.
  lower_third(places, ...) city / state lower-thirds, ProRes 4444 with alpha, from a preset style.

The key is read from ~/.config/elevenlabs/api_key (or $ELEVENLABS_API_KEY) and never logged or returned.
"""
import base64, difflib, json, os, re, tempfile
from fractions import Fraction

from claudio import core

API = 'https://api.elevenlabs.io/v1'
MODEL = 'eleven_multilingual_v2'
SETTINGS = {'stability': 0.45, 'similarity_boost': 0.8, 'style': 0.3, 'use_speaker_boost': True}
BREAK = 0.55                 # seconds of pause after each sentence
SR = 48000


# ───────────────────────────── text ─────────────────────────────
def add_breaks(text, brk=BREAK):
    """A <break time="Xs" /> after every sentence (. ! ? …) that is followed by more text."""
    text = re.sub(r'\s+', ' ', text.strip())
    if not brk:
        return text
    return re.sub(r'([.!?…]["”»)]?) (?=\S)', lambda m: f'{m.group(1)} <break time="{float(brk):g}s" /> ', text)


def sentences(text):
    return [s for s in re.split(r'(?<=[.!?…])["”»)]?\s+', re.sub(r'\s+', ' ', text.strip())) if s]


def paragraphs(text):
    return [re.sub(r'\s+', ' ', c).strip() for c in re.split(r'\n\s*\n', text.strip()) if c.strip()]


def words_from_alignment(al):
    """ElevenLabs' character alignment → [{w, s, e}], one entry per whitespace token; break tags dropped."""
    words, cur, s, e, tag = [], '', None, None, False

    def flush():
        nonlocal cur, s
        if cur:
            words.append({'w': cur, 's': round(s, 3), 'e': round(e, 3)})
        cur, s = '', None
    for ch, a, b in zip(al['characters'], al['character_start_times_seconds'], al['character_end_times_seconds']):
        if tag:
            tag = ch != '>'
            continue
        if ch == '<':
            flush()
            tag = True
        elif ch.isspace():
            flush()
        else:
            if s is None:
                s = a
            cur += ch
            e = b
    flush()
    return words


def respell(words, script):
    """Timings of what the voice read, spelling of the script (a brand read as it sounds keeps its written name)."""
    toks = script.split()
    if [core.norm(w['w']) for w in words] == [core.norm(t) for t in toks]:
        return [{**w, 'w': t} for w, t in zip(words, toks)]
    from claudio import motion
    return [{k: v for k, v in w.items() if k != 'act'} for w in motion._script_words(words, script, [0])]


def sentence_of_words(words, text):
    """For every word, the index of the sentence of `text` it belongs to (difflib-aligned, so a respelled or dropped
    word does not shift the rest)."""
    toks, sid = [], []
    for k, s in enumerate(sentences(text)):
        for t in s.split():
            toks.append(core.norm(t)); sid.append(k)
    out = [None] * len(words)
    sm = difflib.SequenceMatcher(None, [core.norm(w['w']) for w in words], toks, autojunk=False)
    for i, j, n in sm.get_matching_blocks():
        for d in range(n):
            out[i + d] = sid[j + d]
    last = 0
    for i, v in enumerate(out):
        out[i] = last = (v if v is not None else last)
    return out


# ───────────────────────────── ElevenLabs ─────────────────────────────
def check_speed(speed):
    speed = 1.0 if speed is None else float(speed)
    if not 0.7 <= speed <= 1.2:
        raise ValueError('speed must be between 0.7 and 1.2 (ElevenLabs)')
    warn = [f'speed {speed:g} < 1.0: the voice tends to choke and run sentences together — prefer 1.0 and retime the '
            'picture to the words'] if speed < 1.0 else []
    return speed, warn


def tts(text, voice, speed=1.0, previous_text=None, next_text=None, model=MODEL, settings=None):
    """One request to /text-to-speech/{voice}/with-timestamps → (mp3 bytes, [{w, s, e}])."""
    import httpx
    key = core.elevenlabs_key()
    if not key:
        raise RuntimeError('sem chave da ElevenLabs (~/.config/elevenlabs/api_key)')
    body = {'text': text, 'model_id': model, 'voice_settings': {**SETTINGS, **(settings or {}), 'speed': speed}}
    if previous_text:
        body['previous_text'] = previous_text
    if next_text:
        body['next_text'] = next_text
    r = httpx.post(f'{API}/text-to-speech/{voice}/with-timestamps', params={'output_format': 'mp3_44100_128'}, json=body,
                   headers={'xi-api-key': key, 'User-Agent': 'claudio-narration'}, timeout=httpx.Timeout(300, connect=30))
    if r.status_code != 200:
        raise RuntimeError(f'ElevenLabs {r.status_code}: ' + r.text[:300].replace(key, '***'))
    d = r.json()
    al = d.get('alignment') or d.get('normalized_alignment')
    return base64.b64decode(d['audio_base64']), words_from_alignment(al)


def _said(out_dir, said):
    from claudio import motion
    return motion.pronunciation(out_dir) if said is None else said


def _read(text, said):
    from claudio import motion
    return motion.spoken(text, said) if said else text


def take(text, out_dir, voice, name='narration', speed=1.0, sentence_break=BREAK, budget=None, said=None, settings=None,
         model=MODEL):
    """One natural take per paragraph → <name>[_k].mp3 + <name>[_k]_words.json ({words, text, voice, …})."""
    if not voice:
        raise ValueError('voice (ElevenLabs voice_id) is required')
    out_dir = core.local_path(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    speed, warn = check_speed(speed)
    brk = BREAK if sentence_break is None else float(sentence_break)
    said = _said(out_dir, said)
    blocks = paragraphs(text)
    sent = [add_breaks(_read(b, said), brk) for b in blocks]
    chars = sum(len(s) for s in sent)
    if budget and chars > budget:
        raise ValueError(f'{chars} characters to send, over the budget of {budget}')
    takes = []
    for k, (b, s) in enumerate(zip(blocks, sent), 1):
        stem = name if len(blocks) == 1 else f'{name}_{k}'
        mp3, words = tts(s, voice, speed, model=model, settings=settings)
        audio = os.path.join(out_dir, stem + '.mp3')
        open(audio, 'wb').write(mp3)
        words = respell(words, b)
        dur = core.probe(audio)['duration']
        meta = {'words': words, 'text': b, 'voice': voice, 'model': model, 'settings': {**SETTINGS, **(settings or {})},
                'speed': speed, 'sentence_break': brk, 'dur': round(dur, 3)}
        wj = os.path.join(out_dir, stem + '_words.json')
        json.dump(meta, open(wj, 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
        takes.append({'audio': core.nle_path(audio), 'words': core.nle_path(wj), 'duration_s': round(dur, 2),
                      'n_words': len(words), 'sentences': len(sentences(b))})
    r = {'takes': takes, 'characters': chars, 'engine': 'elevenlabs',
         'info': f'{model} · speed {speed:g} · break {brk:g}s · one take per paragraph', 'warnings': warn,
         'next': 'clean_narration_gaps on each take; retime_plan to move the picture to the new words'}
    if len(takes) == 1:
        r.update(audio=takes[0]['audio'], words=takes[0]['words'], duration_s=takes[0]['duration_s'])
    return r


# ───────────────────────────── audio helpers ─────────────────────────────
def _pcm(path):
    """Any audio → float32 (n, 2) at 48 kHz."""
    import numpy as np
    raw = core.run(['ffmpeg', '-v', 'error', '-i', path, '-vn', '-ac', '2', '-ar', str(SR), '-f', 'f32le', '-']).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, 2).copy()


def _write_wav(x, path):
    import numpy as np
    core.run(['ffmpeg', '-v', 'error', '-y', '-f', 'f32le', '-ar', str(SR), '-ac', '2', '-i', '-', '-c:a', 'pcm_s24le', path],
             input=np.ascontiguousarray(x, np.float32).tobytes())


def words_path(audio):
    """The words file of a take: <stem>_words.json next to it, else words.json in its folder."""
    a = core.local_path(audio)
    for p in (os.path.splitext(a)[0] + '_words.json', os.path.join(os.path.dirname(a), 'words.json')):
        if os.path.exists(p):
            return p
    raise FileNotFoundError(f'no words file for {os.path.basename(a)} (expected {os.path.basename(os.path.splitext(a)[0])}_words.json)')


def load_words(src):
    """Words from a path, a JSON string, a list or a {words: […]} dict → (words, meta)."""
    if isinstance(src, str):
        s = src.strip()
        src = json.loads(s) if s[:1] in '[{' else json.load(open(core.local_path(s), encoding='utf-8'))
    if isinstance(src, dict):
        return [{'w': w['w'], 's': float(w['s']), 'e': float(w['e'])} for w in src['words']], src
    return [{'w': w['w'], 's': float(w['s']), 'e': float(w['e'])} for w in src], {}


def _free_name(path):
    stem, ext = os.path.splitext(path)
    k = 1
    while os.path.exists(f'{stem}{k}{ext}'):
        k += 1
    return f'{stem}{k}{ext}'


def _db(x):
    import numpy as np
    return 20 * np.log10(np.sqrt(np.mean(np.square(x))) + 1e-9) if len(x) else -120.0


# ───────────────────────────── one line again ─────────────────────────────
def fix_line(take_audio, line, new_text=None, voice=None, speed=None, said=None, generate=None):
    """Sentence `line` (1-based) of a take generated again and spliced in the pauses around it. The new line keeps the
    old one's lead and tail of pause, its loudness is matched (±6 dB), every word after it shifts by delta_s.
    generate(text, voice, speed, previous_text, next_text) → (audio bytes, words) — tests pass a fake."""
    import numpy as np
    src = core.local_path(take_audio)
    wj = words_path(src)
    ws, meta = load_words(wj)
    text = meta.get('text') or ' '.join(w['w'] for w in ws)
    sents = sentences(text)
    k = int(line) - 1
    if not 0 <= k < len(sents):
        raise ValueError(f'line must be 1..{len(sents)} (the take has {len(sents)} sentences)')
    sid = sentence_of_words(ws, text)
    idx = [i for i, v in enumerate(sid) if v == k]
    if not idx:
        raise ValueError(f'no words of sentence {line} found in the take timings')
    i0, i1 = idx[0], idx[-1]
    voice = voice or meta.get('voice')
    if not voice:
        raise ValueError('voice is required (the take has no voice recorded)')
    speed, warn = check_speed(speed if speed is not None else meta.get('speed', 1.0))
    new = re.sub(r'\s+', ' ', (new_text or sents[k]).strip())
    said = _said(os.path.dirname(src), said)
    gen = generate or (lambda t, v, sp, p, n: tts(t, v, sp, p, n, model=meta.get('model', MODEL), settings=meta.get('settings')))
    raw, nw = gen(add_breaks(_read(new, said), meta.get('sentence_break', BREAK)), voice, speed,
                  ' '.join(sents[:k]) or None, ' '.join(sents[k + 1:]) or None)
    nw = respell(nw, new)
    if not nw:
        raise RuntimeError('the new line came back without word timings')
    with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as f:
        f.write(raw)
    try:
        y = _pcm(f.name)
    finally:
        os.unlink(f.name)
    x = _pcm(src)
    n = len(x)
    cut_a = (ws[i0 - 1]['e'] + ws[i0]['s']) / 2 if i0 > 0 else 0.0
    cut_b = (ws[i1]['e'] + ws[i1 + 1]['s']) / 2 if i1 + 1 < len(ws) else n / SR
    lead, tail = ws[i0]['s'] - cut_a, cut_b - ws[i1]['e']
    p0, p1 = int(round((nw[0]['s'] - lead) * SR)), int(round((nw[-1]['e'] + tail) * SR))
    piece = np.zeros((p1 - p0, 2), np.float32)
    a, b = max(0, p0), min(len(y), p1)
    if b > a:
        piece[a - p0:b - p0] = y[a:b]
    old_voice = x[int(ws[i0]['s'] * SR):int(ws[i1]['e'] * SR)]
    new_voice = y[max(0, int(nw[0]['s'] * SR)):int(nw[-1]['e'] * SR)]
    gain = float(np.clip(_db(old_voice) - _db(new_voice), -6, 6))
    piece *= 10 ** (gain / 20)
    fade = min(int(0.01 * SR), len(piece) // 2)
    if fade:
        ramp = np.linspace(0, 1, fade, dtype=np.float32)[:, None]
        piece[:fade] *= ramp
        piece[-fade:] *= ramp[::-1]
    ia, ib = int(round(cut_a * SR)), int(round(cut_b * SR))
    out = np.concatenate([x[:ia], piece, x[ib:]])
    delta = (len(piece) - (ib - ia)) / SR
    shift = cut_a + lead - nw[0]['s']
    words = ws[:i0] + [{'w': w['w'], 's': round(w['s'] + shift, 3), 'e': round(w['e'] + shift, 3)} for w in nw] + \
            [{'w': w['w'], 's': round(w['s'] + delta, 3), 'e': round(w['e'] + delta, 3)} for w in ws[i1 + 1:]]
    stem = re.sub(r'_(fix\d+|clean)$', '', os.path.splitext(src)[0])
    dst = _free_name(stem + '_fix.wav')
    _write_wav(out, dst)
    sents[k] = new
    meta = {**meta, 'words': words, 'text': ' '.join(sents), 'voice': voice, 'speed': speed, 'dur': round(len(out) / SR, 3),
            'fixed': meta.get('fixed', []) + [{'line': k + 1, 'old': ' '.join(w['w'] for w in ws[i0:i1 + 1]), 'new': new,
                                               'delta_s': round(delta, 3)}]}
    dw = os.path.splitext(dst)[0] + '_words.json'
    json.dump(meta, open(dw, 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
    return {'audio': core.nle_path(dst), 'words': core.nle_path(dw), 'line': k + 1, 'new_text': new,
            'old_text': ' '.join(w['w'] for w in ws[i0:i1 + 1]), 'delta_s': round(delta, 3), 'after_s': round(cut_b, 3),
            'gain_db': round(gain, 1), 'duration_s': round(len(out) / SR, 2), 'warnings': warn,
            'next': f'everything after {cut_b:.2f}s moved {delta:+.3f}s: retime_plan(old_words={core.nle_path(wj)}, '
                    f'new_words={core.nle_path(dw)}, clips) to move the picture'}


# ───────────────────────────── picture follows the words ─────────────────────────────
def _fps(fps):
    return float(Fraction(str(fps)))


def retime_plan(old_words, new_words, clips, fps='24000/1001', offset=0.0):
    """Clips cut to an old narration → where they go on the new one. Each clip start keeps its anchor word (the first word
    starting at or after it) and the same lead before it; a clip end that met the next clip still meets it, an overlap
    (transition) keeps its length, any other end keeps the same tail after its last word. offset = where clip time 0 sits
    on the narration's clock (a nest that starts later in the narration). Times snap to the fps grid."""
    ow, _ = load_words(old_words)
    nw, _ = load_words(new_words)
    if not ow or not nw:
        raise ValueError('both word lists must have words')
    f = _fps(fps)
    half = 0.5 / f
    snap = lambda t: round(round(t * f) / f, 6)
    sm = difflib.SequenceMatcher(None, [core.norm(w['w']) for w in ow], [core.norm(w['w']) for w in nw], autojunk=False)
    m = {}
    for i, j, n in sm.get_matching_blocks():
        for d in range(n):
            m[i + d] = j + d
    if not m:
        raise ValueError('the two narrations share no words')
    keys = sorted(m)

    def fwd(i):          # i itself, else the next old word that exists in the new take, else the last one before
        return next((k for k in keys if k >= i), keys[-1])

    def back(i):
        return next((k for k in reversed(keys) if k <= i), keys[0])

    def map_start(t):
        i = next((k for k, w in enumerate(ow) if w['s'] >= t - 0.05), None)
        if i is None:
            j = back(len(ow) - 1)
            return nw[m[j]]['e'] + (t - ow[j]['e']), ow[j]['w']
        j = fwd(i)
        return nw[m[j]]['s'] - (ow[j]['s'] - t), ow[j]['w']

    def map_end(t):
        i = next((k for k in range(len(ow) - 1, -1, -1) if ow[k]['e'] <= t + 0.05), None)
        if i is None:
            j = fwd(0)
            return nw[m[j]]['s'] - (ow[j]['s'] - t)
        j = back(i)
        return nw[m[j]]['e'] + (t - ow[j]['e'])

    cl = [dict(c) for c in clips]
    for k, c in enumerate(cl):
        c.setdefault('label', str(k + 1))
        c['start'], c['end'] = float(c['start']), float(c['end'])
    rows = []
    for c in cl:
        if abs(c['start']) < half:
            ns, anchor = 0.0, None
        else:
            ns, anchor = map_start(c['start'] + offset)
            ns = max(0.0, ns - offset)
        rows.append({'c': c, 'new_start': snap(ns), 'anchor': anchor})
    order = sorted(range(len(cl)), key=lambda k: cl[k]['start'])
    for pos, k in enumerate(order):
        c, r = cl[k], rows[k]
        nxt = order[pos + 1] if pos + 1 < len(order) else None
        if nxt is not None and abs(c['end'] - cl[nxt]['start']) < half:
            ne = rows[nxt]['new_start']
        elif nxt is not None and c['end'] > cl[nxt]['start']:
            ne = rows[nxt]['new_start'] + (c['end'] - cl[nxt]['start'])
        else:
            ne = map_end(c['end'] + offset) - offset
        r['new_end'] = snap(max(ne, r['new_start'] + 1 / f))
    out = []
    for k, r in enumerate(rows):
        c = r['c']
        extra = {a: b for a, b in c.items() if a not in ('start', 'end')}
        dur, old = round(r['new_end'] - r['new_start'], 6), round(c['end'] - c['start'], 6)
        row = {**extra, 'old_start': c['start'], 'old_end': c['end'], 'new_start': r['new_start'], 'new_end': r['new_end'],
               'duration': dur, 'old_duration': old, 'moved_by': round(r['new_start'] - c['start'], 6), 'anchor': r['anchor'],
               'start_frame': round(r['new_start'] * f), 'duration_frames': round(dur * f)}
        if c.get('src_in') is not None:
            row['src_out'] = round(float(c['src_in']) + dur, 6)
        out.append(row)
    # safe order on one track: 1) shorten, 2) moves — left-movers left to right, then right-movers right to left,
    # 3) lengthen. No step ever runs a clip into one that has not moved yet.
    steps = []
    trim = lambda r: {'clip': r['label'], 'do': 'trim_out', 'duration': r['duration'],
                      **({'new_out_seconds': r['src_out']} if 'src_out' in r else {}), **_ids(r)}
    for r in out:
        if r['duration'] < r['old_duration'] - half:
            steps.append(trim(r))
    left = sorted((r for r in out if r['moved_by'] < -half), key=lambda r: r['old_start'])
    right = sorted((r for r in out if r['moved_by'] > half), key=lambda r: -r['old_start'])
    for r in left + right:
        steps.append({'clip': r['label'], 'do': 'move', 'new_start_seconds': r['new_start'], **_ids(r)})
    for r in out:
        if r['duration'] > r['old_duration'] + half:
            steps.append(trim(r))
    end = max(w['e'] for w in nw) - offset
    return {'clips': out, 'steps': steps, 'narration_end': round(end, 3), 'fps': round(f, 6),
            'words_changed': {'old_only': len(ow) - len(m), 'new_only': len(nw) - len(m)},
            'note': 'apply steps in this order (Premiere MCP move_clip / trim_clip: new_out_seconds is the SOURCE out); '
                    'times are on the clip/nest clock (offset already removed)'}


def _ids(r):
    return {k: r[k] for k in ('node_id', 'clip_id') if k in r}


# ───────────────────────────── clicks and breaths out of the pauses ─────────────────────────────
def clean_gaps(audio, words=None, out=None, min_gap=0.3, gain_db=0.0, length=None):
    """Silence the pauses between sentences (mouth clicks, breaths), words and timing untouched. In each pause longer than
    min_gap: from 60 ms after the last word to just after the last click (a 1–2 frame burst >= 8 dB over its
    surroundings) — or, without clicks, to 150 ms before the next word, so soft consonants (s, ch, f) keep their start;
    never past the true onset of the next word. 15 ms fades. → <stem>_clean.wav (48 kHz, 24-bit) + its words file."""
    import numpy as np
    src = core.local_path(audio)
    ws, meta = load_words(words if words else words_path(src))
    x = _pcm(src)
    n = len(x)
    W = SR // 100
    nf = max(0, (n - 1) // W)
    m = np.abs(x).max(1)[:nf * W].reshape(nf, W)
    db = (20 * np.log10(np.sqrt(np.mean(np.square(m), 1)) + 1e-9)).tolist()
    fade = int(0.015 * SR)
    ramp = np.linspace(0, 1, fade, dtype=np.float32)
    mask = np.ones(n, np.float32)
    done = []

    def ctx(s):           # median level of the frames around a 1–2 frame burst
        around = sorted(db[max(0, s - 4):max(0, s - 1)] + db[s + 2:s + 5])
        return around[len(around) // 2] if around else -120.0
    for i in range(1, len(ws)):
        g0, g1 = ws[i - 1]['e'], ws[i]['s']
        if g1 - g0 < min_gap:
            continue
        a = int((g0 + 0.06) * 100)
        k = max(a + 1, int((g1 - 0.15) * 100))          # true onset: 4 frames in a row above -42 dBFS (a lone click fails)
        while k < len(db) - 4 and not all(db[k + j] > -42 for j in range(4)):
            k += 1
        spikes = [s for s in range(a + 4, min(k - 4, len(db))) if db[s] > -46 and db[s] - ctx(s) >= 8]
        b = spikes[-1] + 3 if spikes else int((g1 - 0.15) * 100)
        b = min(b, k - 2)
        if b <= a:
            continue
        s0, s1 = min(n, a * W), min(n, b * W)
        mask[s0:s1] = 0
        lo, hi = max(0, s0 - fade), min(n, s1 + fade)
        mask[lo:s0] = np.minimum(mask[lo:s0], ramp[::-1][fade - (s0 - lo):])     # fade out into the pause
        mask[s1:hi] = np.minimum(mask[s1:hi], ramp[:hi - s1])                    # fade back in before the word
        done.append({'from': round(a / 100, 2), 'to': round(b / 100, 2), 'clicks': len(spikes),
                     'between': f"{ws[i - 1]['w']} | {ws[i]['w']}"})
    y = x * mask[:, None] * (10 ** (float(gain_db) / 20))
    if length:
        L = int(float(length) * SR)
        y = np.concatenate([y, np.zeros((max(0, L - len(y)), 2), np.float32)])[:L]
    y = np.clip(y, -0.89, 0.89)
    stem = re.sub(r'_clean$', '', os.path.splitext(src)[0])
    dst = core.local_path(out) if out else stem + '_clean.wav'
    _write_wav(y, dst)
    dw = os.path.splitext(dst)[0] + '_words.json'
    json.dump({**meta, 'words': ws, 'cleaned_from': os.path.basename(src)} if meta else ws,
              open(dw, 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
    return {'audio': core.nle_path(dst), 'words': core.nle_path(dw), 'silenced': done, 'pauses_cleaned': len(done),
            'duration_s': round(len(y) / SR, 2)}


# ───────────────────────────── lower-thirds ─────────────────────────────
FONTS = os.path.join(os.path.dirname(__file__), 'fonts')
# coordinates on a 1080-line frame (x from the left, y from the top), scaled to the sequence size
LOWER_THIRDS = {
    'territorios': {   # cream Montserrat, soft blurred dark plate (reads on light skies), small slide-in, alpha fades
        'title_font': 'Montserrat-SemiBold.ttf', 'sub_font': 'Montserrat-Medium.ttf', 'color': '0xF1E8D0',
        'title_size': 58, 'sub_size': 28, 'sub_upper': True, 'x': 135, 'title_y': 868, 'sub_y': 938, 'slide': 18,
        'slide_s': 0.6, 'plate': (40, 820, 640, 200, 0.42, 60), 'bar': (110, 872, 5, 92), 'shadow': 0.45,
        'fade_in': 0.5, 'fade_out': 0.6, 'boxes': False},
}
# the delivered look is text + shadow only: a drawbox on a transparent frame leaves alpha at 0, so the plate and bar of
# the original script never showed. This variant draws them for real (soft dark plate, cream bar beside the text).
LOWER_THIRDS['territorios_placa'] = {**LOWER_THIRDS['territorios'], 'boxes': True}


def _esc(p):
    """A path inside an ffmpeg filter option: forward slashes, ':' escaped, quoted."""
    return "'" + p.replace('\\', '/').replace(':', '\\:').replace("'", "'\\''") + "'"


def _place(p):
    """'Magé | Rio de Janeiro' or {title, subtitle[, name]} (city/state accepted too)."""
    if isinstance(p, str):
        t, _, s = p.partition('|')
        return {'title': t.strip(), 'subtitle': s.strip(), 'name': None}
    return {'title': str(p.get('title') or p.get('city') or '').strip(),
            'subtitle': str(p.get('subtitle') or p.get('state') or '').strip(), 'name': p.get('name')}


def lower_third(places, out_dir, size='1920x1080', fps='24000/1001', duration=4.0, preset='territorios'):
    """Lower-thirds (title + subtitle, e.g. city + state) as ProRes 4444 with alpha: one LT_<name>.mov each + a preview PNG."""
    if preset not in LOWER_THIRDS:
        raise ValueError(f'preset must be one of {sorted(LOWER_THIRDS)}')
    P = LOWER_THIRDS[preset]
    W, H = (int(v) for v in str(size).lower().split('x'))
    sc = min(W, H) / 1080
    X = lambda v: max(1, round(v * sc))
    Y = lambda v: round(H - (1080 - v) * sc)
    out_dir = core.local_path(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    D = float(duration)
    tf, sf = os.path.join(FONTS, P['title_font']), os.path.join(FONTS, P['sub_font'])
    px, py, pw, ph, pa, blur = P['plate']
    bx, by, bw, bh = P['bar']
    sh = f"shadowcolor=black@{P['shadow']}:shadowx={X(2)}:shadowy={X(2)}"
    slide = f"({X(P['x'])}-{X(P['slide'])}*(1-min(t/{P['slide_s']:g}\\,1)))"     # small slide-in from the left
    res = []
    for p in map(_place, places):
        if not p['title']:
            continue
        sub = p['subtitle'].upper() if P['sub_upper'] else p['subtitle']
        key = re.sub(r'[^\w-]+', '_', p['name']) if p['name'] else (''.join(core.norm(t).title() for t in p['title'].split()) or 'LT')
        est = max(len(p['title']) * P['title_size'] * 0.62, len(sub) * P['sub_size'] * 0.72)   # the plate grows with the text
        w = max(pw, P['x'] - px + est + 70)
        tmp = tempfile.mkdtemp(prefix='lt-')
        t1, t2 = os.path.join(tmp, 'title.txt'), os.path.join(tmp, 'sub.txt')
        open(t1, 'w', encoding='utf-8').write(p['title'])
        open(t2, 'w', encoding='utf-8').write(sub)
        graph = (
            f"color=c=black@0.0:s={W}x{H}:r={fps}:d={D:g},format=rgba,"
            + (f"drawbox=x={X(px)}:y={Y(py)}:w={X(w)}:h={X(ph)}:color=black@{pa}:t=fill:replace=1,boxblur={X(blur)}:2,"
               f"drawbox=x={X(bx)}:y={Y(by)}:w={X(bw)}:h={X(bh)}:color={P['color']}@1:t=fill:replace=1,"
               if P['boxes'] else '') +
            f"drawtext=fontfile={_esc(tf)}:textfile={_esc(t1)}:fontsize={X(P['title_size'])}:fontcolor={P['color']}:"
            f"x={slide}:y={Y(P['title_y'])}:{sh},"
            f"drawtext=fontfile={_esc(sf)}:textfile={_esc(t2)}:fontsize={X(P['sub_size'])}:fontcolor={P['color']}:"
            f"x={slide}+{X(2)}:y={Y(P['sub_y'])}:{sh},"
            f"fade=t=in:st=0:d={P['fade_in']:g}:alpha=1,fade=t=out:st={D - P['fade_out']:g}:d={P['fade_out']:g}:alpha=1")
        dst = os.path.join(out_dir, f'LT_{key}.mov')
        prev = os.path.join(out_dir, f'_prev_LT_{key}.png')
        try:
            core.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i', graph, '-c:v', 'prores_ks', '-profile:v', '4444',
                      '-pix_fmt', 'yuva444p10le', '-alpha_bits', '16', dst])
            core.run(['ffmpeg', '-y', '-v', 'error', '-ss', f'{min(1.5, D / 2):g}', '-i', dst, '-frames:v', '1', prev])
        finally:
            for t in (t1, t2):
                os.unlink(t)
            os.rmdir(tmp)
        res.append({'title': p['title'], 'subtitle': p['subtitle'], 'overlay': core.nle_path(dst), 'preview': core.nle_path(prev)})
    return {'lower_thirds': res, 'preset': preset, 'size': f'{W}x{H}', 'fps': str(fps), 'duration_s': D,
            'alpha': 'ProRes 4444 (yuva444p10le)',
            'place_at': 'a NEW empty video track above every used one, by overwrite, where the place first appears; '
                        'look at the preview PNG first'}
