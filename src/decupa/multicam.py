"""decupa.multicam — PluralEyes-style multicam sync from the clips' own audio.

  sync_cameras(cameras, external)   folders/files per camera (+ optional external recorder files) → every clip placed on
                                    one timeline by its audio, external recorder clock drift measured and corrected,
                                    exported as an FCP7 XML that Premiere and Resolve import ("Synced Sequence").

How it aligns (all from audio, no timecode needed):
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

from decupa import core

SR = 8000
ENV = 100                      # envelope rate (10 ms)
AUDIO_EXT = {'.wav', '.mp3', '.m4a', '.aif', '.aiff', '.flac', '.bwf', '.ogg', '.aac'}


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


# ───────────────────────────── inputs ─────────────────────────────
def _files(spec, exts):
    out = []
    for p in ([spec] if isinstance(spec, str) else spec):
        lp = core.local_path(p)
        if os.path.isdir(lp):
            for dp, dn, fn in os.walk(lp):
                dn[:] = [x for x in dn if x.lower() not in core.PROXY_DIRS and not x.startswith(('.', '_'))]
                out += [os.path.join(dp, f) for f in sorted(fn) if os.path.splitext(f)[1].lower() in exts
                        and not f.startswith('._') and not core.is_proxy(os.path.join(dp, f))]
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
    out_dir = out_dir or os.path.join(os.path.dirname(src), 'decupa_drift_corrected')
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
def sync_cameras(cameras, external=None, fps=None, correct_drift=True, progress=lambda *_: None, drift_dir=None):
    nodes = collect(cameras, external)
    if len(nodes) < 2:
        raise ValueError('need at least two files with audio')
    place(nodes, progress=progress)
    placed = [n for n in nodes if n.get('t0') is not None]
    ext = [n for n in placed if n['kind'] == 'audio']
    drift = None
    if ext:
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
    return {'fps': fps, 'nodes': nodes, 'drift': drift,
            'unplaced': [n['name'] for n in nodes if n.get('t0') is None]}


def summary(res):
    rows = []
    for n in sorted(res['nodes'], key=lambda n: (n['cam'], n.get('t0') if n.get('t0') is not None else 1e12)):
        rows.append({'cam': n['cam'], 'clip': n['name'], 'start_s': round(n['t0'], 3) if n.get('t0') is not None else None,
                     'start_frame': n.get('start_frame'), 'duration': round(n['duration'], 2), 'method': n.get('method', 'anchor'),
                     'confidence': n.get('conf'), 'placed_from': n.get('via')})
    return {'fps': res['fps'], 'clips': rows, 'drift': res['drift'], 'unplaced': res['unplaced']}


# ───────────────────────────── export (PluralEyes-compatible layout) ─────────────────────────────
def export_xml(res, out_path, name='Synced Sequence'):
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
    for c in vcams:
        items = []
        for n in sorted([n for n in placed if n['cam'] == c and n['kind'] == 'video'], key=lambda n: n['t0']):
            d = int(round(n['duration'] * F))
            cid = f'v-{fid(n)}'; clipid[n['path']] = cid
            full = n['path'] not in seen; seen.add(n['path'])
            ach = min(int(n['info'].get('audio_channels') or 2), 2)
            links = ''.join(f'<link><linkclipref>{x}</linkclipref><mediatype>{"video" if x.startswith("v-") else "audio"}</mediatype></link>'
                            for x in [cid] + [f'a{ch_}-{fid(n)}' for ch_ in range(1, ach + 1)])
            items.append(f'<clipitem id="{cid}"><name>{E(n["name"])}</name><duration>{d}</duration>{rate}<start>{n["start_frame"]}</start>'
                         f'<end>{n["start_frame"] + d}</end><in>0</in><out>{d}</out>{file_el(n, full)}{links}</clipitem>')
        vtracks.append(f'<track>{"".join(items)}</track>')
    for c in acams:
        for ch_ in (1, 2):
            items = []
            for n in sorted([n for n in placed if n['cam'] == c], key=lambda n: n['t0']):
                if ch_ > min(int(n['info'].get('audio_channels') or 2), 2):
                    continue
                d = int(round((n.get('duration_corrected') or n['duration']) * F))
                full = n['path'] not in seen; seen.add(n['path'])
                items.append(f'<clipitem id="a{ch_}-{fid(n)}"><name>{E(os.path.basename(n.get("corrected_path") or n["path"]))}</name>'
                             f'<duration>{d}</duration>{rate}<start>{n["start_frame"]}</start><end>{n["start_frame"] + d}</end>'
                             f'<in>0</in><out>{d}</out>{file_el(n, full)}<sourcetrack><mediatype>audio</mediatype>'
                             f'<trackindex>{ch_}</trackindex></sourcetrack></clipitem>')
            if items:
                atracks.append(f'<track>{"".join(items)}</track>')
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
        f'<audio><track><clipitem id="ma-{fid(n)}"><name>{E(n["name"])}</name>{file_el(n, False)}</clipitem></track></audio></media></clip>'
        for n in placed if n['cam'] == c) + '</children></bin>' for c in acams)
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4"><project>'
           f'<name>{E(os.path.splitext(os.path.basename(out_path))[0])}</name><children>{seq}{bins}</children></project></xmeml>\n')
    open(out_path, 'w', encoding='utf-8').write(xml)
    return {'path': out_path, 'nle_path': core.nle_path(out_path), 'tracks_video': len(vtracks), 'tracks_audio': len(atracks),
            'duration_s': round(total / F, 2), 'fps': res['fps']}
