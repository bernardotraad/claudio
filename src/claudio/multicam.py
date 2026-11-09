"""claudio.multicam — PluralEyes-style multicam sync from the clips' own audio.

  sync_cameras(cameras, external)   folders/files per camera (+ optional external recorder files) → every clip placed on
                                    one timeline by its audio, external recorder clock drift measured and corrected,
                                    exported as an FCP7 XML that Premiere and Resolve import ("Synced Sequence").

How it aligns a show (cameras with a clock — place_sequence, the way PluralEyes does it):
  1. each camera's clips laid out in recording order by its own clock (one clip at a time; ~1 s resolution)
  2. whole cameras aligned to each other at once by what is played (chroma: same notes in every mic, any room)
  3. every clip refined in a ±3 s window against the other cameras; then the external recordings (board mix, one file
     per song) searched in the cameras' sound — their order on the timeline is whatever the show's was
  4. frame accuracy: every overlapping pair measured on band onsets (8 bands, 5 ms) and solved together by weighted
     least squares, outliers dropped — no chain of clip-to-clip guesses
Without clocks (or when cameras do not line up) it falls back to clip-by-clip placement:

  1. every file → 8 kHz mono audio (cached) → 100 Hz onset envelope (rise of log energy: robust across different mics)
  2. graph placement: start from the anchor (the longest external recording, else the longest clip); repeatedly place the
     unplaced file whose best envelope cross-correlation against an already placed file is the most confident
  3. refine every placement on the 8 kHz waveform (±40 ms) against the neighbour it was placed from
  4. fallback when two mics sound too different: align what is SAID (transcript trigrams) if both are indexed
  5. drift: on long clips, measure the offset every 30 s and fit a line → the external recorder's clock error (ppm);
     the recorder audio is resampled (like PluralEyes' *_drift_corrected+X.wav) so it stays in sync for hours
Camera clocks are the reference: picture is never retimed; positions are whole frames of the sequence rate.
"""
import json, math, os, re, subprocess, uuid
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction

import numpy as np

from claudio import core

SR = 8000
ENV = 100                      # envelope rate (10 ms)
AUDIO_EXT = core.AUDIO_EXT


# ───────────────────────────── audio access (cached) ─────────────────────────────
def audio8k(path):
    """8 kHz mono float32 of a file; reuses the 16 kHz track of an existing index, else decodes once and caches."""
    p = core.local_path(path)
    d = core.cache_dir(p)
    c8 = os.path.join(d, 'a8k.pcm')
    if os.path.exists(c8):                     # an interrupted decode leaves a short file: redo it
        try:
            dur = core.light_index(p)['info']['duration']
            if os.path.getsize(c8) / 2 / SR < dur - 1.0:
                os.remove(c8)
        except Exception:
            pass
    if not os.path.exists(c8):
        a16 = os.path.join(d, 'a16.pcm')
        if os.path.exists(a16):
            x = np.fromfile(a16, np.int16)[::2]
            x.tofile(c8)
        else:                                  # proxy when there is one: same audio, a fraction of the bytes to read
            core.run(['ffmpeg', '-v', 'error', '-y', '-i', core.analysis_source(p), '-vn', '-map', '0:a:0', '-ac', '1',
                      '-ar', str(SR), '-f', 's16le', c8 + '.part'])
            os.replace(c8 + '.part', c8)
    return np.fromfile(c8, np.int16).astype(np.float32) / 32768


def envelope(x):
    hop = SR // ENV
    n = len(x) // hop
    if n < 2:
        return np.zeros(2, np.float32)
    e = np.log((x[:n * hop].reshape(n, hop) ** 2).mean(1) + 1e-8)
    d = np.maximum(np.diff(e, prepend=e[:1]), 0)
    return ((d - d.mean()) / (d.std() + 1e-9)).astype(np.float32)


def _hp(v):
    return v - np.convolve(v, np.ones(8) / 8, 'same')


def xcorr(a, b):
    """Full cross-correlation: returns (lag, ratio) with b[t] ≈ a[t - lag]  →  time_in_b = time_in_a + lag/rate.
    ratio = best peak / best peak elsewhere (≥ ~1.5 means a real match)."""
    n = 1 << int(math.ceil(math.log2(len(a) + len(b))))
    cc = np.fft.irfft(np.fft.rfft(b, n) * np.conj(np.fft.rfft(a, n)), n)
    lags = np.r_[np.arange(0, len(b)), np.arange(-len(a) + 1, 0)]
    cc = np.r_[cc[:len(b)], cc[n - len(a) + 1:]]
    i = int(np.argmax(cc))
    m = np.ones(len(cc), bool)
    m[max(0, i - 60):i + 60] = False
    second = float(cc[m].max()) if m.any() else 1e-9
    # normalise by overlap so short overlaps at the edges don't win by accident
    return int(lags[i]), float(cc[i] / (abs(second) + 1e-9))


def peaks(a, b, k=3, min_sep_s=2.0):
    """The k best distinct alignments of a inside b: [(lag_s, score)], score = Pearson correlation of the envelopes
    over their overlap at that lag (comparable between takes, unlike the raw peak)."""
    n = 1 << int(math.ceil(math.log2(len(a) + len(b))))
    cc = np.fft.irfft(np.fft.rfft(b, n) * np.conj(np.fft.rfft(a, n)), n)
    lags = np.r_[np.arange(0, len(b)), np.arange(-len(a) + 1, 0)]
    cc = np.r_[cc[:len(b)], cc[n - len(a) + 1:]]
    out, m = [], np.ones(len(cc), bool)
    sep = int(min_sep_s * ENV)
    for _ in range(k):
        if not m.any():
            break
        i = int(np.argmax(np.where(m, cc, -np.inf)))
        lag = int(lags[i])
        x0, y0 = max(0, -lag), max(0, lag)
        L = min(len(a) - x0, len(b) - y0)
        if L > ENV * 2:
            xa, yb = a[x0:x0 + L], b[y0:y0 + L]
            r = float(np.corrcoef(xa, yb)[0, 1]) if xa.std() > 0 and yb.std() > 0 else 0.0
            out.append((lag / ENV, r))
        m[max(0, i - sep):i + sep] = False
    return sorted(out, key=lambda p: -p[1])


CH_HOP, CH_NF = 400, 4096          # chroma at 20 frames/s from the 8 kHz audio


def chroma(x):
    """12-bin pitch-class profile per 50 ms (60 Hz–2 kHz), log-compressed and normalised per frame: which notes sound,
    regardless of mix, timbre or loudness — a playback of another mix, or the band playing along, still matches."""
    n = 1 + (len(x) - CH_NF) // CH_HOP
    if n < 4:
        return np.zeros((0, 12), np.float32)
    f = np.fft.rfftfreq(CH_NF, 1 / SR)
    ok = (f > 60) & (f < 2000)
    pc = (np.round(12 * np.log2(f[ok] / 440)) % 12).astype(int)
    win = np.hanning(CH_NF).astype(np.float32)
    C = np.zeros((n, 12), np.float32)
    for i in range(0, n, 512):                      # chunked: long takes stay light on memory
        idx = np.arange(i, min(n, i + 512))[:, None] * CH_HOP + np.arange(CH_NF)[None]
        S = np.abs(np.fft.rfft(x[idx] * win, axis=1))[:, ok]
        for k in range(12):
            C[i:i + len(idx), k] = S[:, pc == k].sum(1)
    C = np.log1p(C)
    C -= C.mean(1, keepdims=True)
    C /= np.linalg.norm(C, axis=1, keepdims=True) + 1e-9
    return C


def chroma_peaks(ct, cg, k=3, min_sep_s=2.0):
    """Best alignments of take chroma ct inside guide chroma cg: [(lag_s, score)], score = mean frame similarity over
    the overlap (needs at least half of the shorter one to overlap)."""
    fps = SR / CH_HOP
    L, G = len(ct), len(cg)
    if not L or not G:
        return []
    n = 1 << int(math.ceil(math.log2(G + L)))
    cc = sum(np.fft.irfft(np.fft.rfft(cg[:, j], n) * np.conj(np.fft.rfft(ct[:, j], n)), n) for j in range(12))
    lags = np.r_[np.arange(0, G), np.arange(-L + 1, 0)]
    cc = np.r_[cc[:G], cc[n - L + 1:]]
    ov = np.minimum(G, lags + L) - np.maximum(0, lags)
    score = np.where(ov >= 0.5 * min(L, G), cc / np.maximum(ov, 1), -1.0)
    out, sep = [], int(min_sep_s * fps)
    for i in np.argsort(-score)[:400]:
        if score[i] <= 0:
            break
        if all(abs(lags[i] - l) > sep for l, _ in out):
            out.append((int(lags[i]), float(score[i])))
        if len(out) == k:
            break
    return [(l / fps, s) for l, s in out]


def _refine_lag(et, eg, lag_s, reach_s=0.6):
    """The onset-envelope alignment closest to a chroma lag, for frame accuracy (chroma frames are 50 ms)."""
    c = int(round(lag_s * ENV)); r = int(reach_s * ENV)
    best = None
    for lag in range(c - r, c + r + 1):
        x0, y0 = max(0, -lag), max(0, lag)
        Lx = min(len(et) - x0, len(eg) - y0)
        if Lx < ENV * 2:
            continue
        a, b = et[x0:x0 + Lx], eg[y0:y0 + Lx]
        v = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))
        if best is None or v > best[1]:
            best = (lag, v)
    return best[0] / ENV if best else lag_s


def place_music(nodes, ref, progress=lambda *_: None, min_r=0.45, tie=0.92):
    """Music video: every take plays the same song, so each one is aligned against the guide track only.
    A take that fits two places almost equally well (a repeated chorus) is placed at the best one and flagged with the
    alternatives; a take whose audio doesn't match the song at all stays unplaced."""
    from concurrent.futures import as_completed
    with ThreadPoolExecutor(3) as ex:
        futs = {ex.submit(audio8k, n['path']): n for n in nodes}
        for k, f in enumerate(as_completed(futs)):
            f.result()
            progress(f"audio {futs[f]['name']}", (k + 1) / len(nodes))
    gx = audio8k(ref['path'])
    eref, cref = envelope(gx), chroma(gx)
    ref.update(t0=0.0, via=None, conf=None, method='trilha guia')
    todo = [n for n in nodes if n is not ref]
    for k, n in enumerate(todo):
        x = audio8k(n['path'])
        ps = chroma_peaks(chroma(x), cref)          # where in the song: by the notes (robust to mix / live playing)
        progress('align', (k + 1) / max(len(todo), 1))
        if not ps or ps[0][1] < min_r:
            n.update(t0=None, via=None, conf=round(ps[0][1], 2) if ps else None, method='unplaced')
            continue
        best = ps[0]
        lag = _refine_lag(envelope(x), eref, best[0])   # exactly where: by the onsets, near that spot
        alts = [p for p in ps[1:] if p[1] >= best[1] * tie and p[1] >= min_r]
        n.update(t0=lag, via=ref['name'], conf=round(best[1], 2),
                 method='música' if not alts else 'música: ambíguo (trecho repetido?) — confira',
                 alternatives=[round(p[0], 2) for p in alts])
    return nodes


# ───────────────────────────── inputs ─────────────────────────────
def _files(spec, exts):
    out = []
    for p in ([spec] if isinstance(spec, str) else spec):
        lp = core.local_path(p)
        if os.path.isdir(lp):
            out += core.media_files(lp, exts)       # originals, or the proxies when they are all that was kept
        elif os.path.isfile(lp):
            out.append(lp)
    return out


def collect(cameras, external=None):
    """cameras: {"name": folder | [files]} or [folder, ...] (name = folder name). external: folder | [files] | None."""
    if isinstance(cameras, (list, tuple)):
        cameras = {os.path.basename(core.local_path(c).rstrip('/')) or f'cam{i + 1}': c for i, c in enumerate(cameras)}
    nodes = []
    for cam, spec in cameras.items():
        for f in _files(spec, core.VIDEO_EXT):
            info = core.light_index(f)['info']
            if not info.get('audio_streams'):
                continue
            nodes.append({'path': f, 'name': os.path.basename(f), 'cam': cam, 'kind': 'video', 'info': info,
                          'duration': info['duration']})
    for f in _files(external or [], AUDIO_EXT | core.VIDEO_EXT):
        info = core.probe(f)
        nodes.append({'path': f, 'name': os.path.basename(f), 'cam': 'AUDIO', 'kind': 'audio', 'info': info,
                      'duration': info['duration']})
    return nodes


# ───────────────────────────── placement ─────────────────────────────
def _words_offset(a, b):
    """Transcript fallback: time_in_b - time_in_a from matching word trigrams (both files must be indexed)."""
    try:
        A, B = core.words_only(a['path']), core.words_only(b['path'])
    except Exception:
        return None
    if len(A) < 3 or len(B) < 3:
        return None
    key = lambda ws: ' '.join(core.norm(w['w']) for w in ws)
    tb = {}
    for i in range(len(B) - 2):
        tb.setdefault(key(B[i:i + 3]), []).append(B[i]['s'])
    votes = [t - A[i]['s'] for i in range(len(A) - 2) if len(key(A[i:i + 3])) > 8 for t in tb.get(key(A[i:i + 3]), [])[:3]]
    if len(votes) < 3:
        return None
    v = np.array(votes)
    best = max(v, key=lambda o: (np.abs(v - o) < 0.25).sum())
    agree = v[np.abs(v - best) < 0.25]
    return (float(np.median(agree)), len(agree)) if len(agree) >= 3 else None


def place(nodes, min_conf=1.6, progress=lambda *_: None, use_words=True):
    """Graph placement. Sets node['t0'] = start on the common clock (seconds), node['via'], node['conf']."""
    envs = {}

    def env(n):
        if n['path'] not in envs:
            envs[n['path']] = envelope(audio8k(n['path']))
        return envs[n['path']]

    from concurrent.futures import as_completed
    with ThreadPoolExecutor(3) as ex:          # decode/cache all audio in parallel (I/O bound on big camera files)
        futs = {ex.submit(audio8k, n['path']): n for n in nodes}
        for k, f in enumerate(as_completed(futs)):
            f.result()
            progress(f"audio {futs[f]['name']}", (k + 1) / len(nodes))
    for n in nodes:
        env(n)
    ext = [n for n in nodes if n['kind'] == 'audio']
    anchor = max(ext or nodes, key=lambda n: n['duration'])
    anchor.update(t0=0.0, via=None, conf=None)
    placed, todo = [anchor], [n for n in nodes if n is not anchor]
    cache = {}
    while todo:
        best = None
        for n in todo:
            for p in placed:
                k = (n['path'], p['path'])
                if k not in cache:
                    lag, r = xcorr(env(n), env(p))        # time_in_p = time_in_n + lag
                    cache[k] = (lag / ENV, r)
                lag, r = cache[k]
                if best is None or r > best[2]:
                    best = (n, p, r, lag)
        n, p, r, lag = best
        if r < min_conf:
            break
        n.update(t0=p['t0'] + lag, via=p['name'], conf=round(r, 2), method='audio')
        placed.append(n); todo.remove(n)
        progress('align', len(placed) / len(nodes))
    # transcript fallback for what audio could not place
    def confirm(n, p, off):
        """A transcript match must also be heard: local envelope correlation around the proposed offset."""
        a, b = env(n), env(p)
        lo = int((off - 0.6) * ENV)
        seg_b = b[max(0, lo): max(0, lo) + len(a) + int(1.2 * ENV)]
        if len(seg_b) < ENV * 4 or len(a) < ENV * 4:
            return False
        L = min(len(a), len(seg_b) - int(1.2 * ENV))
        if L < ENV * 4:
            return False
        lag, r = xcorr(a[:L], seg_b)
        return r >= 1.25 and abs((max(0, lo) + lag) / ENV - off) < 0.3
    if use_words:
        for k, n in enumerate(list(todo)):
            progress(f'transcript {n["name"]}', k / max(len(todo), 1))
            hits = []
            for p in placed:
                w = _words_offset(n, p)
                if w:
                    hits.append((w[1], p, w[0]))
            for votes, p, off in sorted(hits, key=lambda h: -h[0]):
                if votes >= 4 and confirm(n, p, off):
                    n.update(t0=p['t0'] + off, via=p['name'], conf=round(1.5 + votes / 10, 2), method='transcript')
                    placed.append(n); todo.remove(n)
                    break
    if todo:
        _place_islands(todo, placed, env, cache, min_conf, progress)
    for n in todo:
        if n.get('t0') is None:
            n.update(t0=None, via=None, conf=None, method='unplaced')
    # fine refinement on the waveform against the node each one was placed from
    byname = {n['name']: n for n in nodes}
    for n in placed:
        if not n.get('via'):
            continue
        p = byname[n['via']]
        a, b = audio8k(n['path']), audio8k(p['path'])
        ov0 = max(0.0, -(n['t0'] - p['t0']))                       # overlap start in n's time
        L = min(20.0, n['duration'] - ov0 - 0.5, p['duration'] - (n['t0'] - p['t0'] + ov0) - 0.5)
        if L < 2:
            continue
        sa = a[int(ov0 * SR): int((ov0 + L) * SR)]
        pb = n['t0'] - p['t0'] + ov0                                  # same moment in p's time
        lo = int((pb - 0.04) * SR)
        sb = b[max(0, lo): int((pb + L + 0.04) * SR)]
        if len(sb) <= len(sa):
            continue
        lag, r = xcorr(_hp(sa), _hp(sb))
        fine = (max(0, lo) + lag) / SR - ov0 + p['t0']             # n's t0 from the fine match
        if abs(fine - n['t0']) < 0.05 and r > 1.2:
            n['t0'] = fine
    return nodes


def _clock(n):
    """The camera's own clock for when this file started (creation_time / timecode tags), as epoch seconds."""
    import datetime
    try:
        j = json.loads(core.run(['ffprobe', '-v', 'error', '-show_entries', 'format_tags=creation_time', '-of', 'json', n['path']]).stdout)
        ct = j.get('format', {}).get('tags', {}).get('creation_time')
        if ct:
            return datetime.datetime.fromisoformat(ct.replace('Z', '+00:00')).timestamp()
    except Exception:
        pass
    return None


def _place_islands(todo, placed, env, cache, min_conf, progress):
    """Clips with no audio in common with the placed group (e.g. recorded before the recorder started): align them
    to each other by audio, then put each island on the timeline by the camera clock, calibrated on the clips of
    the same camera that WERE placed by audio. Marked approximate."""
    groups = []
    rest = list(todo)
    while rest:
        seed = max(rest, key=lambda n: n['duration'])
        seed['_g'] = {seed['path']: 0.0}
        grp = [seed]; rest.remove(seed)
        grew = True
        while grew:
            grew = False
            for n in list(rest):
                if n['duration'] < 10:                 # too short to trust an audio match → clock only
                    continue
                for g in grp:
                    if g['duration'] < 10:
                        continue
                    lag, r = xcorr(env(n), env(g))
                    if r >= min_conf:
                        seed['_g'][n['path']] = seed['_g'][g['path']] + lag / ENV
                        grp.append(n); rest.remove(n); grew = True
                        break
        groups.append((seed, grp))
    # camera clock calibration: timeline time - clock time, per camera, from audio-placed clips
    calib = {}
    for n in placed:
        if n['kind'] == 'video' and n.get('method') in ('audio', None, 'anchor') or (n['kind'] == 'video' and n.get('t0') is not None and n.get('method') == 'audio'):
            c = _clock(n)
            if c is not None:
                calib.setdefault(n['cam'], []).append(n['t0'] - c)
    for seed, grp in groups:
        offs = []
        for n in grp:
            c = _clock(n)
            if c is not None and calib.get(n['cam']):
                offs.append(float(np.median(calib[n['cam']])) + c - seed['_g'][n['path']])
        if not offs:
            continue
        base = float(np.median(offs))
        for n in grp:
            n.update(t0=base + seed['_g'][n['path']], via=None, conf=None,
                     method='relógio da câmera (aprox. ±1 s, sem áudio em comum)' if len(grp) == 1 else 'ilha: áudio entre si + relógio da câmera (aprox.)')
            if n in todo:
                todo.remove(n)
            placed.append(n)


# ───────────────────────────── sequence placement (PluralEyes-style, for shows) ─────────────────────────────
CFPS = SR / CH_HOP                     # chroma frames per second (20)


def _clock_starts(ns):
    """Start of each clip on its camera's own clock. Most cameras stamp creation_time at the start; some at the end of
    the recording — chosen by which reading leaves the camera's clips not overlapping (a camera records one at a time)."""
    def overlaps(starts):
        o = sorted(zip(starts, ns), key=lambda x: x[0])
        return sum(1 for (s0, a), (s1, _) in zip(o, o[1:]) if s1 < s0 + a['duration'] - 2.0)
    start = [n['_clk'] for n in ns]
    end = [n['_clk'] - n['duration'] for n in ns]
    return end if overlaps(end) < overlaps(start) else start


def _lane(items, length, dim=None, rate=CFPS):
    """items [(t0_s, features)] → one array on a common clock (zeros where nothing rolls)."""
    lane = np.zeros((length, dim) if dim else length, np.float32)
    for t, f in items:
        i = int(round(t * rate))
        a, b = max(0, i), min(length, i + len(f))
        if b > a:
            lane[a:b] = f[a - i:b - i]
    return lane


def _chroma_mix(lanes):
    """Average of several chroma lanes, renormalised per frame; frames where nothing rolls stay zero."""
    s = sum(lanes)
    nrm = np.linalg.norm(s, axis=1, keepdims=True)
    return np.where(nrm > 1e-6, s / (nrm + 1e-9), 0).astype(np.float32)


def _chroma_search(a, b, min_overlap, k=2, sep_s=4.0):
    """Best alignments of chroma a inside chroma b over every lag: [(lag_s, score)], b[t] ≈ a[t - lag]. score = mean
    frame similarity over the frames where BOTH have sound (gaps between clips don't dilute it), only for lags where
    that overlap is at least min_overlap frames."""
    if not len(a) or not len(b):
        return []
    n = 1 << int(math.ceil(math.log2(len(a) + len(b))))
    cc = sum(np.fft.irfft(np.fft.rfft(b[:, j], n) * np.conj(np.fft.rfft(a[:, j], n)), n) for j in range(12))
    ma, mb = (np.abs(a).sum(1) > 0).astype(np.float64), (np.abs(b).sum(1) > 0).astype(np.float64)
    ov = np.fft.irfft(np.fft.rfft(mb, n) * np.conj(np.fft.rfft(ma, n)), n)
    lags = np.r_[np.arange(0, len(b)), np.arange(-len(a) + 1, 0)]
    cc, ov = np.r_[cc[:len(b)], cc[n - len(a) + 1:]], np.r_[ov[:len(b)], ov[n - len(a) + 1:]]
    score = np.where(ov >= min_overlap, cc / np.maximum(ov, 1), -1.0)
    out, sep = [], int(sep_s * CFPS)
    for i in np.argsort(-score)[:4000]:
        if score[i] <= 0:
            break
        if all(abs(lags[i] - l) > sep for l, _ in out):
            out.append((int(lags[i]), float(score[i])))
        if len(out) == k:
            break
    return [(l / CFPS, s) for l, s in out]


def _chroma_near(a, b, lag_s, reach_s):
    """Best lag of chroma a inside b within ±reach of lag_s: (lag_s, score), score over the frames where both sound."""
    c, r = int(round(lag_s * CFPS)), int(round(reach_s * CFPS))
    best = (lag_s, -1.0)
    for lag in range(c - r, c + r + 1):
        x0, y0 = max(0, -lag), max(0, lag)
        L = min(len(a) - x0, len(b) - y0)
        if L < CFPS * 3:
            continue
        xa, yb = a[x0:x0 + L], b[y0:y0 + L]
        both = (np.abs(xa).sum(1) > 0) & (np.abs(yb).sum(1) > 0)
        if both.sum() < CFPS * 3:
            continue
        v = float((xa[both] * yb[both]).sum(1).mean())
        if v > best[1]:
            best = (lag / CFPS, v)
    return best


BF_RATE = 200                          # band onsets: 5 ms frames


def band_onsets(x):
    """Onsets in 8 log-spaced bands (100 Hz–3.6 kHz) at 5 ms: the timing of what every mic hears, band by band —
    far sharper between different mics/rooms than one broadband envelope (which a loud band dominates)."""
    hop, N = SR // BF_RATE, 512
    m = 1 + (len(x) - N) // hop
    if m < 4:
        return np.zeros((0, 8), np.float32)
    edges = np.geomspace(100, 3600, 9)
    f = np.fft.rfftfreq(N, 1 / SR)
    sel = [(f >= edges[b]) & (f < edges[b + 1]) for b in range(8)]
    win = np.hanning(N).astype(np.float32)
    out = np.zeros((m, 8), np.float32)
    for i in range(0, m, 4096):
        idx = np.arange(i, min(m, i + 4096))[:, None] * hop + np.arange(N)[None]
        S = np.abs(np.fft.rfft(x[idx] * win, axis=1)) ** 2
        for b in range(8):
            out[i:i + len(idx), b] = np.log(S[:, sel[b]].sum(1) + 1e-9)
    d = np.maximum(np.diff(out, axis=0, prepend=out[:1]), 0)
    return ((d - d.mean(0)) / (d.std(0) + 1e-9)).astype(np.float32)


def _pair_offset(a, b, reach_s=0.4, chunk_s=30.0, chunks=4):
    """Fine timing error of b relative to a where they overlap (both already placed to ~0.2 s): band onsets
    correlated over a few chunks spread along the overlap. Returns (err_s, weight) — b's true start is
    b['t0'] - err_s relative to a — or None when there is no clear peak."""
    t0 = max(a['t0'], b['t0']) + reach_s + 0.5
    t1 = min(a['t0'] + a['duration'], b['t0'] + b['duration']) - reach_s - 0.5
    if t1 - t0 < 8:
        return None
    fa, fb, R = a['_bf'], b['_bf'], int(reach_s * BF_RATE)
    W = min(chunk_s, t1 - t0)
    starts = np.linspace(t0, t1 - W, max(1, min(chunks, int((t1 - t0) // W))))
    L = int(W * BF_RATE)
    acc = np.zeros(2 * R + 1)
    for t in starts:
        ia, ib = int((t - a['t0']) * BF_RATE), int((t - b['t0']) * BF_RATE)
        sa = fa[ia:ia + L]
        if len(sa) < L or ib - R < 0:
            continue
        for k in range(2 * R + 1):
            sb = fb[ib - R + k: ib - R + k + L]
            if len(sb) == L:
                acc[k] += float((sa * sb).mean())
    i = int(np.argmax(acc))
    m = np.ones(len(acc), bool)
    m[max(0, i - 10):i + 11] = False
    peak, second = acc[i] / len(starts), (acc[m].max() / len(starts) if m.any() else 0.0)
    if peak < 0.06 or peak < 2.5 * max(second, 1e-6) or i in (0, 2 * R):
        return None
    return (i - R) / BF_RATE, peak * min(t1 - t0, 120.0)


def _refine_jointly(nodes, fixed):
    """Every overlapping pair of placed files (different cameras, or a recording against a camera) gives a measured
    timing error; one weighted least-squares solve turns them into a correction per file, with `fixed` held still —
    the way PluralEyes settles a whole show at once instead of trusting a chain. Measurements that disagree with the
    solution (> 40 ms) are dropped and it is solved again."""
    placed = [n for n in nodes if n.get('t0') is not None and len(n.get('_bf', ())) and n.get('method') != 'unplaced']
    idx = {id(n): k for k, n in enumerate(placed)}
    eqs = []
    for i, a in enumerate(placed):
        for b in placed[i + 1:]:
            if a['cam'] == b['cam'] or (a['kind'] == 'audio' and b['kind'] == 'audio'):
                continue
            r = _pair_offset(a, b)
            if r:
                eqs.append((idx[id(a)], idx[id(b)], -r[0], r[1]))       # c_b - c_a = -err
    if not eqs or id(fixed) not in idx:
        return 0
    for _ in range(3):
        rows = [(i, j, d, w) for i, j, d, w in eqs]
        A = np.zeros((len(rows) + 1, len(placed)))
        y = np.zeros(len(rows) + 1)
        for k, (i, j, d, w) in enumerate(rows):
            s = math.sqrt(w)
            A[k, j], A[k, i], y[k] = s, -s, d * s
        A[-1, idx[id(fixed)]] = 1e3                                       # the reference stays where it is
        c = np.linalg.lstsq(A, y, rcond=None)[0]
        keep = [(i, j, d, w) for i, j, d, w in eqs if abs(c[j] - c[i] - d) <= 0.04]
        if len(keep) == len(eqs):
            break
        eqs = keep
    # only files tied to the reference by at least one kept measurement move
    linked = {idx[id(fixed)]}
    grew = True
    while grew:
        grew = False
        for i, j, _, _ in eqs:
            if (i in linked) != (j in linked):
                linked |= {i, j}; grew = True
    moved = 0
    for k, n in enumerate(placed):
        if k in linked and abs(c[k]) <= 0.4:
            n['t0'] += float(c[k])
            n['fine'] = True
            moved += 1
    return moved


def _confident(peaks, min_score=0.3, ratio=1.4):
    if not peaks or peaks[0][1] < min_score:
        return False
    return len(peaks) < 2 or peaks[0][1] >= ratio * max(peaks[1][1], 1e-6)


def place_sequence(nodes, progress=lambda *_: None):
    """Shows and events, the way PluralEyes does it: each camera's clips are laid out in recording order by its own
    clock (a camera records one clip at a time), whole cameras are aligned to each other at once by what is being
    played (chroma: the same notes reach every mic, whatever the mic, mix or room), every clip is then refined in a
    few seconds' window against the other cameras, and only then the external recordings (board mix, one file per
    song…) are looked for in the cameras' sound. Returns False when it does not apply (fewer than two cameras with a
    clock, or cameras that do not line up) — the caller falls back to place()."""
    try:
        return _place_sequence(nodes, progress)
    finally:
        for n in nodes:
            for key in ('_chr', '_bf', '_clk'):
                n.pop(key, None)


def _place_sequence(nodes, progress):
    from concurrent.futures import as_completed
    vids = [n for n in nodes if n['kind'] == 'video']
    with ThreadPoolExecutor(3) as ex:
        futs = {ex.submit(audio8k, n['path']): n for n in nodes}
        for k, f in enumerate(as_completed(futs)):
            f.result()
            progress(f"audio {futs[f]['name']}", (k + 1) / len(nodes) * 0.5)
    for k, n in enumerate(nodes):
        x = audio8k(n['path'])
        n['_chr'], n['_bf'] = chroma(x), band_onsets(x)
        n['_clk'] = _clock(n) if n['kind'] == 'video' else None
        progress(f"audio {n['name']}", 0.5 + (k + 1) / len(nodes) * 0.5)
    cams = {}
    for n in vids:
        cams.setdefault(n['cam'], []).append(n)
    lanes, rel = {}, {}
    for cam, ns in cams.items():
        if any(n['_clk'] is None for n in ns):
            continue
        st = _clock_starts(ns)
        c0 = min(st)
        for n, s in zip(ns, st):
            rel[n['path']] = s - c0
        L = int(math.ceil(max(rel[n['path']] + n['duration'] for n in ns) * CFPS)) + 1
        lanes[cam] = _lane([(rel[n['path']], n['_chr']) for n in ns], L, 12)
    if len(lanes) < 2:
        return False
    # 1. whole cameras against each other, the one that rolled longest first
    ref = max(lanes, key=lambda c: sum(n['duration'] for n in cams[c]))
    off = {ref: 0.0}
    while len(off) < len(lanes):
        t_lo = min(off.values())
        T = int(math.ceil((max(off[c] + len(lanes[c]) / CFPS for c in off) - t_lo) * CFPS)) + 1
        mix = _chroma_mix([_lane([(off[c] - t_lo, lanes[c])], T, 12) for c in off])
        best = None
        for c in lanes:
            if c in off:
                continue
            pk = _chroma_search(lanes[c], mix, min_overlap=60 * CFPS)
            if _confident(pk) and (best is None or pk[0][1] > best[1][0][1]):
                best = (c, pk)
        if not best:
            break
        c, pk = best
        off[c] = t_lo + pk[0][0]
        progress('align', len(off) / len(lanes))
    if len(off) < 2:
        return False
    for c in off:
        for n in cams[c]:
            n.update(t0=off[c] + rel[n['path']], via=None, conf=None, method='sequência da câmera (relógio, aprox. ±1 s)')
    # 2. every clip in a few seconds' window against the other cameras (the clock is good to ~1 s), frame-accurate on
    #    band onsets (all pairs solved together, below); a camera never overlaps itself
    t_lo = min(n['t0'] for c in off for n in cams[c])
    T = int(math.ceil((max(n['t0'] + n['duration'] for c in off for n in cams[c]) - t_lo) * CFPS)) + CFPS * 10
    cam_lane = {c: _lane([(n['t0'] - t_lo, n['_chr']) for n in cams[c]], int(T), 12) for c in off}
    seq = [n for c in off for n in cams[c]]
    for k, n in enumerate(seq):
        others = _chroma_mix([cam_lane[c] for c in off if c != n['cam']])
        lag, s = _chroma_near(n['_chr'], others, n['t0'] - t_lo, reach_s=3.0)
        if s >= 0.3:
            n.update(t0=t_lo + lag, conf=round(s, 2), method='sequência da câmera + áudio')
        progress('clips', (k + 1) / len(seq))
    for c in off:
        ns = sorted(cams[c], key=lambda n: rel[n['path']])
        for a, b in zip(ns, ns[1:]):                       # refinement may not make a camera overlap itself
            if b['t0'] < a['t0'] + a['duration'] - 0.5:
                b.update(t0=a['t0'] + (rel[b['path']] - rel[a['path']]), conf=None,
                         method='sequência da câmera (relógio, aprox. ±1 s)')
    # 3. what is left (external recordings, clips without clock or of a camera that did not line up): searched in the
    #    sound of all the cameras, and accepted only if it fits there clearly
    mix = _chroma_mix(list(cam_lane.values()))
    rest = [n for n in nodes if n.get('t0') is None]
    for k, n in enumerate(rest):
        progress(f"songs {n['name']}", (k + 1) / len(rest))
        if len(n['_chr']) < CFPS * 10:
            continue
        pk = _chroma_search(n['_chr'], mix, min_overlap=0.8 * len(n['_chr']))
        if _confident(pk):
            n.update(t0=t_lo + pk[0][0], via=None, conf=round(pk[0][1], 2), method='áudio (procurado nas câmeras)')
    for n in nodes:
        if n.get('t0') is None:
            n.update(t0=None, via=None, conf=None, method='unplaced')
    # 4. frame accuracy: every overlapping pair measured on band onsets, solved together (the longest clip of the
    #    reference camera stays put); then a camera still may not overlap itself
    progress('fine', 0.0)
    _refine_jointly(nodes, max(cams[ref], key=lambda n: n['duration']))
    for c in off:
        ns = sorted(cams[c], key=lambda n: n['t0'])
        for a, b in zip(ns, ns[1:]):
            if b['t0'] < a['t0'] + a['duration'] - 0.1:
                b['t0'] = a['t0'] + a['duration']
    progress('fine', 1.0)
    return True


# ───────────────────────────── drift ─────────────────────────────
def measure_drift(clip, ref, step=30.0, win=12.0):
    """Offset of `clip` against `ref` measured every `step` s along the clip (8 kHz waveform match around the
    placement). Returns slope (ppm of ref time per clip time − 1) and the points, or None if too short."""
    if clip['duration'] < 4 * step:
        return None
    a, b = audio8k(clip['path']), audio8k(ref['path'])
    pts = []
    t = 5.0
    while t + win < clip['duration'] - 2:
        tb = clip['t0'] - ref['t0'] + t
        if 0 < tb < ref['duration'] - win - 1:
            sa = _hp(a[int(t * SR): int((t + win) * SR)])
            lo = int((tb - 0.15) * SR)
            sb = _hp(b[max(0, lo): int((tb + win + 0.15) * SR)])
            if len(sb) > len(sa) and np.abs(sa).mean() > 1e-4:
                lag, r = xcorr(sa, sb)
                if r > 1.5:
                    pts.append((t, (max(0, lo) + lag) / SR - t))
        t += step
    if len(pts) < 4:
        return None
    T = np.array([p[0] for p in pts]); O = np.array([p[1] for p in pts])
    # robust slope: median of pairwise slopes (Theil–Sen)
    sl = [(O[j] - O[i]) / (T[j] - T[i]) for i in range(len(T)) for j in range(i + 1, len(T)) if T[j] - T[i] > 60]
    if not sl:
        return None
    k = float(np.median(sl))
    resid = O - (np.median(O - k * T) + k * T)
    return {'ppm': round(k * 1e6, 2), 'points': len(pts), 'span_s': round(float(T[-1] - T[0]), 1),
            'residual_ms': round(float(np.median(np.abs(resid)) * 1000), 2)}


def drift_correct(ext, ppm, out_dir=None):
    """Resample an external recording so its clock matches the cameras (PluralEyes-style *_drift_corrected±X.wav).
    ppm > 0: the recording runs long against the cameras → it gets shortened, and vice versa."""
    src = core.local_path(ext['path'])
    k = ppm * 1e-6
    out_dir = out_dir or os.path.join(os.path.dirname(src), 'claudio_drift_corrected')
    os.makedirs(out_dir, exist_ok=True)
    delta = ext['duration'] * (1 / (1 + k) - 1)
    stem = os.path.splitext(os.path.basename(src))[0]
    out = os.path.join(out_dir, f'{stem}_drift_corrected{delta:+.3f}.wav')
    if not os.path.exists(out):
        sr = int(ext['info'].get('audio_rate') or 48000)
        ch = max(1, min(int(ext['info'].get('audio_channels') or 2), 8))
        # asetrate reinterprets the rate (stretches time by (1+k)), aresample brings it back to the original rate
        core.run(['ffmpeg', '-v', 'error', '-y', '-i', src, '-af', f'asetrate={sr * (1 + k):.6f},aresample={sr}:resampler=soxr:precision=28',
                  '-ac', str(ch), '-c:a', 'pcm_s24le', out])
    return out


# ───────────────────────────── main entry ─────────────────────────────
def sync_cameras(cameras, external=None, fps=None, correct_drift=True, progress=lambda *_: None, drift_dir=None, music=False):
    """music=True: a music video — the external audio is the guide track and every take is aligned against it (see
    place_music); no drift correction (the guide is a digital file, not a recorder)."""
    nodes = collect(cameras, external)
    if len(nodes) < 2:
        raise ValueError('need at least two files with audio')
    ext0 = [n for n in nodes if n['kind'] == 'audio']
    if music:
        if not ext0:
            raise ValueError('music video mode needs the guide track as external audio')
        place_music(nodes, max(ext0, key=lambda n: n['duration']), progress)
    elif not place_sequence(nodes, progress):         # cameras with a clock: by sequence; else clip by clip
        place(nodes, progress=progress)
    placed = [n for n in nodes if n.get('t0') is not None]
    ext = [n for n in placed if n['kind'] == 'audio']
    drift = None
    if ext and not music:
        ref = max(ext, key=lambda n: n['duration'])
        meas = []
        long_clips = sorted([n for n in placed if n['kind'] == 'video' and n['duration'] >= 240], key=lambda n: -n['duration'])[:6]
        for n in long_clips:
            m = measure_drift(n, ref)
            if m:
                meas.append((n['name'], n['duration'], m))
        if meas:
            w = np.array([d for _, d, _ in meas]); v = np.array([m['ppm'] for _, _, m in meas])
            order = np.argsort(v); cw = np.cumsum(w[order])
            ppm = float(v[order][np.searchsorted(cw, cw[-1] / 2)])            # duration-weighted median
            drift = {'reference': ref['name'], 'ppm': round(ppm, 2),
                     'over_recording_ms': round(ppm * 1e-6 * ref['duration'] * 1000, 1),
                     'measured_on': [{'clip': c, 'ppm': m['ppm'], 'points': m['points'], 'residual_ms': m['residual_ms']} for c, _, m in meas]}
            fr = float(Fraction(fps)) if fps else 30.0
            if correct_drift and abs(drift['over_recording_ms']) > 1000 / fr / 2:
                out = drift_correct(ref, ppm, drift_dir)
                k = ppm * 1e-6
                # cameras keep their times; the corrected recording runs 1/(1+k) as long — its start stays where it was
                for n in placed:
                    if n is not ref and n['kind'] == 'video':
                        n['t0'] = ref['t0'] + (n['t0'] - ref['t0']) / (1 + k)
                ref['corrected_path'] = out
                ref['duration_corrected'] = ref['duration'] / (1 + k)
                drift['corrected_file'] = core.nle_path(out)
    t_min = min(n['t0'] for n in placed)
    for n in placed:
        n['t0'] -= t_min
    vids = [n for n in placed if n['kind'] == 'video']
    if not fps:
        rates = {}
        for n in vids:
            rates[n['info']['fps']] = rates.get(n['info']['fps'], 0) + n['duration']
        fps = max(rates, key=rates.get) if rates else '30000/1001'
    F = float(Fraction(fps))
    for n in placed:
        n['start_frame'] = int(round(n['t0'] * F))
    return {'fps': fps, 'nodes': nodes, 'drift': drift, 'music': bool(music),
            'unplaced': [n['name'] for n in nodes if n.get('t0') is None]}


def summary(res):
    rows = []
    for n in sorted(res['nodes'], key=lambda n: (n['cam'], n.get('t0') if n.get('t0') is not None else 1e12)):
        rows.append({'cam': n['cam'], 'clip': n['name'], 'start_s': round(n['t0'], 3) if n.get('t0') is not None else None,
                     'start_frame': n.get('start_frame'), 'duration': round(n['duration'], 2), 'method': n.get('method', 'anchor'),
                     'confidence': n.get('conf'), 'placed_from': n.get('via'),
                     **({'also_fits_at_s': n['alternatives']} if n.get('alternatives') else {})})
    return {'fps': res['fps'], 'clips': rows, 'drift': res['drift'], 'unplaced': res['unplaced']}


# ───────────────────────────── export (PluralEyes-compatible layout) ─────────────────────────────
# Premiere label colours (FCP7 XML <labels><label2>): one per camera, so every angle and its audio read at a glance.
LABELS = ['Cerulean', 'Mango', 'Forest', 'Magenta', 'Yellow', 'Violet', 'Teal', 'Rose', 'Tan', 'Lavender', 'Brown', 'Purple']
AUDIO_LABEL = 'Caribbean'
PREMIERE_LABELS = set(LABELS) | {AUDIO_LABEL, 'Iris', 'Blue', 'Green'}


def camera_labels(cams, labels=None):
    """{camera: Premiere label}. External audio gets its own colour; cameras take the palette in order.
    labels overrides per camera (names from PREMIERE_LABELS, case-insensitive)."""
    want = {k.upper(): v for k, v in (labels or {}).items()}
    fix = {x.lower(): x for x in PREMIERE_LABELS}
    out = {}
    for c in cams:
        v = want.get(c.upper())
        if v and v.lower() in fix:
            out[c] = fix[v.lower()]
        elif c == 'AUDIO':
            out[c] = AUDIO_LABEL
    pal = [c for c in LABELS if c not in out.values()]
    for c in cams:
        if c not in out:
            out[c] = pal.pop(0) if pal else 'Iris'
    return out


def export_xml(res, out_path, name='Synced Sequence', labels=None):
    """FCP7 XML like PluralEyes writes: a project with one bin per camera (+ audio) and a "Synced Sequence" with
    V1..Vn = cameras, stereo audio pairs per camera, external audio on the last pair. Premiere: import the file;
    Resolve: File › Import Timeline (or the Resolve MCP import_timeline_from_file)."""
    fps = Fraction(res['fps']); F = float(fps)
    E = core._esc
    rate = core._rate_xml(fps)
    placed = [n for n in res['nodes'] if n.get('t0') is not None]
    cams = []
    for n in placed:
        if n['cam'] not in cams:
            cams.append(n['cam'])
    vcams = [c for c in cams if any(n['cam'] == c and n['kind'] == 'video' for n in placed)]
    acams = vcams + [c for c in cams if c not in vcams]
    col = camera_labels(acams, labels)
    lab = lambda n: f'<labels><label2>{col[n["cam"]]}</label2></labels>'
    ids = {}

    def fid(n):
        return ids.setdefault(n['path'], str(uuid.uuid4()).upper())

    def file_el(n, full):
        path = n.get('corrected_path') or n['path']
        if not full:
            return f'<file id="{fid(n)}"/>'
        info = n['info']
        dur = int(round((n.get('duration_corrected') or n['duration']) * F))
        ch = min(int(info.get('audio_channels') or 2), 2)
        vid = (f'<video><samplecharacteristics>{core._rate_xml(info["fps"])}<width>{info["width"]}</width><height>{info["height"]}</height>'
               f'<anamorphic>FALSE</anamorphic><pixelaspectratio>square</pixelaspectratio></samplecharacteristics></video>') if n['kind'] == 'video' else ''
        tc = ''
        if n['kind'] == 'video' and info.get('start_tc'):
            clk = core.clock_of(info)
            tc = (f'<timecode>{core._rate_xml(info["fps"])}<string>{info["start_tc"]}</string><frame>{clk.start}</frame>'
                  f'<displayformat>{"DF" if clk.drop else "NDF"}</displayformat></timecode>')
        return (f'<file id="{fid(n)}"><name>{E(os.path.basename(path))}</name><pathurl>{E(core.file_url(path))}</pathurl>{rate}'
                f'<duration>{dur}</duration>{tc}<media>{vid}<audio><samplecharacteristics><depth>16</depth>'
                f'<samplerate>{info.get("audio_rate") or 48000}</samplerate></samplecharacteristics><channelcount>{ch}</channelcount></audio></media></file>')

    seen = set()
    vtracks, atracks = [], []
    clipid = {}
    def lanes(ns, dur_of):
        """Clips of one source that overlap in time (music-video takes all play the same song) go on stacked tracks."""
        out = []
        for n in sorted(ns, key=lambda n: n['t0']):
            for lane in out:
                if lane[-1]['t0'] + dur_of(lane[-1]) <= n['t0'] + 1e-3:
                    lane.append(n); break
            else:
                out.append([n])
        return out

    vlanes = [(c, ln) for c in vcams for ln in lanes([n for n in placed if n['cam'] == c and n['kind'] == 'video'], lambda n: n['duration'])]
    for c, lane in vlanes:
        items = []
        for n in lane:
            d = int(round(n['duration'] * F))
            cid = f'v-{fid(n)}'; clipid[n['path']] = cid
            full = n['path'] not in seen; seen.add(n['path'])
            ach = min(int(n['info'].get('audio_channels') or 2), 2)
            links = ''.join(f'<link><linkclipref>{x}</linkclipref><mediatype>{"video" if x.startswith("v-") else "audio"}</mediatype></link>'
                            for x in [cid] + [f'a{ch_}-{fid(n)}' for ch_ in range(1, ach + 1)])
            items.append(f'<clipitem id="{cid}"><name>{E(n["name"])}</name><duration>{d}</duration>{rate}<start>{n["start_frame"]}</start>'
                         f'<end>{n["start_frame"] + d}</end><in>0</in><out>{d}</out>{file_el(n, full)}{links}{lab(n)}</clipitem>')
        vtracks.append(f'<track>{"".join(items)}</track>')
    alanes = [(c, ln) for c in acams for ln in lanes([n for n in placed if n['cam'] == c], lambda n: n.get('duration_corrected') or n['duration'])]
    for c, lane in alanes:
        pair = []
        for ch_ in (1, 2):
            items = []
            for n in lane:
                if ch_ > min(int(n['info'].get('audio_channels') or 2), 2):
                    continue
                d = int(round((n.get('duration_corrected') or n['duration']) * F))
                full = n['path'] not in seen; seen.add(n['path'])
                items.append(f'<clipitem id="a{ch_}-{fid(n)}"><name>{E(os.path.basename(n.get("corrected_path") or n["path"]))}</name>'
                             f'<duration>{d}</duration>{rate}<start>{n["start_frame"]}</start><end>{n["start_frame"] + d}</end>'
                             f'<in>0</in><out>{d}</out>{file_el(n, full)}<sourcetrack><mediatype>audio</mediatype>'
                             f'<trackindex>{ch_}</trackindex></sourcetrack>{lab(n)}</clipitem>')
            pair.append(items)
        # one stereo track per lane when every clip on it is one mic pair; recorders with two mics stay dual mono
        st = all(core.stereo_ok(n['info'] | {'path': n['path']}, 0.0, min(n['duration'], 600.0)) for n in lane)
        atracks.append(core.audio_track_pair(*pair, stereo=st))
    total = max(n['start_frame'] + int(round((n.get('duration_corrected') or n['duration']) * F)) for n in placed)
    W, H = next(((n['info']['width'], n['info']['height']) for n in placed if n['kind'] == 'video'), (1920, 1080))
    seq = (f'<sequence id="{uuid.uuid4()}"><name>{E(name)}</name><duration>{total}</duration>{rate}'
           f'<timecode>{rate}<string>00:00:00:00</string><frame>0</frame><displayformat>NDF</displayformat></timecode>'
           f'<media><video><format><samplecharacteristics>{rate}<width>{W}</width><height>{H}</height>'
           f'<pixelaspectratio>square</pixelaspectratio></samplecharacteristics></format>{"".join(vtracks)}</video>'
           f'<audio>{"".join(atracks)}</audio></media></sequence>')
    # bins (one per camera + audio) with master clips that point at the same files, like PluralEyes
    bins = ''.join(f'<bin><name>{E(c)}</name><children>' + ''.join(
        f'<clip id="m-{fid(n)}"><name>{E(n["name"])}</name><duration>{int(round(n["duration"] * F))}</duration>{rate}'
        f'<media>{"<video><track><clipitem id=" + chr(34) + "mv-" + fid(n) + chr(34) + "><name>" + E(n["name"]) + "</name>" + file_el(n, False) + "</clipitem></track></video>" if n["kind"] == "video" else ""}'
        f'<audio><track><clipitem id="ma-{fid(n)}"><name>{E(n["name"])}</name>{file_el(n, False)}</clipitem></track></audio></media>{lab(n)}</clip>'
        for n in placed if n['cam'] == c) + '</children></bin>' for c in acams)
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4"><project>'
           f'<name>{E(os.path.splitext(os.path.basename(out_path))[0])}</name><children>{seq}{bins}</children></project></xmeml>\n')
    open(out_path, 'w', encoding='utf-8').write(xml)
    return {'path': out_path, 'nle_path': core.nle_path(out_path), 'tracks_video': len(vtracks), 'tracks_audio': len(atracks),
            'duration_s': round(total / F, 2), 'fps': res['fps'], 'labels': col}
