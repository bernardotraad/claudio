"""claudio.montage — from logged footage to a real, reviewable cut.

  music_map       tempo, beat grid, bar downbeats, energy sections, drop, quiet bars (music first: the song sets the cuts)
  music_windows   which stretch of the song fits the requested duration (starts on a downbeat, lands the drop)
  sync_audio      offset between a camera clip and an external recorder file (dual-system sound)
  reframe         sharpness-weighted subject position → crop for 9:16 / 1:1 / 4:5 from 16:9 (and a preview image)
  fit_edit        ordered shots + pace → timeline where every cut lands on the beat grid (dialogue keeps its length)
  render_edit     segments → concat → music (ducked under dialogue) + captions + titles + loudness → MP4 + storyboard
  export_edit     the same cut as FCP7 XML (music track, audio levels, reframe) for Premiere / Resolve finishing
"""
import json, math, os, re, subprocess, tempfile
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction

import numpy as np
from PIL import Image, ImageDraw

from claudio import core

MSR = 22050
MHOP = 512
ONSET_LAG = 0.0185
ASPECTS = {'16:9': (16, 9), '9:16': (9, 16), '1:1': (1, 1), '4:5': (4, 5), '4:3': (4, 3)}
RES = {'final': {'16:9': (1920, 1080), '9:16': (1080, 1920), '1:1': (1080, 1080), '4:5': (1080, 1350), '4:3': (1440, 1080)},
       'preview': {'16:9': (1280, 720), '9:16': (540, 960), '1:1': (720, 720), '4:5': (720, 900), '4:3': (960, 720)}}
PACE_BEATS = {'calm': 4, 'medium': 2, 'fast': 1, 'hype': 0.5}


# ───────────────────────────── music ─────────────────────────────
def _decode_mono(path, sr, start=0.0, dur=None):
    cmd = ['ffmpeg', '-v', 'error', '-ss', f'{start:.3f}', '-i', path] + (['-t', f'{dur:.3f}'] if dur else []) + \
          ['-ac', '1', '-ar', str(sr), '-f', 's16le', '-']
    return np.frombuffer(core.run(cmd).stdout, np.int16).astype(np.float32) / 32768


def _beat_track(onset, fps_o, bpm_hint=None):
    """Tempo by autocorrelation (log-Gaussian prior around 120 BPM) + Ellis dynamic-programming beat tracker."""
    o = onset - onset.mean()
    n = len(o)
    ac = np.fft.irfft(np.abs(np.fft.rfft(o, 2 * n)) ** 2)[:n]
    lags = np.arange(n)
    with np.errstate(divide='ignore'):
        bpm = 60 * fps_o / np.maximum(lags, 1e-9)
    center = bpm_hint or 120
    prior = np.exp(-0.5 * (np.log2(np.maximum(bpm, 1e-9) / center) / 0.9) ** 2)
    valid = (bpm >= 60) & (bpm <= 190)
    score = np.where(valid, ac * prior, -np.inf)
    L = int(np.argmax(score))
    if 1 <= L < n - 1:   # parabolic refinement
        a, b, c = ac[L - 1], ac[L], ac[L + 1]
        L = L + 0.5 * (a - c) / (a - 2 * b + c + 1e-12)
    period = float(L)
    # DP beat tracking
    loc = onset / (onset.std() + 1e-9)
    cum = loc.copy()
    back = -np.ones(n, int)
    lo, hi = int(round(period / 2)), int(round(2 * period))
    alpha = 100.0
    for t in range(hi, n):
        prev = np.arange(t - hi, t - lo + 1)
        pen = -alpha * np.log((t - prev) / period) ** 2
        k = int(np.argmax(cum[prev] + pen))
        cum[t] = loc[t] + cum[prev[k]] + pen[k]
        back[t] = prev[k]
    tail = np.arange(max(0, n - int(period)), n)
    t = int(tail[np.argmax(cum[tail])])
    beats = []
    while t >= 0:
        beats.append(t)
        t = back[t]
    beats = np.array(beats[::-1])
    # extend backwards to the start with the period (DP starts at 2 periods)
    while len(beats) and beats[0] - period > 0:
        beats = np.r_[int(round(beats[0] - period)), beats]
    return 60 * fps_o / period, beats


def _refine_beats(onset, bf, reach=2):
    """Each beat frame moved to the onset peak within ±reach frames, interpolated between frames. Beats with no real
    onset nearby (a held note, a break) keep the tracker's position."""
    if not len(bf):
        return np.asarray(bf, float)
    ref = float(np.median([onset[f] for f in bf])) or 0.0
    out = []
    for f in bf:
        lo, hi = max(1, f - reach), min(len(onset) - 2, f + reach)
        if hi < lo:
            out.append(float(f)); continue
        k = lo + int(np.argmax(onset[lo:hi + 1]))
        if onset[k] < 0.3 * ref:
            out.append(float(f)); continue
        a, b, c = onset[k - 1], onset[k], onset[k + 1]
        den = a - 2 * b + c
        d = 0.5 * (a - c) / den if den else 0.0
        out.append(k + max(-0.5, min(0.5, d)))
    return np.array(out)


def music_map(path, start=0.0, end=None):
    """Full map of a song (cached). Times are seconds of the source file."""
    path = core.local_path(path)
    d = core.cache_dir(path)
    key = f'music2_{start:.2f}_{end or 0:.2f}.json'          # v2: refined beats (sub-frame, lag-corrected)
    mp = os.path.join(d, key)
    if os.path.exists(mp):
        return json.load(open(mp))
    x = _decode_mono(path, MSR, start, (end - start) if end else None)
    n_fft = 2048
    frames = 1 + max(0, (len(x) - n_fft) // MHOP)
    win = np.hanning(n_fft).astype(np.float32)
    S = np.empty((frames, n_fft // 2 + 1), np.float32)
    for i in range(0, frames, 2048):          # chunked STFT keeps memory flat on long songs
        idx = np.arange(i, min(frames, i + 2048))[:, None] * MHOP + np.arange(n_fft)[None]
        S[i:i + len(idx)] = np.abs(np.fft.rfft(x[idx] * win, axis=1))
    freqs = np.fft.rfftfreq(n_fft, 1 / MSR)
    logS = np.log1p(10 * S)
    flux = np.maximum(np.diff(logS, axis=0), 0)
    onset = np.r_[0, flux.sum(1)]
    low = np.r_[0, flux[:, freqs < 180].sum(1)]
    k = max(1, int(0.4 * MSR / MHOP))
    onset = np.maximum(onset - np.convolve(onset, np.ones(k) / k, 'same'), 0)
    fps_o = MSR / MHOP
    bpm, bf = _beat_track(onset, fps_o)
    # the tracker works on whole onset frames, so its period is a whole number of frames and the grid drifts against the
    # real tempo (up to ~30 ms on a 128 BPM click): move each beat to its onset peak, with sub-frame (parabolic) precision
    bff = _refine_beats(onset, bf)
    # frame time = window centre, plus the measured lag of the flux peak behind the transient (constant ~18.5 ms for
    # clicks of 2–20 ms and noise bursts alike, calibrated in tests/test_beats.py)
    beats = (bff * MHOP + n_fft / 2) / MSR + ONSET_LAG
    if len(beats) >= 8:
        bpm = 60.0 / float(np.median(np.diff(beats)))
    rms = np.sqrt((S ** 2).mean(1)) + 1e-9
    db = 20 * np.log10(rms)
    # downbeats: the phase (of 4) whose beats carry the most low-end onset energy
    lowb = np.array([low[max(0, f - 2):f + 3].max() for f in bf]) if len(bf) else np.zeros(0)
    phase = int(np.argmax([lowb[p::4].mean() if len(lowb[p::4]) else 0 for p in range(4)])) if len(bf) >= 8 else 0
    downbeats = beats[phase::4]
    # bars → energy levels → sections
    bars = []
    for a_, b_ in zip(downbeats, list(downbeats[1:]) + [len(x) / MSR]):
        fa, fb = int(a_ * fps_o), max(int(a_ * fps_o) + 1, int(b_ * fps_o))
        bars.append([float(a_), float(b_), float(np.mean(db[fa:fb]))])
    e = np.array([b_[2] for b_ in bars]) if bars else np.zeros(0)
    if len(e) >= 3:
        es = np.array([np.median(e[max(0, i - 1):i + 2]) for i in range(len(e))])
        q1, q2 = np.percentile(es, [33, 66])
        lev = ['low' if v < q1 else 'high' if v >= q2 else 'mid' for v in es]
        # merge single-bar blips
        for i in range(1, len(lev) - 1):
            if lev[i - 1] == lev[i + 1] != lev[i]:
                lev[i] = lev[i - 1]
    else:
        es, lev = e, ['mid'] * len(e)
    sections = []
    for (a_, b_, _), l_ in zip(bars, lev):
        if sections and sections[-1]['level'] == l_:
            sections[-1]['end'] = round(b_ + start, 3); sections[-1]['bars'] += 1
        else:
            sections.append({'start': round(a_ + start, 3), 'end': round(b_ + start, 3), 'level': l_, 'bars': 1})
    drop = None
    if len(es) >= 8:   # the drop: biggest bar-energy jump after at least 4 bars of music (not the song simply starting)
        first = next((i for i, v in enumerate(es) if v > np.median(es) - 6), 0) + 4
        rise = [(es[i:i + 2].mean() - es[i - 2:i].mean(), i) for i in range(max(first, 2), len(es) - 1)]
        if rise:
            r, i = max(rise)
            if r > 2.5:
                drop = round(bars[i][0] + start, 3)
    med = float(np.median(e)) if len(e) else 0
    quiet = [[round(a_ + start, 3), round(b_ + start, 3)] for a_, b_, v in bars if v < med - 10]
    out = {'path': path, 'nle_path': core.nle_path(path), 'start': start, 'duration': round(len(x) / MSR, 3),
           'bpm': round(float(bpm), 1), 'beat_sec': round(60 / bpm, 4), 'meter': '4/4 (assumed)',
           'bpm_alternatives': {'half_time': round(float(bpm) / 2, 1), 'double_time': round(float(bpm) * 2, 1),
                                'note': 'tempo octave is ambiguous: for a half-time feel cut on downbeats or use 2x the beats'},
           'beats': [round(float(b_) + start, 4) for b_ in beats],
           'downbeats': [round(float(b_) + start, 4) for b_ in downbeats],
           'bar_energy_db': [round(float(v), 1) for v in e], 'sections': sections, 'drop': drop, 'quiet_bars': quiet}
    json.dump(out, open(mp, 'w'))
    return out


def music_summary(m):
    return {k: m[k] for k in ('nle_path', 'duration', 'bpm', 'bpm_alternatives', 'beat_sec', 'meter', 'sections', 'drop', 'quiet_bars')} | {
        'beats': len(m['beats']), 'first_downbeats': m['downbeats'][:8]}


def music_windows(m, duration, n=3):
    """Best stretches of the song for a cut of `duration` s: start on a downbeat, end on a downbeat near the target,
    prefer ending at a section boundary and containing the drop in its first 20–60 %."""
    db = m['downbeats']
    bounds = {round(s['end'], 2) for s in m['sections']} | {round(s['start'], 2) for s in m['sections']}
    lev = {'low': 0, 'mid': 1, 'high': 2}
    cands = []
    for i, a in enumerate(db):
        ends = [b for b in db[i + 1:] if abs(b - a - duration) <= max(2 * m['beat_sec'] * 4, 0.12 * duration)]
        for b in ends:
            score = -abs(b - a - duration) / duration
            if any(abs(b - x) < m['beat_sec'] for x in bounds): score += 1
            if any(abs(a - x) < m['beat_sec'] for x in bounds): score += 0.5
            if m['drop'] and a + 0.2 * (b - a) <= m['drop'] <= a + 0.6 * (b - a): score += 1.5
            secs = [s for s in m['sections'] if s['start'] < b and s['end'] > a]
            score += 0.3 * np.mean([lev[s['level']] for s in secs]) if secs else 0
            if any(q[0] <= a < q[1] for q in m['quiet_bars']): score -= 1
            cands.append({'start': round(a, 3), 'end': round(b, 3), 'len': round(b - a, 2), 'score': round(float(score), 2),
                          'contains_drop': bool(m['drop'] and a <= m['drop'] < b),
                          'sections': [f"{s['level']} {s['start']:.1f}-{s['end']:.1f}" for s in secs]})
    cands.sort(key=lambda c: -c['score'])
    out = []
    for c in cands:
        if all(c['end'] <= o['start'] or c['start'] >= o['end'] or abs(c['start'] - o['start']) > 4 * m['beat_sec'] * 4 for o in out):
            out.append(c)
        if len(out) == n:
            break
    return out


# ───────────────────────────── dual-system sound ─────────────────────────────
def sync_audio(video, audio, max_offset=120.0):
    """Offset so that audio_time = video_time + offset (cross-correlation of 8 kHz high-passed signals)."""
    v = core.local_path(video); a = core.local_path(audio)
    sr = 8000
    x = _decode_mono(v, sr); y = _decode_mono(a, sr)
    hp = lambda s: s - np.convolve(s, np.ones(16) / 16, 'same')
    x, y = hp(x), hp(y)
    n = 1 << int(math.ceil(math.log2(len(x) + len(y))))
    cc = np.fft.irfft(np.fft.rfft(y, n) * np.conj(np.fft.rfft(x, n)), n)
    lim = min(int(max_offset * sr), n // 2)
    cand = np.r_[cc[:lim], cc[-lim:]]
    lags = np.r_[np.arange(lim), np.arange(-lim, 0)]
    i = int(np.argmax(np.abs(cand)))
    peak = abs(cand[i])
    mask = np.abs(lags - lags[i]) > sr * 0.05
    second = float(np.abs(cand[mask]).max()) if mask.any() else 1e-9
    off = lags[i] / sr
    return {'offset_sec': round(float(off), 4), 'confidence': round(float(peak / (second + 1e-9)), 2),
            'meaning': 'audio_time = video_time + offset_sec', 'polarity_inverted': bool(cand[i] < 0),
            'reliable': bool(peak / (second + 1e-9) > 1.5)}


# ───────────────────────────── reframe ─────────────────────────────
def crop_box(sw, sh, aspect, x=0.5, y=0.5, zoom=1.0):
    aw, ah = ASPECTS[aspect]
    if sw / sh > aw / ah:
        ch = sh / zoom; cw = ch * aw / ah
    else:
        cw = sw / zoom; ch = cw * ah / aw
    cw, ch = min(cw, sw), min(ch, sh)
    cx = (sw - cw) * min(max(x, 0), 1); cy = (sh - ch) * min(max(y, 0), 1)
    return int(cx) // 2 * 2, int(cy) // 2 * 2, int(cw) // 2 * 2, int(ch) // 2 * 2


def fcp_center(ow, oh, cx, cy, cw, ch, scale, info):
    """Basic Motion centre of a crop, as Premiere reads it: the picture's offset from the frame centre in SEQUENCE
    pixels divided by the source's own frame size (horiz × width, vert × height of the file — measured on Premiere's
    import and export, 2026-10-30). The crop is in oriented pixels; scale (%) takes it to the sequence."""
    dx = ((ow / 2) - (cx + cw / 2)) * scale / 100
    dy = ((oh / 2) - (cy + ch / 2)) * scale / 100
    return dx / info['width'], dy / info['height']


def suggest_reframe(idx, s, e, aspect, n=5):
    """Where the in-focus subject is: centre of mass of local sharpness (shallow depth of field makes the subject the
    sharpest thing), averaged over n frames, converted to the crop's x/y (0 = left/top, 1 = right/bottom)."""
    info = idx['info']
    fr = core.grab_many(info['path'], s, e, n, 320)
    xs, ys = [], []
    for _, im in fr:
        g = np.asarray(im.convert('L'), np.float32)
        lap = np.abs(-4 * g[1:-1, 1:-1] + g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:])
        lap = np.where(lap > np.percentile(lap, 90), lap, 0)
        if lap.sum() <= 0:
            continue
        yy, xx = np.mgrid[0:lap.shape[0], 0:lap.shape[1]]
        xs.append(float((xx * lap).sum() / lap.sum() / lap.shape[1]))
        ys.append(float((yy * lap).sum() / lap.shape[0] / lap.sum()))
    sx = float(np.median(xs)) if xs else 0.5
    sy = float(np.median(ys)) if ys else 0.5
    sw, sh = core.oriented(info)
    _, _, cw, ch = crop_box(sw, sh, aspect)
    def to_param(c, full, crop):
        return 0.5 if full - crop < 2 else min(max((c * full - crop / 2) / (full - crop), 0), 1)
    return {'subject_x': round(sx, 3), 'subject_y': round(sy, 3), 'reframe_x': round(to_param(sx, sw, cw), 3),
            'reframe_y': round(to_param(sy, sh, ch), 3)}


def auto_grade(idx, s, e, target=0.36):
    """Preview-only exposure fix: gamma that brings the shot's mean luma to `target` (dark/log footage), a touch of
    contrast/saturation when it was lifted a lot. The real grade belongs in the NLE."""
    fr = core.grab_many(idx['info']['path'], s, max(e, s + 0.1), 3, 160)
    if not fr:
        return None
    lum = float(np.mean([np.asarray(im.convert('L'), np.float32).mean() / 255 for _, im in fr]))
    lum = min(max(lum, 0.02), 0.95)
    gamma = min(max(math.log(lum) / math.log(target), 0.7), 2.4)    # eq gamma: out = in^(1/gamma)
    if abs(gamma - 1) < 0.08:
        return None
    lifted = gamma > 1.3                      # a big lift flattens the image: give contrast and colour back
    return {'gamma': round(gamma, 3), 'contrast': 1.15 if lifted else 1.03,
            'saturation': 1.15 if lifted else 1.0, 'measured_luma': round(lum, 3)}


def multicam_grid(sync, start, end, n=6, width=300, aspect=None):
    """Multicam monitor: one row per angle, one column per moment in [start, end] of MASTER time — every tile is
    what that camera saw at that instant (black = angle not rolling). Pick angles per bar by looking."""
    rows = [r for r in sync if r.get('reliable') and r['master_out'] > start and r['master_in'] < end]
    ts = [start + (k + 0.5) * (end - start) / n for k in range(n)]
    tiles = []
    for r in rows:
        idx = core.light_index(r['path'] if 'path' in r else r['nle_path'])
        row = []
        for t in ts:
            ct = t - r['offset']
            if 0 <= ct < r['duration']:
                im = core.grab(idx['info']['path'], ct, width)
            else:
                h = int(width * 9 / 16)
                im = Image.new('RGB', (width, h), (8, 8, 8))
            core._label(im, [f"{r['clip']}", f"master {t:.1f}s"], core._font(12))
            row.append(im)
        tiles.append(row)
    if not tiles:
        raise ValueError('no reliable angle covers that range')
    h = max(im.height for row in tiles for im in row)
    out = Image.new('RGB', (width * n, h * len(tiles)), (0, 0, 0))
    for y, row in enumerate(tiles):
        for x, im in enumerate(row):
            out.paste(im, (x * width, y * h))
    return out, [round(t, 2) for t in ts]


def crop_preview(idx, times, aspect, x=None, y=None, zoom=1.0):
    info = idx['info']; clk = core.clock_of(info)
    sug = suggest_reframe(idx, min(times), max(times) + 0.01, aspect, n=min(len(times), 5))
    x = sug['reframe_x'] if x is None else x
    y = sug['reframe_y'] if y is None else y
    ims = []
    for t in times[:4]:
        im = core.grab(info['path'], t, 640)
        ow, oh = core.oriented(info)
        sc = im.width / ow
        cx, cy, cw, ch = crop_box(ow, oh, aspect, x, y, zoom)
        dim = Image.new('RGB', im.size, (0, 0, 0))
        mask = Image.new('L', im.size, 150)
        ImageDraw.Draw(mask).rectangle([cx * sc, cy * sc, (cx + cw) * sc, (cy + ch) * sc], fill=0)
        im = Image.composite(dim, im, mask)
        ImageDraw.Draw(im).rectangle([cx * sc, cy * sc, (cx + cw) * sc - 1, (cy + ch) * sc - 1], outline=(255, 220, 0), width=2)
        core._label(im, [clk.stamp(t), f'{aspect} x={x:.2f} y={y:.2f} zoom={zoom:g}'], core._font(14))
        ims.append(im)
    w, h = ims[0].size
    cols = min(2, len(ims))
    out = Image.new('RGB', (w * cols, h * math.ceil(len(ims) / cols)))
    for k, im in enumerate(ims):
        out.paste(im, ((k % cols) * w, (k // cols) * h))
    return out, {'reframe_x': x, 'reframe_y': y, 'zoom': zoom, 'suggested': sug}


# ───────────────────────────── edit fitting ─────────────────────────────
def music_for(mu):
    """The map an edit uses: whole song, or only the used stretch of a long master recording."""
    if mu.get('master'):
        # map with context (±45 s, snapped to 15 s so nearby edits share the cache): bar phase needs a long window
        a = max(0.0, math.floor((float(mu['start']) - 45) / 15) * 15)
        b = math.ceil((float(mu.get('end') or mu['start'] + 300) + 45) / 15) * 15
        return music_map(mu['path'], float(a), float(b))
    return music_map(mu['path'])


def _grid(edit, m, fps):
    if not m or edit.get('cut_on', 'beat') == 'none':
        return None
    ms, me = edit['music']['start'], edit['music'].get('end') or (m['start'] + m['duration'])
    src = m['beats']
    if edit.get('cut_on') in ('downbeat', 'bar'):
        # bars are counted from the beat where the cut starts (detected downbeat phase is fragile on syncopated music)
        k0 = int(np.argmin([abs(b - ms) for b in m['beats']]))
        src = m['beats'][k0 % 4::4]
    pts = [b - ms for b in src if ms - 1e-3 <= b <= me + 1e-3]
    if edit.get('cut_on') == 'half':
        pts = sorted(pts + [(a + b) / 2 for a, b in zip(pts, pts[1:])])
    return sorted({int(round(p * fps)) for p in pts} | {0})


def _level_at(m, t_music):
    for s in (m or {}).get('sections', []):
        if s['start'] <= t_music < s['end']:
            return s['level']
    return 'mid'


def split_on_pauses(path, s_in, s_out, max_pause=0.45, pad=0.12, lang='pt'):
    """Pieces of [s_in, s_out) with every silence between words longer than max_pause cut out (pad kept on each side):
    the "remove long breaths" pass. Returns [(in, out), …]; one piece when there is nothing to cut."""
    idx = core.build_index(path, lang)
    ws = [w for w in core.load_words(idx) if w['e'] > s_in and w['s'] < s_out]
    if len(ws) < 2:
        return [(s_in, s_out)]
    pieces, a = [], s_in
    for w0, w1 in zip(ws, ws[1:]):
        if w1['s'] - w0['e'] > max_pause:
            pieces.append((a, min(s_out, w0['e'] + pad)))
            a = max(s_in, w1['s'] - pad)
    pieces.append((a, s_out))
    return [(round(x, 3), round(y, 3)) for x, y in pieces if y - x > 0.2]


def fit_edit(edit, lang='pt'):
    """Resolve an edit spec into a frame-accurate timeline. Returns the fitted edit (shots with tl/src frames) and warnings.
    Shot option trim_pauses=<seconds>: cut silences longer than that inside the shot (jump cuts — cover them with
    b-roll). Overlay runs take layer=2,3,4… (video track); stills=[{path, from, to, layer}] lay images (a logo) over a
    span. Empty layers in between stay as empty tracks (e.g. V3 left for the editor's adjustment layers)."""
    if any(sh.get('trim_pauses') for sh in edit['shots']):
        shots = []
        for sh in edit['shots']:
            if sh.get('trim_pauses') and 'in' in sh and 'out' in sh:
                parts = split_on_pauses(sh['path'], float(sh['in']), float(sh['out']), float(sh['trim_pauses']), lang=lang)
                for k, (a, b) in enumerate(parts):
                    shots.append({**{x: y for x, y in sh.items() if x != 'trim_pauses'}, 'in': a, 'out': b,
                                  'label': (sh.get('label') or '') + (f' ({k + 1}/{len(parts)})' if len(parts) > 1 else '')})
            else:
                shots.append(sh)
        edit = {**edit, 'shots': shots}
    aspect = edit.get('aspect', '16:9')
    assert aspect in ASPECTS, f'aspect must be one of {list(ASPECTS)}'
    first = core.light_index(edit['shots'][0]['path'], lang)
    fps = Fraction(edit.get('fps') or first['info']['fps'])
    F = float(fps)
    m = None
    if edit.get('music', {}).get('path'):
        mu = edit['music']
        if 'start' not in mu:
            mu['start'] = (music_map(mu['path'])['downbeats'] or [0])[0]
        m = music_for(mu)
    grid = _grid(edit, m, F)
    beat_f = (m['beat_sec'] * F) if m else F
    pace = edit.get('pace', 'medium')
    warns, out, t = [], [], 0
    for i, sh in enumerate(edit['shots']):
        idx = core.light_index(sh['path'], lang)
        info = idx['info']
        if 'offset' in sh:                      # multicam angle: source time follows the master clock
            if not edit.get('music'):
                raise ValueError('multicam shots need edit.music = the master recording (with "master": true)')
            s_in = edit['music']['start'] + t / F - float(sh['offset'])
            s_out = min(info['duration'], s_in + float(sh.get('max_len', 1e9)))
            sh = {**sh, 'anchor': 'start', 'audio': sh.get('audio', 'mute'), 'in': s_in, 'out': s_out}
            if s_in < 0 or s_out - s_in < 0.4:
                warns.append(f'shot {i + 1}: angle {info["name"]} does not cover master {edit["music"]["start"] + t / F:.2f}s')
                s_in = max(s_in, 0.0)
        else:
            s_in, s_out = float(sh['in']), float(sh['out'])
        if sh.get('snap') == 'speech':
            r = core.snap(idx, s_in, s_out, mode='speech', tighten=bool(sh.get('tighten')))
            s_in, s_out = r['in_sec'], r['out_sec']
        avail = int(math.floor((s_out - s_in) * F + 0.02))   # tolerate float error: never lose a frame
        keep = sh.get('keep', sh.get('audio') == 'dialogue')
        if keep:
            length = avail
            if grid:
                nxt = [g for g in grid if g >= t + length]
                room = int((info['duration'] - s_out) * F)
                if nxt and nxt[0] - (t + length) <= min(beat_f, room):
                    length = nxt[0] - t          # extend the tail to the next beat (source has the frames)
                elif nxt and room < nxt[0] - (t + length):
                    warns.append(f'shot {i + 1}: dialogue ends {(nxt[0] - t - length) / F:.2f}s before the beat and the source has no tail')
        else:
            if 'len' in sh:
                desired = round(float(sh['len']) * F)
            else:
                beats = sh.get('beats')
                if beats is None:
                    beats = PACE_BEATS.get(pace, 2)
                    if m and edit.get('pace_follows_music', True):
                        lev = _level_at(m, edit['music']['start'] + t / F)
                        beats = beats * (2 if lev == 'low' else 0.5 if lev == 'high' else 1)
                        beats = max(beats, 1 if edit.get('cut_on') != 'half' else 0.5)
                desired = beats * beat_f
            min_len = int(0.4 * F)
            if grid:
                cands = [g for g in grid if g - t >= min_len]
                fits = [g for g in cands if g - t <= avail]
                if fits:
                    g = min(fits, key=lambda g: abs(g - t - desired))
                    length = g - t
                elif cands:
                    length = avail
                    warns.append(f'shot {i + 1}: only {avail / F:.2f}s usable, shorter than one beat step — cut lands off-beat')
                else:
                    length = min(avail, int(desired))
            else:
                length = min(avail, int(round(desired)))
        length = max(1, min(length, int(info['duration'] * F) - int(s_in * F)))
        # choose the source sub-window
        extra = (s_out - s_in) - length / F
        anchor = sh.get('anchor', 'center')
        src_in = s_in if keep or anchor == 'start' or extra <= 0 else s_out - length / F if anchor == 'end' else \
            float(sh['at']) if anchor == 'at' else s_in + extra / 2
        rx, ry, gr = sh.get('reframe_x', 0.5), sh.get('reframe_y', 0.5), sh.get('grade')
        if rx == 'auto' or ry == 'auto':
            sug = suggest_reframe(idx, src_in, src_in + length / F, aspect, n=4)
            rx = sug['reframe_x'] if rx == 'auto' else rx
            ry = sug['reframe_y'] if ry == 'auto' else ry
        if gr == 'auto':
            gr = auto_grade(idx, src_in, src_in + length / F)
        sh = {**sh, 'reframe_x': rx, 'reframe_y': ry, 'grade': gr}
        out.append({**sh, 'n': i + 1, 'clip': info['name'], 'nle_path': info['nle_path'], 'src_fps': info['fps'],
                    'src_in': round(src_in, 4), 'src_out': round(src_in + length / F, 4),
                    'tl_in_f': t, 'tl_out_f': t + length, 'tl_in': round(t / F, 3), 'tl_out': round((t + length) / F, 3),
                    'beats_len': round(length / beat_f, 2) if m else None, 'keep': bool(keep),
                    'audio': sh.get('audio', 'dialogue' if keep else 'ambient'),
                    'reframe_x': sh.get('reframe_x', 0.5), 'reframe_y': sh.get('reframe_y', 0.5), 'zoom': sh.get('zoom', 1.0)})
        t += length
    total = t / F
    for a_, b_ in zip(out, out[1:]):              # same camera back-to-back = jump cut unless the picture changed
        if a_['path'] == b_['path'] and abs(a_['src_out'] - b_['src_in']) < 1.0:
            warns.append(f"shots {a_['n']}-{b_['n']}: same angle back-to-back ({a_['clip']}) — jump cut; use another angle/b-roll or merge them")
    # b-roll runs: picture-only layer over the spine (speech keeps playing underneath); cuts on the grid
    ovs = []
    for r_i, run in enumerate(edit.get('overlays', [])):
        a = int(round(float(run['from']) * F)); b = int(round(float(run['to']) * F))
        if grid:
            a = min(grid, key=lambda g: abs(g - a)) if abs(min(grid, key=lambda g: abs(g - a)) - a) <= beat_f / 2 else a
            b = min(grid, key=lambda g: abs(g - b)) if abs(min(grid, key=lambda g: abs(g - b)) - b) <= beat_f / 2 else b
        b = min(b, t)
        clips = run['clips']
        pos = a
        for c_i, c in enumerate(clips):
            if pos >= b - int(0.3 * F):
                warns.append(f'overlay run {r_i + 1}: clips {c_i + 1}+ not used (run already full)')
                break
            idx = core.light_index(c['path'], lang); info = idx['info']
            avail = int(math.floor((float(c['out']) - float(c['in'])) * F + 0.02))
            left = len(clips) - c_i
            if c_i == len(clips) - 1:
                desired = b - pos
            elif 'beats' in c:
                desired = c['beats'] * beat_f
            elif 'len' in c:
                desired = round(float(c['len']) * F)
            else:
                desired = (b - pos) / left          # share what is left evenly, then snap
            end = pos + desired
            if grid and c_i < len(clips) - 1:
                cands = [g for g in grid if pos + int(0.4 * F) <= g <= min(b, pos + avail)]
                if cands:
                    end = min(cands, key=lambda g: abs(g - end))
            end = int(round(min(end, pos + avail, b)))
            if end - pos < int(0.3 * F):
                warns.append(f'overlay run {r_i + 1} clip {c_i + 1}: too short ({(end - pos) / F:.2f}s usable)')
                continue
            extra = (float(c['out']) - float(c['in'])) - (end - pos) / F
            anchor = c.get('anchor', 'center')
            src_in = float(c['in']) if anchor == 'start' or extra <= 0 else float(c['out']) - (end - pos) / F if anchor == 'end' else float(c['in']) + extra / 2
            rx, ry, gr = c.get('reframe_x', 0.5), c.get('reframe_y', 0.5), c.get('grade')
            if rx == 'auto' or ry == 'auto':
                sug = suggest_reframe(idx, src_in, src_in + (end - pos) / F, aspect, n=4)
                rx = sug['reframe_x'] if rx == 'auto' else rx
                ry = sug['reframe_y'] if ry == 'auto' else ry
            if gr == 'auto':
                gr = auto_grade(idx, src_in, src_in + (end - pos) / F)
            c = {**c, 'reframe_x': rx, 'reframe_y': ry, 'grade': gr}
            ovs.append({**c, 'run': r_i + 1, 'layer': int(run.get('layer', 2)), 'n': f'{r_i + 1}.{c_i + 1}', 'clip': info['name'], 'nle_path': info['nle_path'],
                        'src_in': round(src_in, 4), 'src_out': round(src_in + (end - pos) / F, 4),
                        'tl_in_f': pos, 'tl_out_f': end, 'tl_in': round(pos / F, 3), 'tl_out': round(end / F, 3),
                        'beats_len': round((end - pos) / beat_f, 2) if m else None, 'audio': c.get('audio', 'mute'),
                        'reframe_x': c.get('reframe_x', 0.5), 'reframe_y': c.get('reframe_y', 0.5), 'zoom': c.get('zoom', 1.0)})
            pos = end
        if pos < b - int(0.3 * F):
            warns.append(f'overlay run {r_i + 1}: {(b - pos) / F:.2f}s left uncovered — add clips or shorten the run')
    mu = edit.get('music')
    if mu and m:
        mend = mu.get('end') or (m['start'] + m['duration'])
        if mu['start'] + total > mend + 0.05:
            warns.append(f'cut is {total:.1f}s but the music stretch is {mend - mu["start"]:.1f}s — add music or remove shots')
        mu['used_end'] = round(mu['start'] + total, 3)
    stills = []
    for st in edit.get('stills', []):
        a = int(round(float(st.get('from', 0)) * F)); b = min(t, int(round(float(st.get('to', total)) * F)))
        if b - a > 0:
            stills.append({'path': core.local_path(st['path']), 'layer': int(st.get('layer', 5)), 'tl_in_f': a, 'tl_out_f': b,
                           'tl_in': round(a / F, 3), 'tl_out': round(b / F, 3), 'label': st.get('label')})
    return {**edit, 'fps': f'{fps.numerator}/{fps.denominator}', 'duration': round(total, 3), 'shots': out, 'overlays': ovs, 'stills': stills,
            'bpm': m['bpm'] if m else None, 'warnings': warns}


def timeline_table(fit):
    row = lambda s, layer: {'layer': layer, 'n': s['n'], 'tl': f"{s['tl_in']:.2f}-{s['tl_out']:.2f}", 'len': round(s['tl_out'] - s['tl_in'], 2),
                            'beats': s['beats_len'], 'clip': s['clip'], 'src': f"{s['src_in']:.2f}-{s['src_out']:.2f}",
                            'audio': s['audio'], 'label': s.get('label', '')}
    return [row(s, 'V1') for s in fit['shots']] + [row(o, 'V2 b-roll') for o in fit.get('overlays', [])]


# ───────────────────────────── captions / titles (ASS) ─────────────────────────────
def _ass_time(t):
    t = max(t, 0)
    return f'{int(t // 3600)}:{int(t // 60 % 60):02d}:{t % 60:05.2f}'


def build_ass(fit, W, H, lang='pt'):
    cap = fit.get('captions') or {}
    vertical = H > W
    fs = int(H * (0.042 if vertical else 0.055))
    mv = int(H * (0.2 if vertical else 0.07))
    hdr = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,DejaVu Sans,{fs},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,{max(2, fs // 12)},1,2,{int(W * 0.08)},{int(W * 0.08)},{mv},1
Style: Title,DejaVu Sans,{int(fs * 1.9)},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,{max(3, fs // 8)},2,5,{int(W * 0.06)},{int(W * 0.06)},0,1
Style: Lower,DejaVu Sans,{int(fs * 0.9)},&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,0,0,3,{max(2, fs // 10)},0,1,{int(W * 0.06)},{int(W * 0.06)},{int(H * 0.08)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    if cap.get('enabled'):
        maxw = int(cap.get('max_words', 4 if vertical else 7))
        upper = cap.get('uppercase', vertical)
        for s in fit['shots']:
            if s['audio'] != 'dialogue':
                continue
            idx = core.light_index(s['path'], lang)
            ws = core.words_in(core.load_words(idx), s['src_in'], s['src_out'], idx)
            chunk = []
            def flush():
                if not chunk:
                    return
                a = s['tl_in'] + chunk[0]['s'] - s['src_in']; b = s['tl_in'] + chunk[-1]['e'] - s['src_in'] + 0.15
                txt = ' '.join(w['w'] for w in chunk)
                ev.append((max(a, s['tl_in']), min(b, s['tl_out']), 'Cap', txt.upper() if upper else txt))
                chunk.clear()
            for w in ws:
                if chunk and (len(chunk) >= maxw or w['s'] - chunk[-1]['e'] > 0.5 or w['e'] - chunk[0]['s'] > 2.2):
                    flush()
                chunk.append(w)
                if re.search(r'[.?!]$', w['w']):
                    flush()
            flush()
    for tx in fit.get('texts', []):
        st = {'title': 'Title', 'lower': 'Lower', 'caption': 'Cap'}.get(tx.get('style', 'title'), 'Title')
        fade = r'{\fad(200,200)}'
        ev.append((float(tx['at']), float(tx['at']) + float(tx.get('dur', 2)), st, fade + tx['text'].replace('\n', r'\N')))
    ev.sort()
    fixes = (cap.get('fix') or {})
    clean = []
    for k, (a, b, st, t) in enumerate(ev):
        if st == 'Cap':
            t = re.sub(r'\s+([,.!?;:])', r'\1', t)                      # "599 ,00" → "599,00"
            t = re.sub(r'(?i)\bR\s*\$\s*', 'R$ ', t)                     # "R $ 599" → "R$ 599"
            t = re.sub(r'(\d)\s*,\s*(\d)', r'\1,\2', t)
            for wrong, right in fixes.items():
                t = re.sub(re.escape(wrong), right.upper() if t.isupper() else right, t, flags=re.I)
            nxt = next((e[0] for e in ev[k + 1:] if e[2] == 'Cap'), None)
            if nxt is not None and b > nxt:
                b = nxt                                                 # one caption block on screen at a time
        clean.append((a, b, st, t))
    ev = clean
    return hdr + ''.join(f'Dialogue: 0,{_ass_time(a)},{_ass_time(b)},{st},,0,0,0,,{t}\n' for a, b, st, t in ev), len(ev)


# ───────────────────────────── render ─────────────────────────────
def _duck_expr(intervals, depth_db, ramp=0.25):
    if not intervals:
        return '1'
    d = 10 ** (depth_db / 20)
    parts = [f'clip((t-{a - ramp:.3f})/{ramp},0,1)*clip(({b + ramp:.3f}-t)/{ramp},0,1)' for a, b in intervals]
    m = parts[0]
    for p in parts[1:]:
        m = f'max({m},{p})'
    return f'1-{1 - d:.4f}*{m}'


def render_edit(fit, out_path, quality='preview', workers=3, lang='pt'):
    aspect = fit.get('aspect', '16:9')
    W, H = (fit.get('resolution') or RES[quality][aspect]) if quality == 'final' else RES['preview'][aspect]
    fps = Fraction(fit['fps']); F = float(fps)
    work = tempfile.mkdtemp(prefix='claudio_render_', dir=os.path.dirname(out_path))
    enc = ['-c:v', 'libx264', '-preset', 'ultrafast' if quality == 'preview' else 'medium',
           '-crf', '23' if quality == 'preview' else '16', '-pix_fmt', 'yuv420p']
    amb_db = float(fit.get('ambient_db', -16))

    def seg(s, tag='s'):
        idx = core.light_index(s['path'], lang)
        info = idx['info']
        n = s['tl_out_f'] - s['tl_in_f']
        dur = n / F
        ow, oh = core.oriented(info)
        cx, cy, cw, ch = crop_box(ow, oh, aspect, s['reframe_x'], s['reframe_y'], s['zoom'])
        g = s.get('grade') or {}
        eq = f",eq=gamma={g.get('gamma', 1):.3f}:contrast={g.get('contrast', 1):.3f}:saturation={g.get('saturation', 1):.3f}" if g else ''
        vf = f"{core.rot_vf(info['path'])}fps={fps.numerator}/{fps.denominator},crop={cw}:{ch}:{cx}:{cy},scale={W}:{H}:flags=lanczos,setsar=1{eq},format=yuv420p,tpad=stop_mode=clone:stop_duration=1"
        gain = {'dialogue': 0.0, 'ambient': amb_db, 'mute': None}[s['audio']]
        if s.get('gain_db') is not None and gain is not None:
            gain = float(s['gain_db'])
        cmd = ['ffmpeg', '-v', 'error', '-y', '-ss', f"{s['src_in']:.4f}", '-i', info['path']]
        ext = s.get('ext_audio')
        if ext and gain is not None:
            cmd += ['-ss', f"{max(0.0, s['src_in'] + float(ext['offset'])):.4f}", '-i', core.local_path(ext['path'])]
            amap = '1:a:0'
        elif info['audio_streams'] and gain is not None:
            amap = '0:a:0'
        else:
            cmd += ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo']
            amap = '1:a:0'
            gain = 0.0
        af = f"aresample=48000,aformat=channel_layouts=stereo,volume={gain}dB,apad,atrim=0:{dur:.5f},afade=t=in:d=0.01,afade=t=out:st={max(0, dur - 0.012):.4f}:d=0.012"
        o = os.path.join(work, f"{tag}{str(s['n']).replace('.', '_')}.mkv")
        cmd += ['-map', '0:v:0', '-map', amap, '-vf', vf, '-af', af, '-frames:v', str(n), *enc, '-g', '15',
                '-c:a', 'pcm_s16le', '-ar', '48000', o]
        core.run(cmd)
        return o

    with ThreadPoolExecutor(workers) as ex:
        segs = list(ex.map(seg, fit['shots']))
        ovl = list(ex.map(lambda o: seg(o, 'o'), fit.get('overlays', [])))
    lst = os.path.join(work, 'list.txt')
    open(lst, 'w').write(''.join(f"file '{p}'\n" for p in segs))
    full = os.path.join(work, 'full.mkv')
    core.run(['ffmpeg', '-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', lst, '-c', 'copy', full])
    total = fit['duration']
    ass, n_ev = build_ass(fit, W, H, lang)
    assp = os.path.join(work, 'subs.ass'); open(assp, 'w', encoding='utf-8').write(ass)
    fade_in, fade_out = float(fit.get('fade_in', 0.3)), float(fit.get('fade_out', 0.8))
    vf = []
    if n_ev:
        vf.append("subtitles=filename='{}'".format(assp.replace('\\', '/').replace(':', r'\:').replace("'", r"\'")))
    if fade_in: vf.append(f'fade=t=in:st=0:d={fade_in}')
    if fade_out: vf.append(f'fade=t=out:st={max(0, total - fade_out):.3f}:d={fade_out}')
    cmd = ['ffmpeg', '-v', 'error', '-y', '-i', full]
    for p in ovl:
        cmd += ['-i', p]
    mi = 1 + len(ovl)                       # music input index
    mu = fit.get('music')
    if mu and mu.get('path'):
        dia = [(s['tl_in'], s['tl_out']) for s in fit['shots'] if s['audio'] == 'dialogue']
        merged = []
        for a, b in dia:
            if merged and a - merged[-1][1] < 0.6:
                merged[-1] = (merged[-1][0], b)
            else:
                merged.append((a, b))
        duck = '1' if mu.get('master') else _duck_expr(merged, float(mu.get('duck_db', -14)))
        mf_in, mf_out = float(mu.get('fade_in', 0.05)), float(mu.get('fade_out', 1.5))
        cmd += ['-ss', f"{mu['start']:.4f}", '-t', f'{total:.4f}', '-i', core.local_path(mu['path'])]
        if mu.get('master'):
            mu.setdefault('gain_db', 0); mu.setdefault('fade_in', 0.3)
        fc = (f"[{mi}:a]aresample=48000,aformat=channel_layouts=stereo,volume={float(mu.get('gain_db', -4))}dB,"
              f"volume='{duck}':eval=frame,afade=t=in:d={mf_in},afade=t=out:st={max(0, total - mf_out):.3f}:d={mf_out}[m];"
              f"[0:a][m]amix=inputs=2:normalize=0:duration=first,loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[a]")
    else:
        fc = '[0:a]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[a]'
    # picture: spine, then each b-roll overlay placed at its timeline position, then captions/titles + fades on top
    vchain, last = [], '0:v'
    for k, o in enumerate(fit.get('overlays', [])):
        vchain.append(f"[{k + 1}:v]setpts=PTS-STARTPTS+{o['tl_in']:.4f}/TB[ov{k}];"
                      f"[{last}][ov{k}]overlay=eof_action=pass:enable='between(t,{o['tl_in']:.4f},{o['tl_out'] - 0.0005:.4f})'[b{k}]")
        last = f'b{k}'
    # overlay audio (ambient b-roll) is mixed in below only if asked; default b-roll is picture-only
    fc = ''.join(x + ';' for x in vchain) + f"[{last}]{','.join(vf) or 'null'}[v];" + fc
    cmd += ['-filter_complex', fc, '-map', '[v]', '-map', '[a]', *enc, '-c:a', 'aac', '-b:a', '192k',
            '-movflags', '+faststart', '-t', f'{total:.4f}', out_path]
    core.run(cmd)
    for p in segs + ovl + [full, lst, assp]:
        try: os.remove(p)
        except OSError: pass
    try: os.rmdir(work)
    except OSError: pass
    return {'path': out_path, 'nle_path': core.nle_path(out_path), 'duration': total, 'size': f'{W}x{H}',
            'captions_events': n_ev, 'quality': quality}


def source_storyboard(fit, lang='pt'):
    """Storyboard WITHOUT rendering: for every shot (and b-roll), the source frame at its middle with the edit's
    rotation/crop applied, plus the same timeline bar (beats, sections, b-roll layer). For reviewing a cut before it
    goes to Premiere/Resolve."""
    aspect = fit.get('aspect', '16:9')
    tw = 220 if aspect in ('9:16', '4:5') else 300
    items = sorted(fit['shots'] + fit.get('overlays', []), key=lambda s: (s['tl_in'], isinstance(s['n'], int)))
    tiles = []
    for s in items:
        info = core.light_index(s['path'], lang)['info']
        t = (s['src_in'] + s['src_out']) / 2
        im = core.grab(info['path'], t, 960)
        ow, oh = core.oriented(info)
        sc = im.width / ow
        cx, cy, cw, ch = crop_box(ow, oh, aspect, s['reframe_x'], s['reframe_y'], s['zoom'])
        im = im.crop((int(cx * sc), int(cy * sc), int((cx + cw) * sc), int((cy + ch) * sc)))
        im = im.resize((tw, int(tw * im.height / max(im.width, 1))))
        core._label(im, [f"{'B' if not isinstance(s['n'], int) else '#'}{s['n']} {s['tl_in']:.2f}s",
                         f"{s['tl_out'] - s['tl_in']:.2f}s" + (f" · {s['beats_len']}b" if s.get('beats_len') else ''),
                         (s.get('label') or s['clip'])[:22]], core._font(12))
        tiles.append(im)
    return _board(fit, tiles, tw)


def storyboard(fit, video_path, lang='pt'):
    """One tile per shot (middle frame of the RENDERED cut) + a timeline bar with beat ticks, sections and ducking."""
    shots = sorted(fit['shots'] + fit.get('overlays', []), key=lambda s: (s['tl_in'], isinstance(s['n'], int)))
    aspect = fit.get('aspect', '16:9')
    tw = 220 if aspect in ('9:16', '4:5') else 300
    tiles = []
    for s in shots:
        t = (s['tl_in'] + s['tl_out']) / 2
        im = core.grab(core.local_path(video_path), t, tw)
        core._label(im, [f"{'B' if not isinstance(s['n'], int) else '#'}{s['n']} {s['tl_in']:.2f}s", f"{s['tl_out'] - s['tl_in']:.2f}s" + (f" · {s['beats_len']}b" if s['beats_len'] else ''),
                         (s.get('label') or s['clip'])[:22]], core._font(12))
        tiles.append(im)
    return _board(fit, tiles, tw)


def _board(fit, tiles, tw):
    aspect = fit.get('aspect', '16:9')
    shots = fit['shots']
    th = tiles[0].height
    cols = min(len(tiles), 8 if aspect in ('9:16', '4:5') else 6)
    rows = math.ceil(len(tiles) / cols)
    BW = cols * tw
    # legend: blue = speech, green = ambient, grey = mute, orange (top) = b-roll over speech, yellow ticks = beats
    BH = 70
    img = Image.new('RGB', (BW, rows * th + BH), (18, 18, 18))
    for k, im in enumerate(tiles):
        img.paste(im, ((k % cols) * tw, (k // cols) * th))
    dr = ImageDraw.Draw(img)
    y0 = rows * th + 8
    total = fit['duration']
    X = lambda t: int(t / total * (BW - 1))
    col = {'dialogue': (90, 180, 255), 'ambient': (120, 200, 120), 'mute': (150, 150, 150)}
    for s in fit['shots']:
        dr.rectangle([X(s['tl_in']), y0 + 13, X(s['tl_out']) - 1, y0 + 26], fill=col[s['audio']], outline=(0, 0, 0))
        dr.text((X(s['tl_in']) + 2, y0 + 13), str(s['n']), fill=(0, 0, 0), font=core._font(10))
    for o in fit.get('overlays', []):   # b-roll layer drawn above the spine
        dr.rectangle([X(o['tl_in']), y0, X(o['tl_out']) - 1, y0 + 12], fill=(230, 160, 60), outline=(0, 0, 0))
    mu = fit.get('music')
    if mu and mu.get('path'):
        m = music_for(mu)
        for b in m['beats']:
            t = b - mu['start']
            if 0 <= t <= total:
                big = any(abs(b - d) < 1e-3 for d in m['downbeats'])
                dr.line([(X(t), y0 + 30), (X(t), y0 + (46 if big else 38))], fill=(255, 210, 0) if big else (160, 140, 60))
        for sct in m['sections']:
            a, b = sct['start'] - mu['start'], sct['end'] - mu['start']
            if b > 0 and a < total:
                dr.text((X(max(a, 0)) + 2, y0 + 48), sct['level'], fill={'low': (120, 120, 200), 'mid': (200, 200, 200), 'high': (255, 120, 80)}[sct['level']], font=core._font(11))
        if m.get('drop') and 0 <= m['drop'] - mu['start'] <= total:
            x = X(m['drop'] - mu['start']); dr.line([(x, y0), (x, y0 + 60)], fill=(255, 60, 60), width=2)
    return img


# ───────────────────────────── export to NLE ─────────────────────────────
def export_edit_fcp7(fit, out_path, lang='pt'):
    """FCP7 XML of the fitted cut: V1 picture (with Basic Motion scale/centre for reframes), A1 production audio as one
    stereo track with per-shot levels (dual mono on the next pair for a lav on one side / two different mics), then music.
    Clip in/out are expressed in the sequence timebase (FCP7 convention)."""
    fps = Fraction(fit['fps']); F = float(fps)
    aspect = fit.get('aspect', '16:9')
    W, H = fit.get('resolution') or RES['final'][aspect]
    rate = core._rate_xml(fps)
    E = core._esc
    files, vitems, mitems = {}, [], {1: [], 2: []}
    aitems = {g: {1: [], 2: []} for g in ('st', 'dm')}   # one stereo track (A1) + dual mono for lav-on-one-side / two mics
    amb_db = float(fit.get('ambient_db', -16))
    prev_a = {}                                       # (group, channel) → (end frame, out frame, media frames) of its last clip
    group = {}
    for s in fit['shots']:
        if s['audio'] != 'mute':
            info = core.light_index(s['path'], lang)['info']
            e = s['src_in'] + (s['tl_out_f'] - s['tl_in_f']) / F
            group[s['n']] = 'st' if core.stereo_ok(info, s['src_in'], e) else 'dm'
    base = {'st': 1, 'dm': 3 if 'st' in group.values() else 1}
    xfade = ('<transitionitem><start>{0}</start><end>{1}</end><alignment>center</alignment>' + rate +
             '<effect><name>Cross Fade (+3dB)</name><effectid>KGAudioTransCrossFade3dB</effectid><effectcategory>Dissolve</effectcategory>'
             '<effecttype>transition</effecttype><mediatype>audio</mediatype></effect></transitionitem>')

    def file_el(path, info):
        if path in files:
            return f'<file id="{files[path]}"/>'
        files[path] = f'file-{len(files) + 1}'
        clk = core.clock_of(info) if info.get('fps') else None
        ach = min(info.get('audio_channels') or 0, 2)
        vid = (f'<video><samplecharacteristics>{core._rate_xml(info["fps"])}<width>{info["width"]}</width>'
               f'<height>{info["height"]}</height></samplecharacteristics></video>') if info.get('has_video') else ''
        tc = (f'<timecode>{core._rate_xml(info["fps"])}<string>{clk.tc(0)}</string><frame>{clk.start}</frame>'
              f'<displayformat>{"DF" if clk.drop else "NDF"}</displayformat></timecode>') if clk else ''
        return (f'<file id="{files[path]}"><name>{E(os.path.basename(path))}</name><pathurl>{E(core.file_url(path))}</pathurl>'
                f'{rate}<duration>{int(info["duration"] * F)}</duration>{tc}<media>{vid}'
                + (f'<audio><samplecharacteristics><depth>16</depth><samplerate>{info.get("audio_rate") or 48000}</samplerate>'
                   f'</samplecharacteristics><channelcount>{ach}</channelcount></audio>' if ach else '') + '</media></file>')

    def level(db):
        return (f'<filter><effect><name>Audio Levels</name><effectid>audiolevels</effectid><effectcategory>audiolevels</effectcategory>'
                f'<effecttype>audiolevels</effecttype><mediatype>audio</mediatype><parameter><parameterid>level</parameterid>'
                f'<name>Level</name><valuemin>0</valuemin><valuemax>3.98109</valuemax><value>{10 ** (db / 20):.5f}</value>'
                f'</parameter></effect></filter>')

    for s in fit['shots']:
        idx = core.light_index(s['path'], lang); info = idx['info']
        p = info['path']
        n = s['n']
        cin, cout = int(round(s['src_in'] * F)), int(round(s['src_in'] * F)) + (s['tl_out_f'] - s['tl_in_f'])
        common = (f'<name>{E(s.get("label") or info["name"])}</name><duration>{int(info["duration"] * F)}</duration>{rate}'
                  f'<start>{s["tl_in_f"]}</start><end>{s["tl_out_f"]}</end><in>{cin}</in><out>{cout}</out>')
        # reframe: scale so the crop fills the frame, centre in Premiere's units (fcp_center)
        ow, oh = core.oriented(info)
        rot = core.get_rotation(info['path'])
        cx, cy, cw, ch = crop_box(ow, oh, aspect, s['reframe_x'], s['reframe_y'], s['zoom'])
        scale = max(W / cw, H / ch) * 100
        offx, offy = fcp_center(ow, oh, cx, cy, cw, ch, scale, info)
        motion = ''
        if rot or abs(scale - max(W / ow, H / oh) * 100) > 0.5 or abs(offx) > 1e-3 or abs(offy) > 1e-3:
            motion = (f'<filter><effect><name>Basic Motion</name><effectid>basic</effectid><effectcategory>motion</effectcategory>'
                      f'<effecttype>motion</effecttype><mediatype>video</mediatype>'
                      f'<parameter><parameterid>scale</parameterid><name>Scale</name><valuemin>0</valuemin><valuemax>1000</valuemax><value>{scale:.2f}</value></parameter>'
                      f'<parameter><parameterid>rotation</parameterid><name>Rotation</name><valuemin>-8640</valuemin><valuemax>8640</valuemax><value>{rot}</value></parameter>'
                      f'<parameter><parameterid>center</parameterid><name>Center</name><value><horiz>{offx:.4f}</horiz><vert>{offy:.4f}</vert></value></parameter>'
                      f'</effect></filter>')
        ach = min(info['audio_channels'] or 0, 2) if s['audio'] != 'mute' else 0
        g = group.get(n, 'dm')
        links = ''.join(f'<link><linkclipref>c{n}-{k}</linkclipref><mediatype>{"video" if k == "v" else "audio"}</mediatype>'
                        f'<trackindex>{1 if k == "v" else base[g] + int(k[1]) - 1}</trackindex><clipindex>{n}</clipindex></link>'
                        for k in ['v'] + ['a1', 'a2'][:ach])
        vitems.append(f'<clipitem id="c{n}-v">{common}{file_el(p, info)}{motion}{links}</clipitem>')
        db = 0.0 if s['audio'] == 'dialogue' else amb_db
        if s.get('gain_db') is not None:
            db = float(s['gain_db'])
        for ch_ in range(1, ach + 1):
            pe = prev_a.get((g, ch_))                  # two takes butted: 2 frames of crossfade, so the join doesn't click
            if pe and pe[0] == s['tl_in_f'] and cin >= 1 and pe[1] + 1 <= pe[2]:
                aitems[g][ch_].append(xfade.format(s['tl_in_f'] - 1, s['tl_in_f'] + 1))
            prev_a[(g, ch_)] = (s['tl_out_f'], cout, int(info['duration'] * F))
            aitems[g][ch_].append(f'<clipitem id="c{n}-a{ch_}">{common}<file id="{files[p]}"/><sourcetrack><mediatype>audio</mediatype>'
                               f'<trackindex>{ch_}</trackindex></sourcetrack>{level(db)}{links}</clipitem>')
    layers = {}
    for o in fit.get('overlays', []):
        idx = core.light_index(o['path'], lang); info = idx['info']
        cin = int(round(o['src_in'] * F)); cout = cin + (o['tl_out_f'] - o['tl_in_f'])
        ow, oh = core.oriented(info)
        cx, cy, cw, ch = crop_box(ow, oh, aspect, o['reframe_x'], o['reframe_y'], o['zoom'])
        scale = max(W / cw, H / ch) * 100
        offx, offy = fcp_center(ow, oh, cx, cy, cw, ch, scale, info)
        motion = (f'<filter><effect><name>Basic Motion</name><effectid>basic</effectid><effectcategory>motion</effectcategory>'
                  f'<effecttype>motion</effecttype><mediatype>video</mediatype>'
                  f'<parameter><parameterid>scale</parameterid><name>Scale</name><valuemin>0</valuemin><valuemax>1000</valuemax><value>{scale:.2f}</value></parameter>'
                  f'<parameter><parameterid>center</parameterid><name>Center</name><value><horiz>{offx:.4f}</horiz><vert>{offy:.4f}</vert></value></parameter>'
                  f'</effect></filter>')
        layers.setdefault(int(o.get('layer', 2)), []).append(f'<clipitem id="b{str(o["n"]).replace(".", "_")}"><name>{E(o.get("label") or info["name"])}</name>'
                  f'<duration>{int(info["duration"] * F)}</duration>{rate}<start>{o["tl_in_f"]}</start><end>{o["tl_out_f"]}</end>'
                  f'<in>{cin}</in><out>{cout}</out>{file_el(info["path"], info)}{motion}</clipitem>')
    for k, st in enumerate(fit.get('stills', [])):           # images (logo) over a span
        from PIL import Image as _I
        try:
            iw, ih = _I.open(st['path']).size
        except Exception:
            iw, ih = W, H
        L = st['tl_out_f'] - st['tl_in_f']
        if st['path'] not in files:
            files[st['path']] = f'file-{len(files) + 1}'
            fel = (f'<file id="{files[st["path"]]}"><name>{E(os.path.basename(st["path"]))}</name><pathurl>{E(core.file_url(st["path"]))}</pathurl>'
                   f'{rate}<duration>{L}</duration><media><video><samplecharacteristics>{rate}<width>{iw}</width><height>{ih}</height>'
                   f'</samplecharacteristics></video></media></file>')
        else:
            fel = f'<file id="{files[st["path"]]}"/>'
        layers.setdefault(int(st['layer']), []).append(
            f'<clipitem id="still-{k}"><name>{E(st.get("label") or os.path.basename(st["path"]))}</name><duration>{L}</duration>{rate}'
            f'<start>{st["tl_in_f"]}</start><end>{st["tl_out_f"]}</end><in>0</in><out>{L}</out>{fel}</clipitem>')
    v2 = [x for v in layers.values() for x in v]
    upper = ''.join(f'<track>{"".join(layers.get(k, []))}</track>' for k in range(2, max(layers) + 1)) if layers else ''
    mu = fit.get('music')
    mu = mu if mu and mu.get('path') else None
    if mu:
        mp = core.local_path(mu['path'])
        minfo = core.probe(mp)
        mi = int(round(mu['start'] * F)); total_f = fit['shots'][-1]['tl_out_f']
        for ch_ in (1, 2):
            mitems[ch_].append(f'<clipitem id="music-{ch_}"><name>{E(os.path.basename(mp))}</name><duration>{int(minfo["duration"] * F)}</duration>'
                               f'{rate}<start>0</start><end>{total_f}</end><in>{mi}</in><out>{mi + total_f}</out>'
                               f'{file_el(mp, minfo) if ch_ == 1 else f"<file id={chr(34)}{files[mp]}{chr(34)}/>"}'
                               f'<sourcetrack><mediatype>audio</mediatype><trackindex>{min(ch_, max(1, minfo["audio_channels"]))}</trackindex></sourcetrack>'
                               f'{level(float(mu.get("gain_db", -4)))}</clipitem>')
    tracks = (core.audio_track_pair(aitems['st'][1], aitems['st'][2]) + core.audio_track_pair(aitems['dm'][1], aitems['dm'][2], False)
              + core.audio_track_pair(mitems[1], mitems[2], bool(mu) and (minfo.get('audio_channels') or 0) >= 2))
    total_f = fit['shots'][-1]['tl_out_f']
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4"><sequence id="seq-1">'
           f'<name>{E(fit.get("name", "Cláudio — montagem"))}</name><duration>{total_f}</duration>{rate}'
           f'<timecode>{rate}<string>01:00:00:00</string><frame>{round(F) * 3600}</frame><displayformat>NDF</displayformat></timecode>'
           f'<media><video><format><samplecharacteristics>{rate}<width>{W}</width><height>{H}</height>'
           f'<pixelaspectratio>square</pixelaspectratio></samplecharacteristics></format><track>{"".join(vitems)}</track>'
           + upper +
           f'</video><audio>{tracks}</audio></media></sequence></xmeml>\n')
    open(out_path, 'w', encoding='utf-8').write(xml)
    return {'path': out_path, 'nle_path': core.nle_path(out_path), 'shots': len(fit['shots']), 'broll': len(v2), 'duration_sec': fit['duration'],
            'sequence': f'{W}x{H} @ {fit["fps"]}'}


# ───────────────────────────── project files ─────────────────────────────
def project_dir(project):
    p = core.local_path(project)
    os.makedirs(os.path.join(p, 'renders'), exist_ok=True)
    return p


def sequence_name(fit, v):
    """What the editor sees in the bin: the edit's own name ("INC — Estela"), with the version when it has none."""
    name = (fit.get('name') or '').strip() or 'Cláudio — montagem'
    return name if re.search(r'\bv\d+\b', name, re.I) else f'{name} v{v}'


def safe_filename(name):
    """A sequence name as a Windows-safe file name (Premiere names the imported bin after the file)."""
    return re.sub(r'\s+', ' ', re.sub(r'[<>:"/\\|?*\x00-\x1f]', '-', name)).strip(' .') or 'montagem'


def next_version(p):
    vs = [int(m.group(1)) for f in os.listdir(p) if (m := re.match(r'edit_v(\d+)\.json$', f))]
    return max(vs, default=0) + 1


def load_fit(p, version=None):
    version = version or (next_version(p) - 1)
    if version < 1:
        raise FileNotFoundError('no edit in this project yet — build_edit first')
    return version, json.load(open(os.path.join(p, f'edit_v{version}.json'), encoding='utf-8'))


# ───────────────────────────── multicam: sync every clip to one master recording ─────────────────────────────
ENV_SR = 100     # envelope rate for the coarse search (10 ms)


def _envelope(x, sr):
    """Onset-like envelope at 100 Hz: rise of log energy (robust between different microphones)."""
    hop = sr // ENV_SR
    n = len(x) // hop
    e = np.log((x[:n * hop].reshape(n, hop) ** 2).mean(1) + 1e-8)
    d = np.maximum(np.diff(e, prepend=e[:1]), 0)
    return (d - d.mean()) / (d.std() + 1e-9)


def _xcorr_best(sig, ref):
    """Lag (in samples) where sig best matches inside ref, plus peak/second-peak ratio."""
    n = 1 << int(math.ceil(math.log2(len(sig) + len(ref))))
    cc = np.fft.irfft(np.fft.rfft(ref, n) * np.conj(np.fft.rfft(sig, n)), n)[:len(ref)]
    i = int(np.argmax(cc))
    excl = max(1, len(cc) // 2000)
    mask = np.ones(len(cc), bool); mask[max(0, i - 50):i + 50] = False
    second = float(cc[mask].max()) if mask.any() else 1e-9
    return i, float(cc[i] / (abs(second) + 1e-9))


_master_cache = {}


def _master(path):
    if path not in _master_cache:
        x = _decode_mono(path, 8000)
        _master_cache.clear()
        _master_cache[path] = (x, _envelope(x, 8000))
    return _master_cache[path]


def sync_clip_to_master(master, clip, probe_len=90.0):
    """master_time = clip_time + offset. Coarse: envelope xcorr over the whole master; fine: 8 kHz waveform xcorr
    within ±40 ms; drift: the same measure on the clip's last minute (ms of drift over the clip)."""
    m = core.local_path(master); c = core.local_path(clip)
    mx, menv = _master(m)
    info = core.probe(c)
    if not info['audio_streams']:
        return {'clip': info['name'], 'nle_path': info['nle_path'], 'error': 'clip has no audio'}
    dur = info['duration']

    def measure(t0, L):
        y = _decode_mono(c, 8000, t0, L)
        yenv = _envelope(y, 8000)
        lag, conf = _xcorr_best(yenv, menv)
        coarse = lag / ENV_SR - t0                      # master_time - clip_time
        # fine
        a = int((t0 + coarse - 0.05) * 8000)
        seg = mx[max(0, a): a + len(y) + 800]
        if len(seg) > len(y):
            hp = lambda v: v - np.convolve(v, np.ones(8) / 8, 'same')
            fl, _ = _xcorr_best(hp(y), hp(seg))
            fine = (max(0, a) + fl) / 8000 - t0
            if abs(fine - coarse) < 0.06:
                coarse = fine
        return coarse, conf

    L = min(probe_len, dur)
    off, conf = measure(0.0, L)
    if conf < 1.5 and dur > L + 5:              # little shared sound at the start: use the whole clip (≤ 10 min)
        off2, conf2 = measure(0.0, min(dur, 600.0))
        if conf2 > conf:
            off, conf = off2, conf2
    method = 'audio'
    if conf < 1.5:                              # different mics / weak board feed: align what is SAID instead
        r = _sync_by_words(m, c)
        if r and r['votes'] >= 3:
            off, conf, method = r['offset'], 1.5 + r['agreement'] * 3, 'transcript'
            # refine on the waveform around the matched speech (±0.3 s)
            y = _decode_mono(c, 8000, r['clip_t'], 8.0)
            a = int((r['clip_t'] + off - 0.3) * 8000)
            seg = mx[max(0, a): a + len(y) + 4800]
            if len(seg) > len(y):
                hp = lambda v: v - np.convolve(v, np.ones(8) / 8, 'same')
                fl, fconf = _xcorr_best(hp(y), hp(seg))
                fine = (max(0, a) + fl) / 8000 - r['clip_t']
                if abs(fine - off) < 0.3 and fconf > 1.2:
                    off = fine
    drift_ms = None
    if dur > 2 * probe_len + 30:
        off_end, conf_end = measure(dur - probe_len, probe_len)
        if conf_end > 1.3 and abs(off_end - off) < 1.0:
            drift_ms = round((off_end - off) * 1000, 1)
    return {'clip': info['name'], 'path': c, 'nle_path': info['nle_path'], 'offset': round(off, 4), 'confidence': round(conf, 2),
            'reliable': conf > 1.5, 'method': method, 'master_in': round(off, 3), 'master_out': round(off + dur, 3), 'duration': round(dur, 3),
            'fps': info.get('fps'), 'drift_ms_over_clip': drift_ms}


def _sync_by_words(master, clip, n=3):
    """Offset from matching word trigrams between the clip's and the master's transcripts (both indexed/cached)."""
    mi = core.light_index(master); ci = core.light_index(clip)
    MW, CW = core.load_words(mi), core.load_words(ci)
    if len(CW) < n or len(MW) < n:
        return None
    key = lambda ws: ' '.join(core.norm(w['w']) for w in ws)
    table = {}
    for i in range(len(MW) - n + 1):
        table.setdefault(key(MW[i:i + n]), []).append(MW[i]['s'])
    votes = []
    for i in range(len(CW) - n + 1):
        k = key(CW[i:i + n])
        if len(k) > 8 and k in table and len(table[k]) <= 3:
            votes += [(t - CW[i]['s'], CW[i]['s']) for t in table[k]]
    if not votes:
        return None
    offs = np.array([v[0] for v in votes])
    best = max(offs, key=lambda o: (np.abs(offs - o) < 0.25).sum())
    agree = offs[np.abs(offs - best) < 0.25]
    clip_t = min(v[1] for v in votes if abs(v[0] - best) < 0.25)
    return {'offset': float(np.median(agree)), 'votes': int(len(agree)), 'agreement': float(len(agree) / len(offs)),
            'clip_t': max(0.0, clip_t - 0.5)}


def multicam_sync(master, clips, workers=2):
    with ThreadPoolExecutor(workers) as ex:
        res = list(ex.map(lambda c: sync_clip_to_master(master, c), clips))
    res.sort(key=lambda r: r.get('master_in', 1e9))
    return res


def angles_at(sync, t):
    """Which synced clips cover master time t (and the clip-local time there)."""
    return [{'clip': r['clip'], 'nle_path': r['nle_path'], 'clip_time': round(t - r['offset'], 3)}
            for r in sync if r.get('reliable') and r['master_in'] <= t < r['master_out']]
