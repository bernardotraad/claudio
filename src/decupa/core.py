"""decupa.core — the "eyes and ears" for footage logging.

Everything is computed from the real media (no guessing):
  probe      ffprobe: fps, duration, size, rotation, source timecode, audio channels
  audio      16 kHz mono RMS envelope → speech/silence, clipping
  picture    one decode pass: full-rate tiny frames (cuts, frame accurate) + 4 fps 320 px frames
             (sharpness, exposure, global motion via phase correlation → static / pan / tilt / shake)
  speech     faster-whisper (CUDA) with word timestamps
  takes      utterances grouped by similarity → repeated attempts of the same line, best one ranked
  broll      no-speech material segmented into usable windows by stability, focus and exposure
  images     contact sheets / timeline strips (thumbs + waveform + words) / full frames, timecode burned in
  export     frame-accurate cut plan → FCP7 XML (Premiere + Resolve), CMX3600 EDL, JSON

Indexes are cached in ~/.cache/decupa/<key>/ (key = path + size + mtime).
"""
import difflib, hashlib, io, json, math, os, re, subprocess, threading, time, unicodedata, urllib.parse
from fractions import Fraction

import numpy as np
from PIL import Image, ImageDraw, ImageFont

CACHE = os.path.expanduser(os.environ.get('DECUPA_CACHE', '~/.cache/decupa'))
WHISPER_MODEL = os.environ.get('DECUPA_WHISPER', 'large-v3-turbo')
INDEX_VERSION = 5
SR = 16000          # audio analysis rate
HOP = 0.02          # RMS hop (s)
QFPS = 4            # quality-analysis frame rate
VIDEO_EXT = {'.mp4', '.mov', '.mxf', '.mkv', '.avi', '.m4v', '.mts', '.m2ts', '.webm', '.braw', '.crm', '.r3d', '.mpg'}


# ───────────────────────────── paths / platform ─────────────────────────────
import platform, sys
IS_MAC = sys.platform == 'darwin'
IS_WIN = os.name == 'nt'
IS_WSL = sys.platform.startswith('linux') and 'microsoft' in platform.uname().release.lower()
DISTRO = os.environ.get('WSL_DISTRO_NAME', 'Ubuntu')


def local_path(p):
    """Any path the user/NLE gives → path this process can open.
    In WSL accepts D:\\x\\y.mp4, D:/x/y.mp4, \\\\wsl.localhost\\Ubuntu\\home\\...; elsewhere just normalises."""
    p = p.strip().strip('"')
    if IS_WSL:
        m = re.match(r'^\\\\wsl(?:\.localhost|\$)\\[^\\]+(\\.*)$', p, re.I)
        if m:
            return m.group(1).replace('\\', '/')
        m = re.match(r'^([A-Za-z]):[\\/](.*)$', p)
        if m:
            return f'/mnt/{m.group(1).lower()}/' + m.group(2).replace('\\', '/')
    if p.startswith('file://'):
        p = urllib.parse.unquote(re.sub(r'^file://(localhost)?', '', p))
        if IS_WIN and re.match(r'^/[A-Za-z]:', p):
            p = p[1:]
    return os.path.abspath(os.path.expanduser(p))


def nle_path(p):
    """Local path → the path Premiere/Resolve see. Only differs in WSL (the NLE runs on Windows)."""
    if not IS_WSL:
        return p
    m = re.match(r'^/mnt/([a-z])/(.*)$', p)
    if m:
        return f'{m.group(1).upper()}:\\' + m.group(2).replace('/', '\\')
    return f'\\\\wsl.localhost\\{DISTRO}' + p.replace('/', '\\')


def file_url(p):
    w = nle_path(p)
    if w.startswith('\\\\'):
        return 'file:' + urllib.parse.quote(w.replace('\\', '/'))
    if re.match(r'^[A-Za-z]:\\', w):
        return 'file://localhost/' + urllib.parse.quote(w.replace('\\', '/'), safe='/')
    return 'file://localhost' + urllib.parse.quote(w, safe='/')


def cache_dir(path):
    st = os.stat(path)
    key = hashlib.sha1(f'{path}|{st.st_size}|{int(st.st_mtime)}'.encode()).hexdigest()[:16]
    d = os.path.join(CACHE, f'{re.sub(r"[^A-Za-z0-9_.-]", "_", os.path.basename(path))[:40]}-{key}')
    os.makedirs(d, exist_ok=True)
    return d


# ───────────────────────────── timecode ─────────────────────────────
def tc_to_frames(tc, fps_int, drop):
    h, m, s, f = [int(x) for x in re.split(r'[:;.]', tc)]
    if not drop:
        return ((h * 60 + m) * 60 + s) * fps_int + f
    d = 2 if fps_int == 30 else 4
    tm = h * 60 + m
    return ((h * 3600 + m * 60 + s) * fps_int + f) - d * (tm - tm // 10)


def frames_to_tc(n, fps_int, drop):
    if not drop:
        f = n % fps_int; s = n // fps_int
        return f'{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}:{f:02d}'
    d = 2 if fps_int == 30 else 4
    per10 = fps_int * 600 - d * 9
    per1 = fps_int * 60 - d
    q, r = divmod(n, per10)
    n += d * 9 * q + (d * ((r - d) // per1) if r > d else 0)
    f = n % fps_int; s = n // fps_int
    return f'{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d};{f:02d}'


class Clock:
    """Seconds ↔ frames ↔ source timecode for one clip."""

    def __init__(self, fps, start_tc):
        self.fps = Fraction(fps)
        self.fps_int = round(float(self.fps))
        self.ntsc = self.fps.denominator == 1001
        self.drop = ';' in (start_tc or '') or False
        self.start = tc_to_frames(start_tc, self.fps_int, self.drop) if start_tc else 0

    def frame(self, sec, mode='round'):
        x = sec * float(self.fps)
        return int(math.floor(x + 1e-6) if mode == 'floor' else math.ceil(x - 1e-6) if mode == 'ceil' else round(x))

    def sec(self, frame):
        return frame / float(self.fps)

    def tc(self, frame):
        return frames_to_tc(self.start + frame, self.fps_int, self.drop)

    def stamp(self, sec):
        """Human label: source TC + clip-relative seconds."""
        return f'{self.tc(self.frame(sec, "floor"))}  {sec:7.2f}s'


# ───────────────────────────── probe ─────────────────────────────
def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, check=True, **kw)


def probe(path):
    j = json.loads(run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', path]).stdout)
    v = next((s for s in j['streams'] if s['codec_type'] == 'video' and not s.get('disposition', {}).get('attached_pic')), None)
    auds = [s for s in j['streams'] if s['codec_type'] == 'audio']
    tags = [j['format'].get('tags', {})] + [s.get('tags', {}) for s in j['streams']]
    start_tc = next((t[k] for t in tags for k in t if k.lower() == 'timecode'), None)
    info = {'path': path, 'nle_path': nle_path(path), 'name': os.path.basename(path),
            'duration': float(j['format'].get('duration') or 0), 'has_video': bool(v), 'start_tc': start_tc,
            'audio_streams': len(auds), 'audio_channels': sum(int(a.get('channels', 0)) for a in auds) or 0,
            'audio_rate': int(auds[0]['sample_rate']) if auds else None}
    if v:
        fr = v.get('r_frame_rate') or v.get('avg_frame_rate')
        afr = v.get('avg_frame_rate')
        fps = Fraction(fr) if fr and fr != '0/0' else Fraction(afr)
        if afr and afr != '0/0' and abs(float(Fraction(afr)) - float(fps)) > 0.5:
            fps = Fraction(afr)  # VFR phone footage: r_frame_rate can be 90000/1
        fps = fps.limit_denominator(1001)
        rot = 0
        for sd in v.get('side_data_list', []):
            if 'rotation' in sd:
                rot = int(sd['rotation'])
        rot = int(v.get('tags', {}).get('rotate', rot))
        w, h = int(v['width']), int(v['height'])
        if abs(rot) % 180 == 90:
            w, h = h, w
        info.update(fps=f'{fps.numerator}/{fps.denominator}', fps_float=round(float(fps), 3), width=w, height=h,
                    codec=v.get('codec_name'), pix_fmt=v.get('pix_fmt'), rotation=rot,
                    vfr=bool(afr and v.get('r_frame_rate') and afr != v.get('r_frame_rate')))
        if v.get('duration'):
            info['duration'] = float(v['duration'])
        info['frames'] = int(round(info['duration'] * float(fps)))
    return info


def clock_of(info):
    return Clock(Fraction(info.get('fps', '30/1')), info.get('start_tc'))


# ───────────────────────────── audio ─────────────────────────────
def load_pcm(d):
    return np.fromfile(os.path.join(d, 'a16.pcm'), np.int16).astype(np.float32) / 32768


def analyze_audio(path, d, info):
    pcm = os.path.join(d, 'a16.pcm')
    if not info['audio_streams']:
        return None
    if not os.path.exists(pcm):
        run(['ffmpeg', '-y', '-loglevel', 'error', '-i', path, '-map', '0:a:0', '-ac', '1', '-ar', str(SR),
             '-f', 's16le', pcm])
    x = load_pcm(d)
    hop = int(SR * HOP)
    n = len(x) // hop
    fr = x[:n * hop].reshape(n, hop)
    rms = np.sqrt((fr ** 2).mean(1) + 1e-12)
    db = 20 * np.log10(rms + 1e-9)
    peak = np.abs(fr).max(1)
    np.save(os.path.join(d, 'rms_db.npy'), db.astype(np.float32))
    floor = float(np.percentile(db, 10))
    thr = max(floor + 10, -50.0)
    loud = db > thr
    # silences ≥ 0.4 s
    sil, i = [], 0
    while i < n:
        if not loud[i]:
            j = i
            while j < n and not loud[j]:
                j += 1
            if (j - i) * HOP >= 0.4:
                sil.append([round(i * HOP, 2), round(j * HOP, 2)])
            i = j
        else:
            i += 1
    return {'noise_floor_db': round(floor, 1), 'silence_threshold_db': round(thr, 1),
            'peak_db': round(float(20 * np.log10(peak.max() + 1e-9)), 1),
            'clipped_ms': int((peak > 0.995).sum() * HOP * 1000), 'silences': sil,
            'speech_ratio': round(float(loud.mean()), 3)}


# ───────────────────────────── picture ─────────────────────────────
def _phase_shift(a, b):
    """Global translation between two frames (px at analysis scale) + peak strength."""
    win = np.outer(np.hanning(a.shape[0]), np.hanning(a.shape[1]))
    A, B = np.fft.fft2(a * win), np.fft.fft2(b * win)
    R = A * np.conj(B)
    r = np.fft.ifft2(R / (np.abs(R) + 1e-9)).real
    y, x = np.unravel_index(np.argmax(r), r.shape)
    if y > a.shape[0] // 2: y -= a.shape[0]
    if x > a.shape[1] // 2: x -= a.shape[1]
    return x, y, float(r.max())


def analyze_picture(path, d, info, progress=lambda *_: None):
    """One decode: tiny full-rate frames (cut detection) and 4 fps 320-px frames (quality + motion)."""
    W, H = info['width'], info['height']
    qw = 320; qh = max(2, int(round(qw * H / W / 2)) * 2)
    tw, th = 64, max(2, int(round(64 * H / W / 2)) * 2)
    qraw = os.path.join(d, 'q.gray')
    cmd = ['ffmpeg', '-y', '-loglevel', 'error', '-threads', '0', '-i', path, '-filter_complex',
           f'[0:v:0]split=2[a][b];[a]scale={tw}:{th}:flags=area,format=gray[t];'
           f'[b]fps={QFPS},scale={qw}:{qh}:flags=area,format=gray[q]',
           '-map', '[t]', '-f', 'rawvideo', 'pipe:1', '-map', '[q]', '-f', 'rawvideo', qraw]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    fsz = tw * th
    diffs, lum, prev, k = [], [], None, 0
    total = max(1, info.get('frames', 1))
    while True:
        buf = p.stdout.read(fsz * 256)
        if not buf:
            break
        fr = np.frombuffer(buf[:len(buf) // fsz * fsz], np.uint8).reshape(-1, th, tw).astype(np.int16)
        if prev is not None:
            fr_all = np.concatenate([prev[None], fr])
        else:
            fr_all = fr
            diffs.append(0.0)
        dd = np.abs(np.diff(fr_all, axis=0)).mean((1, 2))
        diffs.extend(dd.tolist())
        lum.extend(fr.mean((1, 2)).tolist())
        prev = fr[-1]
        k += len(fr)
        progress('picture', k / total)
    p.wait()
    diffs = np.array(diffs, np.float32)
    fps = float(Fraction(info['fps']))
    # cuts: spike vs local median (frame accurate)
    cuts = []
    for i in range(1, len(diffs)):
        lo, hi = max(0, i - 12), min(len(diffs), i + 13)
        med = float(np.median(np.r_[diffs[lo:i], diffs[i + 1:hi]])) if hi - lo > 1 else 0
        if diffs[i] > 14 and diffs[i] > 4 * med + 3 and diffs[i] == diffs[max(0, i - 2):i + 3].max():
            # a real cut is a single jump between two steady pictures; an object whipping past the lens
            # (clapper, hand) is a burst of big changes on either side
            before, after = diffs[max(1, i - 5):i], diffs[i + 1:i + 6]
            if (not len(before) or before.max() < diffs[i] / 3) and (not len(after) or after.max() < diffs[i] / 3):
                cuts.append(i)
    # quality at 4 fps
    q = np.fromfile(qraw, np.uint8)
    q = q[:len(q) // (qw * qh) * qw * qh].reshape(-1, qh, qw).astype(np.float32)
    os.remove(qraw)
    rows = []
    small_prev = None
    for i, f in enumerate(q):
        lap = (-4 * f[1:-1, 1:-1] + f[:-2, 1:-1] + f[2:, 1:-1] + f[1:-1, :-2] + f[1:-1, 2:])
        sharp = float(lap.var())
        mean = float(f.mean())
        hi = float((f > 250).mean()); lo_ = float((f < 6).mean())
        small = f[::2, ::2]
        if small_prev is not None:
            dx, dy, conf = _phase_shift(small_prev, small)
        else:
            dx = dy = 0; conf = 1.0
        small_prev = small
        rows.append((i / QFPS, sharp, mean, hi, lo_, dx * 2, dy * 2, conf))
    m = np.array(rows, np.float32) if rows else np.zeros((0, 8), np.float32)
    np.save(os.path.join(d, 'q.npy'), m)
    return {'cuts_frames': cuts, 'cuts_sec': [round(c / fps, 3) for c in cuts], 'samples': len(m),
            'luma_mean': round(float(np.mean(lum)) if lum else 0, 1)}


def motion_labels(m, width_px=320):
    """Per 4-fps sample: label camera behaviour from global shift (px/s at 320 px wide) and residual change."""
    if not len(m):
        return []
    vx = m[:, 5] * QFPS; vy = m[:, 6] * QFPS; conf = m[:, 7]
    # shake = high-frequency jitter of the shift around its smoothed trend
    def smooth(v, k=5):
        return np.convolve(np.pad(v, (k // 2, k // 2), mode='edge'), np.ones(k) / k, mode='valid')
    jit = np.hypot(vx - smooth(vx), vy - smooth(vy))
    sx, sy = smooth(vx), smooth(vy)
    out = []
    for i in range(len(m)):
        if conf[i] < 0.04:
            lab = 'chaotic'          # no coherent global motion (whip, subject fills frame, cut)
        elif jit[i] > 40:
            lab = 'shaky'
        elif abs(sx[i]) > 6 and abs(sx[i]) >= abs(sy[i]):
            lab = 'pan_' + ('left' if sx[i] > 0 else 'right')
        elif abs(sy[i]) > 6:
            lab = 'tilt_' + ('up' if sy[i] > 0 else 'down')
        else:
            lab = 'static'
        out.append((lab, float(jit[i]), float(sx[i]), float(sy[i])))
    return out


# ───────────────────────────── speech ─────────────────────────────
_model, _model_lock = None, threading.Lock()


def _preload_cuda_libs():
    """cuBLAS/cuDNN come from pip wheels (nvidia-cublas-cu12, nvidia-cudnn-cu12); load them globally so
    CTranslate2 finds them without LD_LIBRARY_PATH (cuDNN dlopens its sub-libraries by soname)."""
    import ctypes, glob, site
    for sp in site.getsitepackages() + [site.getusersitepackages()]:
        for sub in ('cuda_nvrtc', 'cublas', 'cudnn'):
            for so in sorted(glob.glob(os.path.join(sp, 'nvidia', sub, 'lib', 'lib*.so*')), key=lambda p: ('Lt' not in p, p)):
                try:
                    ctypes.CDLL(so, mode=ctypes.RTLD_GLOBAL)
                except OSError:
                    pass


def whisper_backend():
    """'mlx' on Apple Silicon when mlx-whisper is installed, else faster-whisper (CUDA if usable, CPU otherwise)."""
    if os.environ.get('DECUPA_BACKEND'):
        return os.environ['DECUPA_BACKEND']
    if IS_MAC and platform.machine() == 'arm64':
        try:
            import mlx_whisper  # noqa: F401
            return 'mlx'
        except ImportError:
            pass
    return 'faster-whisper'


def whisper():
    """faster-whisper model; CUDA is verified with a real encode (missing cuBLAS only fails at inference)."""
    global _model
    with _model_lock:
        if _model is None:
            _preload_cuda_libs()
            from faster_whisper import WhisperModel
            try:
                m = WhisperModel(WHISPER_MODEL, device='cuda', compute_type='float16')
                list(m.transcribe(np.zeros(SR, np.float32), language='en')[0])
                _model = m
            except Exception:
                _model = WhisperModel(WHISPER_MODEL, device='cpu', compute_type='int8')
        return _model


PROMPT_PT = 'Hã, é... então, tipo, né? Vamos de novo. Corta. Gravando.'


def transcribe(d, lang, progress=lambda *_: None, duration=1):
    audio = load_pcm(d)
    prompt = PROMPT_PT if (lang or '').startswith('pt') else None
    words, out = [], []
    if whisper_backend() == 'mlx':
        import mlx_whisper
        r = mlx_whisper.transcribe(audio, path_or_hf_repo=os.environ.get('DECUPA_MLX_MODEL', 'mlx-community/whisper-large-v3-turbo'),
                                   language=lang or None, word_timestamps=True, condition_on_previous_text=False,
                                   initial_prompt=prompt, verbose=None)
        for s in r['segments']:
            words.extend({'w': w['word'].strip(), 's': round(float(w['start']), 3), 'e': round(float(w['end']), 3),
                          'p': round(float(w.get('probability', 1)), 3)} for w in s.get('words', []) if w['word'].strip())
            out.append({'s': round(float(s['start']), 2), 'e': round(float(s['end']), 2), 'text': s['text'].strip()})
        progress('speech', 1)
        return {'language': r.get('language', lang), 'segments': out, 'words': words}
    segs, info = whisper().transcribe(
        audio, language=lang or None, word_timestamps=True, vad_filter=True,
        vad_parameters={'min_silence_duration_ms': 300},
        condition_on_previous_text=False,   # keep repeated takes instead of "deduplicating" them
        initial_prompt=prompt)
    for s in segs:
        ws = [{'w': w.word.strip(), 's': round(w.start, 3), 'e': round(w.end, 3), 'p': round(w.probability, 3)}
              for w in (s.words or []) if w.word.strip()]
        words.extend(ws)
        out.append({'s': round(float(s.start), 2), 'e': round(float(s.end), 2), 'text': s.text.strip()})
        progress('speech', s.end / max(duration, 1))
    return {'language': info.language, 'segments': out, 'words': words}


def norm(w):
    return re.sub(r'[^a-z0-9%]', '', unicodedata.normalize('NFKD', w.lower()).encode('ascii', 'ignore').decode())


FILLERS = {'ha', 'han', 'hm', 'hmm', 'eh', 'ehh', 'ahn', 'uh', 'uhm', 'um', 'hum'}   # not 'é' (verb) / 'né' / 'tipo' (often meaningful)


def utterances(words, gap=0.7):
    """Split word stream at pauses ≥ gap — each utterance is one candidate line/attempt."""
    out, cur = [], []
    for w in words:
        if cur and w['s'] - cur[-1]['e'] >= gap:
            out.append(cur); cur = []
        cur.append(w)
    if cur:
        out.append(cur)
    return [{'s': u[0]['s'], 'e': u[-1]['e'], 'text': ' '.join(x['w'] for x in u),
             'n': len(u), 'conf': round(float(np.mean([x['p'] for x in u])), 3),
             'fillers': sum(norm(x['w']) in FILLERS for x in u)} for u in out]


def group_takes(utts, sim=0.55):
    """Cluster repeated attempts of the same line. A false start (prefix of a later attempt) joins its group."""
    toks = [[norm(w) for w in u['text'].split() if norm(w)] for u in utts]
    groups = []
    for i, t in enumerate(toks):
        if not t:
            continue
        best, bs = None, 0
        for g in groups:
            ref = toks[g[-1]]
            r = difflib.SequenceMatcher(None, ref, t, autojunk=False).ratio()
            short, long_ = (t, ref) if len(t) < len(ref) else (ref, t)
            pref = difflib.SequenceMatcher(None, short, long_[:len(short)], autojunk=False).ratio() if len(short) >= 3 else 0
            score = max(r, pref * 0.95)
            if score > bs:
                best, bs = g, score
        if best is not None and bs >= sim and i - best[-1] <= 6:
            best.append(i)
        else:
            groups.append([i])
    return groups


# ───────────────────────────── index (orchestration) ─────────────────────────────
def build_index(path, lang='pt', progress=lambda *_: None, force=False):
    path = local_path(path)
    d = cache_dir(path)
    ip = os.path.join(d, 'index.json')
    if os.path.exists(ip) and not force:
        idx = json.load(open(ip))
        if idx.get('version') == INDEX_VERSION:
            return idx
    t0 = time.time()
    info = probe(path)
    progress('probe', 1)
    idx = {'version': INDEX_VERSION, 'info': info, 'cache': d}
    idx['audio'] = analyze_audio(path, d, info)
    progress('audio', 1)
    if info['has_video']:
        idx['picture'] = analyze_picture(path, d, info, progress)
    sp = None
    if idx['audio'] and idx['audio']['speech_ratio'] > 0.02:
        sp = transcribe(d, lang, progress, info['duration'])
        json.dump(sp['words'], open(os.path.join(d, 'words.json'), 'w'), ensure_ascii=False)
    idx['speech'] = {'language': sp['language'], 'segments': sp['segments'], 'n_words': len(sp['words'])} if sp else None
    idx['seconds_to_index'] = round(time.time() - t0, 1)
    json.dump(idx, open(ip, 'w'), ensure_ascii=False, indent=1)
    return idx


def load_words(idx):
    p = os.path.join(idx['cache'], 'words.json')
    return json.load(open(p)) if os.path.exists(p) else []


def load_quality(idx):
    p = os.path.join(idx['cache'], 'q.npy')
    return np.load(p) if os.path.exists(p) else np.zeros((0, 8), np.float32)


def sharp_ref(m):
    """Clip-relative sharpness reference: 90th percentile (so 'soft' means soft for THIS camera/lens)."""
    return float(np.percentile(m[:, 1], 90)) if len(m) else 1.0


def range_quality(idx, s, e):
    m = load_quality(idx)
    if not len(m):
        return {}
    sel = m[(m[:, 0] >= s) & (m[:, 0] <= e)]
    if not len(sel):
        sel = m[np.argmin(np.abs(m[:, 0] - s))][None]
    ref = sharp_ref(m)
    labs = motion_labels(m)
    li = [i for i in range(len(m)) if s <= m[i, 0] <= e] or [int(np.argmin(np.abs(m[:, 0] - s)))]
    mot = {}
    for i in li:
        mot[labs[i][0]] = mot.get(labs[i][0], 0) + 1
    return {'sharpness': round(float(np.median(sel[:, 1]) / max(ref, 1e-6)), 2),
            'luma': round(float(sel[:, 2].mean()), 1),
            'clipped_highlights_pct': round(float(sel[:, 3].mean() * 100), 1),
            'crushed_blacks_pct': round(float(sel[:, 4].mean() * 100), 1),
            'motion': max(mot, key=mot.get), 'motion_mix': {k: round(v / len(li), 2) for k, v in mot.items()},
            'shake': round(float(np.median([labs[i][1] for i in li])), 1)}


# ───────────────────────────── slate (cena / take / ação / corta) ─────────────────────────────
NUM = {'um': 1, 'uma': 1, 'one': 1, 'dois': 2, 'duas': 2, 'two': 2, 'tres': 3, 'three': 3, 'quatro': 4, 'four': 4,
       'cinco': 5, 'five': 5, 'seis': 6, 'six': 6, 'sete': 7, 'seven': 7, 'oito': 8, 'eight': 8, 'nove': 9,
       'nine': 9, 'dez': 10, 'ten': 10, 'onze': 11, 'doze': 12, 'treze': 13, 'quatorze': 14, 'catorze': 14, 'quinze': 15}
SCENE_W = {'cena', 'sena', 'scene', 'seena'}
TAKE_W = {'take', 'tomada', 'teik', 'teic', 'tek'}
PART_W = {'parte', 'part'}
ACTION_W = {'acao', 'action', 'gravando', 'rolando'}
ABORT_P = ('perai', 'peraai', 'peraii', 'espera', 'denovo', 'maisuma', 'errei', 'desculpa', 'calma', 'volta', 'naonao',
           'wait', 'holdon', 'again', 'sorry', 'onemore', 'letsgoagain', 'mybad')
CUT_W = {'corta', 'corte', 'cut', 'cortou'}
SHOT_W = ('contraplano', 'primeiro plano', 'plano geral', 'plano medio', 'plano detalhe', 'close', 'detalhe', 'geral', 'aberto', 'fechado')


def _num(tok):
    t = norm(tok)
    if re.fullmatch(r'\d+', t):
        return int(t)
    return NUM.get(t)


def slate(idx):
    """Parse the clapper call-out from the transcript. Action runs from the end of the last 'ação' (after any
    slate) to the start of the first 'corta' after it. Crew chatter outside that range is not performance."""
    W = load_words(idx)
    dur = idx['info']['duration']
    toks = [norm(w['w']) for w in W]
    out = {'scene': None, 'take': None, 'part': None, 'shot': None, 'slate_at': None}
    for i, t in enumerate(toks):
        if t in SCENE_W and i + 1 < len(toks) and _num(W[i + 1]['w']) is not None and out['scene'] is None:
            out['scene'] = W[i + 1]['w'].strip('.,') if re.match(r'\d+[.,]\d', W[i + 1]['w']) else _num(W[i + 1]['w'])
            out['slate_at'] = W[i]['s']
            tail = ' '.join(toks[i:i + 14])
            last = i + 1
            for j in range(i + 2, min(i + 12, len(toks) - 1)):
                if toks[j] in TAKE_W and out['take'] is None:
                    out['take'] = _num(W[j + 1]['w']); last = j + 1
                if toks[j] in PART_W and out['part'] is None:
                    out['part'] = _num(W[j + 1]['w']); last = j + 1
                if any(norm(x.split()[-1]) == toks[j] for x in SHOT_W):
                    last = j
            out['slate_end'] = W[min(last, len(W) - 1)]['e']
            for s in SHOT_W:
                if norm(s.replace(' ', '')) in tail.replace(' ', ''):
                    out['shot'] = s
                    break
    after = out['slate_at'] or 0
    acts = [W[i] for i, t in enumerate(toks) if t in ACTION_W and W[i]['s'] >= after]
    a_start = acts[-1]['e'] if acts else out.get('slate_end')
    # a second 'ação' long after the first one is part of the take only if nothing was said in between
    cuts = [W[i] for i, t in enumerate(toks) if t in CUT_W and a_start is not None and W[i]['s'] > a_start]
    a_end = cuts[0]['s'] if cuts else None
    out['action'] = [round(a_start, 2) if a_start is not None else 0.0, round(a_end, 2) if a_end else round(dur, 2)]
    out['has_slate'] = out['scene'] is not None
    out['has_action_call'] = a_start is not None
    out['has_cut_call'] = a_end is not None
    return out


def performance_words(idx):
    """Words spoken inside the action range (excludes slate, 'ação', 'corta', crew chatter around them)."""
    sl = slate(idx)
    s, e = sl['action']
    return [w for w in load_words(idx) if w['s'] >= s and w['e'] <= e and norm(w['w']) not in ACTION_W | CUT_W]


def scenes_report(idxs, prefer='last'):
    """Cross-clip decupagem: group clips by slate (scene, part, shot), compare the performed lines of each take,
    rank takes. Clips without slate/speech are listed as b-roll candidates."""
    groups, loose = {}, []
    for idx in idxs:
        sl = slate(idx)
        pw = performance_words(idx)
        text = ' '.join(w['w'] for w in pw)
        q = range_quality(idx, *sl['action'])
        flat = ''.join(norm(w['w']) for w in pw)
        aborted = any(p in flat for p in ABORT_P) and len(pw) < 25
        item = {'path': idx['info']['nle_path'], 'aborted': aborted, 'clip': idx['info']['name'], 'take': sl['take'],
                'action': sl['action'], 'action_len': round(sl['action'][1] - sl['action'][0], 1),
                'lines': text, 'words': len(pw), 'asr_conf': round(float(np.mean([w['p'] for w in pw])), 3) if pw else None,
                'cut_called': sl['has_cut_call'], 'picture': q}
        if sl['has_slate']:
            key = f"cena {sl['scene']}" + (f" parte {sl['part']}" if sl['part'] else '') + (f" ({sl['shot']})" if sl['shot'] else '')
            groups.setdefault(key, []).append(item)
        else:
            item['kind'] = 'dialogue_no_slate' if len(pw) > 8 else ('action_no_dialogue' if sl['has_action_call'] else 'broll_or_misc')
            loose.append(item)
    out = []
    for key, items in groups.items():
        items.sort(key=lambda x: (x['take'] or 0, x['clip']))
        toks = [[norm(w) for w in it['lines'].split() if norm(w)] for it in items]
        ok = [t for t, it in zip(toks, items) if t and not it['aborted']]
        sim = lambda a, b: difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()
        ref = max(ok, key=lambda a: sum(sim(a, b) for b in ok)) if ok else []   # consensus (medoid) version
        spoken = bool(ref) and len(ref) >= 6
        for k, (it, t) in enumerate(zip(items, toks)):
            cov = sim(ref, t) if spoken else None
            it['coverage_vs_consensus'] = round(cov, 2) if cov is not None else None
            it['score'] = round((2 * cov if spoken else 0) - 2.0 * it['aborted'] + (it['asr_conf'] or 0) * spoken
                                + 0.6 * min(it['picture'].get('sharpness', 1), 1)
                                - 0.5 * (it['picture'].get('motion') in ('shaky', 'chaotic'))
                                + 0.3 * it['cut_called'] + (0.5 * k / max(len(items) - 1, 1) if prefer == 'last' else 0), 2)
        best = max(items, key=lambda x: x['score'])
        agree = np.mean([sim(a, b) for a in ok for b in ok if a is not b]) if len(ok) > 1 else 1
        mode = 'no_dialogue (judge the picture)' if not spoken else 'improvised (lines differ between takes — judge performance)' if agree < 0.3 else 'dialogue'
        out.append({'scene': key, 'takes': len(items), 'mode': mode,
                    'best': best['clip'], 'candidates': items})
    out.sort(key=lambda g: g['scene'])
    return {'scenes': out, 'no_slate': loose,
            'how_to_verify': 'contact_sheet the best and the runner-up over their action range; timeline_strip the line delivery'}


# ───────────────────────────── takes (dialogue) ─────────────────────────────
def takes_report(idx, gap=0.7, prefer='last'):
    words = performance_words(idx) if slate(idx)['has_slate'] else load_words(idx)
    if not words:
        return {'groups': [], 'note': 'no speech found in this clip'}
    U = utterances(words, gap)
    G = group_takes(U)
    groups = []
    for gi, g in enumerate(G):
        cands = []
        longest = max(U[i]['n'] for i in g)
        for rank_in_time, i in enumerate(g):
            u = U[i]
            q = range_quality(idx, u['s'], u['e'])
            complete = u['n'] >= 0.85 * longest
            score = (2.0 * complete + 1.5 * u['conf'] - 0.4 * u['fillers'] + 0.6 * min(q.get('sharpness', 1), 1)
                     - 0.5 * (q.get('motion') in ('shaky', 'chaotic')) - 0.02 * q.get('clipped_highlights_pct', 0)
                     + (0.6 * rank_in_time / max(len(g) - 1, 1) if prefer == 'last' else 0))
            cands.append({'take': rank_in_time + 1, 's': u['s'], 'e': u['e'], 'text': u['text'], 'words': u['n'],
                          'asr_conf': u['conf'], 'fillers': u['fillers'], 'complete': complete,
                          'picture': q, 'score': round(score, 2)})
        best = max(cands, key=lambda c: c['score'])
        groups.append({'group': gi + 1, 'attempts': len(g), 'line': best['text'], 'best_take': best['take'],
                       'candidates': cands})
    return {'groups': groups, 'utterances': len(U),
            'how_to_verify': 'look at timeline_strip for the best take (start/end) and for any close runner-up'}


# ───────────────────────────── b-roll (no speech) ─────────────────────────────
def broll_windows(idx, min_len=1.5, soft=0.45, max_shake=40):
    """Split picture into shots (hard cuts) → inside each shot, runs of steady motion class that are sharp and
    exposed. Returns usable windows, best first, plus rejected stretches with the reason."""
    m = load_quality(idx)
    if not len(m):
        return {'windows': [], 'rejected': []}
    info = idx['info']
    dur = info['duration']
    ref = sharp_ref(m)
    labs = motion_labels(m)
    cuts = [0.0] + idx.get('picture', {}).get('cuts_sec', []) + [dur]
    sil = idx['audio']['silences'] if idx.get('audio') else []
    words = load_words(idx)

    def speech_in(s, e):
        return sum(1 for w in words if w['s'] < e and w['e'] > s)

    wins, rej = [], []
    for a, b in zip(cuts, cuts[1:]):
        ids = [i for i in range(len(m)) if a + 0.15 <= m[i, 0] < b - 0.15]
        if not ids:
            continue
        # per-sample verdict
        def bad(i):
            if m[i, 1] < soft * ref: return 'soft'
            if m[i, 3] > 0.25: return 'blown'
            if m[i, 4] > 0.5 or m[i, 2] < 12: return 'dark'
            if labs[i][0] == 'chaotic': return 'chaotic'
            if labs[i][1] > max_shake: return 'shaky'
            return None
        keys = [bad(i) or labs[i][0] for i in ids]
        segs, start = [], 0
        for k in range(1, len(ids) + 1):
            if k == len(ids) or keys[k] != keys[start]:
                end_t = m[ids[k], 0] if k < len(ids) else b
                segs.append((keys[start], m[ids[start], 0] if start else a, end_t))
                start = k
        # merge tiny flickers (< 0.5 s) into neighbours
        merged = []
        for lab, s, e in segs:
            if merged and (e - s < 0.5 or merged[-1][0] == lab):
                merged[-1] = (merged[-1][0], merged[-1][1], e)
            else:
                merged.append((lab, s, e))
        for lab, s, e in merged:
            s, e = round(float(s), 2), round(float(min(e, b)), 2)
            if lab in ('soft', 'blown', 'dark', 'chaotic', 'shaky'):
                if e - s >= 0.5:
                    rej.append({'s': s, 'e': e, 'reason': lab})
                continue
            if e - s < min_len:
                continue
            q = range_quality(idx, s, e)
            score = q['sharpness'] + (0.3 if lab == 'static' else 0.2) - q['shake'] / 100 - q['clipped_highlights_pct'] / 50
            wins.append({'s': s, 'e': e, 'len': round(e - s, 2), 'camera': lab, 'picture': q,
                         'speech_words': speech_in(s, e), 'score': round(score, 2)})
    wins.sort(key=lambda w: -w['score'])
    return {'windows': wins, 'rejected': rej, 'shots': len(cuts) - 1,
            'how_to_verify': 'contact_sheet each window you plan to use and describe what is in it (annotate)'}


# ───────────────────────────── cut snapping ─────────────────────────────
def snap(idx, s, e, pad_in=0.12, pad_out=0.20, mode='speech', tighten=False):
    """Frame-accurate in/out.
    speech (default keeps your breathing room): a boundary that falls inside a word (or its pad) moves OUT of it so
    no word is clipped, then nudges to the quietest audio within ±50 ms. tighten=True instead trims to
    first-word-minus-pad / last-word-plus-pad (for jump-cut talking heads).
    picture: snap to a hard cut within 6 frames so no flash frame from another shot leaks in."""
    info = idx['info']
    clk = clock_of(info)
    words = load_words(idx)
    if mode == 'speech' and words:
        dur = info['duration']
        inside = [w for w in words if w['e'] > s and w['s'] < e]
        if tighten and inside:
            first, last = inside[0], inside[-1]
            prev = [w for w in words if w['e'] <= first['s']]
            nxt = [w for w in words if w['s'] >= last['e']]
            s = max(first['s'] - pad_in, (prev[-1]['e'] + 0.04) if prev else 0)
            e = min(last['e'] + pad_out, (nxt[0]['s'] - 0.04) if nxt else dur)
        else:
            for w in words:                       # in-point inside a word → before it
                if w['s'] - pad_in < s < w['e']:
                    s = max(0, w['s'] - pad_in); break
            for w in reversed(words):             # out-point inside a word → after it
                if w['s'] < e < w['e'] + pad_out:
                    e = min(dur, w['e'] + pad_out); break
        p = os.path.join(idx['cache'], 'rms_db.npy')
        if os.path.exists(p):
            db = np.load(p)
            def free(t):
                return not any(w['s'] - 0.02 < t < w['e'] + 0.02 for w in words)
            def quiet(t):
                a_, b_ = max(0, int((t - 0.05) / HOP)), min(len(db), int((t + 0.05) / HOP) + 1)
                cand = [k * HOP for k in sorted(range(a_, b_), key=lambda k: db[k]) if free(k * HOP)]
                return cand[0] if cand else t
            s, e = quiet(s), quiet(e)
    cuts = idx.get('picture', {}).get('cuts_frames', [])
    fi, fo = clk.frame(s, 'floor'), clk.frame(e, 'ceil')
    for c in cuts:
        if 0 < c - fi <= 6 and mode == 'picture': fi = c
        if 0 <= fo - c <= 6 and c > fi: fo = c          # out stops before the next shot's first frame
        if 0 <= c - fi < 2: fi = c                         # never start on the last frame of the previous shot
    fo = min(fo, info.get('frames', fo))
    return {'in_frame': fi, 'out_frame': fo, 'in_sec': round(clk.sec(fi), 3), 'out_sec': round(clk.sec(fo), 3),
            'in_tc': clk.tc(fi), 'out_tc': clk.tc(fo), 'duration_frames': fo - fi,
            'note': 'out_frame is exclusive (first frame NOT used)'}


# ───────────────────────────── images ─────────────────────────────
def _font(size):
    for f in ['/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
              '/System/Library/Fonts/Menlo.ttc', '/System/Library/Fonts/Monaco.ttf', 'C:\\Windows\\Fonts\\consolab.ttf',
              'C:\\Windows\\Fonts\\consola.ttf']:
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def grab(path, t, width):
    """One frame as PIL image (accurate seek, rotation applied by ffmpeg)."""
    raw = run(['ffmpeg', '-loglevel', 'error', '-ss', f'{max(t, 0):.3f}', '-i', path, '-frames:v', '1',
               '-vf', f'scale={width}:-2', '-f', 'image2pipe', '-vcodec', 'png', '-']).stdout
    return Image.open(io.BytesIO(raw)).convert('RGB')


def grab_many(path, s, e, n, width):
    """n evenly spaced frames in [s, e) with one decode."""
    dur = max(e - s, 0.05)
    raw = run(['ffmpeg', '-loglevel', 'error', '-ss', f'{s:.3f}', '-t', f'{dur:.3f}', '-i', path,
               '-vf', f'fps={n / dur:.6f}:round=down,scale={width}:-2', '-frames:v', str(n),
               '-f', 'image2pipe', '-vcodec', 'ppm', '-']).stdout
    ims, i = [], 0
    while i < len(raw):
        hdr = raw[i:i + 64].split(maxsplit=4)
        w, h, mx = int(hdr[1]), int(hdr[2]), int(hdr[3])
        off = i + len(b' '.join(hdr[:4])) + 1
        ims.append(Image.frombytes('RGB', (w, h), raw[off:off + w * h * 3]))
        i = off + w * h * 3
    ts = [s + (k + 0.5) * dur / n for k in range(len(ims))]
    return list(zip(ts, ims))


def _label(img, lines, font):
    dr = ImageDraw.Draw(img)
    y = 2
    for ln in lines:
        if not ln:
            continue
        bb = dr.textbbox((0, 0), ln, font=font)
        dr.rectangle([0, y, bb[2] + 6, y + bb[3] + 4], fill=(0, 0, 0))
        dr.text((3, y + 1), ln, fill=(255, 230, 0), font=font)
        y += bb[3] + 5


def words_at(words, t, span=1.0):
    return ' '.join(w['w'] for w in words if w['s'] < t + span / 2 and w['e'] > t - span / 2)


def to_jpeg(img, q=82, max_side=2400):
    if max(img.size) > max_side:
        r = max_side / max(img.size)
        img = img.resize((int(img.width * r), int(img.height * r)), Image.LANCZOS)
    b = io.BytesIO()
    img.save(b, 'JPEG', quality=q)
    return b.getvalue()


def contact_sheet(idx, s=None, e=None, n=12, cols=None, width=None):
    """Grid of n frames across [s, e) with source TC, seconds, and what is being said at that moment."""
    info = idx['info']; clk = clock_of(info)
    s = 0 if s is None else max(0, s)
    e = info['duration'] if e is None else min(e, info['duration'])
    portrait = info['height'] > info['width']
    cols = cols or (6 if portrait else 4)
    width = width or (260 if portrait else 400)
    words = load_words(idx)
    frames = grab_many(info['path'], s, e, n, width)
    if not frames:
        raise RuntimeError('no frames decoded in range')
    tw, th = frames[0][1].size
    rows = math.ceil(len(frames) / cols)
    font = _font(13 if width < 300 else 15)
    sheet = Image.new('RGB', (cols * tw, rows * th), (20, 20, 20))
    for k, (t, im) in enumerate(frames):
        said = words_at(words, t)
        _label(im, [clk.stamp(t), (said[:38] + '…') if len(said) > 38 else said], font)
        sheet.paste(im, ((k % cols) * tw, (k // cols) * th))
    return sheet, [round(t, 2) for t, _ in frames]


def timeline_strip(idx, s, e, thumbs=8, width=1600):
    """For deciding exact in/out: thumbnails on top, waveform (dB) with silence shading, then each word placed
    on the time axis, with a second ruler. Everything shares one horizontal time scale."""
    info = idx['info']; clk = clock_of(info)
    s = max(0, s); e = min(e, info['duration'])
    span = e - s
    words = load_words(idx)
    tw = width // thumbs
    top = []
    if info['has_video']:
        top = grab_many(info['path'], s, e, thumbs, tw)
    th = top[0][1].height if top else 0
    WH, WD = 140, 70
    H = th + WH + WD + 28
    img = Image.new('RGB', (width, H), (16, 16, 16))
    dr = ImageDraw.Draw(img)
    font = _font(13)
    X = lambda t: int((t - s) / span * (width - 1))
    for k, (t, im) in enumerate(top):
        _label(im, [clk.stamp(t)], _font(11))
        img.paste(im, (k * tw, 0))
    # waveform
    p = os.path.join(idx['cache'], 'rms_db.npy')
    y0 = th
    if os.path.exists(p):
        db = np.load(p)
        thr = idx['audio']['silence_threshold_db']
        for x in range(width):
            t = s + x / width * span
            a, b = int(t / HOP), max(int(t / HOP) + 1, int((t + span / width) / HOP))
            v = float(db[a:b].max()) if a < len(db) else -90
            hgt = max(1, int((min(max(v, -60), 0) + 60) / 60 * (WH - 10)))
            col = (90, 200, 255) if v > thr else (70, 70, 90)
            dr.line([(x, y0 + WH // 2 - hgt // 2), (x, y0 + WH // 2 + hgt // 2)], fill=col)
    # cuts
    for c in idx.get('picture', {}).get('cuts_sec', []):
        if s <= c <= e:
            dr.line([(X(c), 0), (X(c), H)], fill=(255, 60, 60), width=2)
    # words
    yw = th + WH
    for k, w in enumerate(x for x in words if x['e'] > s and x['s'] < e):
        x1, x2 = X(max(w['s'], s)), X(min(w['e'], e))
        row = k % 2
        dr.rectangle([x1, yw + 4 + row * 32, max(x2, x1 + 2), yw + 30 + row * 32], outline=(255, 210, 0),
                     fill=(60, 50, 0) if w['p'] > 0.6 else (90, 20, 20))
        dr.text((x1 + 2, yw + 8 + row * 32), w['w'], fill=(255, 255, 255), font=font)
    # ruler
    yr = H - 24
    step = 0.1 if span <= 3 else 0.25 if span <= 8 else 0.5 if span <= 16 else 1.0
    t = math.ceil(s / step) * step
    while t <= e:
        major = abs(t / (step * 4) - round(t / (step * 4))) < 1e-6
        dr.line([(X(t), yr), (X(t), yr + (10 if major else 5))], fill=(200, 200, 200))
        if major:
            dr.text((X(t) + 2, yr + 8), f'{t:.2f}s', fill=(200, 200, 200), font=_font(11))
        t += step
    return img


def frames_image(idx, times, width=960):
    info = idx['info']; clk = clock_of(info)
    words = load_words(idx)
    ims = []
    for t in times[:6]:
        im = grab(info['path'], t, width)
        _label(im, [clk.stamp(t) + f'  frame {clk.frame(t, "floor")}', words_at(words, t, 0.6)], _font(18))
        ims.append(im)
    cols = 1 if len(ims) == 1 else 2
    w, h = ims[0].size
    out = Image.new('RGB', (w * cols, h * math.ceil(len(ims) / cols)), (0, 0, 0))
    for k, im in enumerate(ims):
        out.paste(im, ((k % cols) * w, (k // cols) * h))
    return out


# ───────────────────────────── log (annotations) ─────────────────────────────
def log_path(idx):
    return os.path.join(idx['cache'], 'log.json')


def read_log(idx):
    p = log_path(idx)
    return json.load(open(p)) if os.path.exists(p) else []


def annotate(idx, s, e, text, tags=(), rating=None):
    L = read_log(idx)
    L.append({'s': round(s, 2), 'e': round(e, 2), 'text': text, 'tags': list(tags), 'rating': rating,
              'tc_in': clock_of(idx['info']).tc(clock_of(idx['info']).frame(s, 'floor'))})
    L.sort(key=lambda x: x['s'])
    json.dump(L, open(log_path(idx), 'w'), ensure_ascii=False, indent=1)
    return L


# ───────────────────────────── export ─────────────────────────────
def _rate_xml(fps):
    f = Fraction(fps)
    return f'<rate><timebase>{round(float(f))}</timebase><ntsc>{"TRUE" if f.denominator == 1001 else "FALSE"}</ntsc></rate>'


def _esc(s):
    return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')


def resolve_plan(plan, lang='pt'):
    """Normalise plan clips: index each source, convert seconds→frames (snapping if asked)."""
    out = []
    for c in plan['clips']:
        idx = build_index(c['path'], lang)
        info = idx['info']; clk = clock_of(info)
        if 'in_frame' in c:
            fi, fo = int(c['in_frame']), int(c['out_frame'])
        elif c.get('snap'):
            r = snap(idx, float(c['in']), float(c['out']), mode=c['snap'], tighten=bool(c.get('tighten')))
            fi, fo = r['in_frame'], r['out_frame']
        else:
            fi, fo = clk.frame(float(c['in']), 'floor'), clk.frame(float(c['out']), 'ceil')
        out.append({**c, 'idx': idx, 'info': info, 'clk': clk, 'in_frame': fi, 'out_frame': fo})
    return out


def export_fcp7(plan, out_path, lang='pt'):
    """FCP7 XML (xmeml v4): imports in Premiere (File › Import) and Resolve (Import Timeline)."""
    clips = resolve_plan(plan, lang)
    seq_fps = Fraction(plan.get('fps') or clips[0]['info']['fps'])
    W = plan.get('width') or clips[0]['info']['width']; H = plan.get('height') or clips[0]['info']['height']
    rate = _rate_xml(seq_fps)
    files_done, fid = {}, {}
    v_items, a_items = [], {1: [], 2: []}
    rec = 0
    for n, c in enumerate(clips, 1):
        info = c['info']
        if Fraction(info['fps']) != seq_fps:
            raise ValueError(f"{info['name']} is {info['fps']} fps but the sequence is {seq_fps}; "
                             'split into separate timelines or pass an explicit fps')
        dur = c['out_frame'] - c['in_frame']
        key = info['path']
        if key not in fid:
            fid[key] = f'file-{len(fid) + 1}'
            ach = min(info['audio_channels'] or 0, 2)
            files_done[key] = (
                f'<file id="{fid[key]}"><name>{_esc(info["name"])}</name><pathurl>{_esc(file_url(key))}</pathurl>'
                f'{_rate_xml(info["fps"])}<duration>{info["frames"]}</duration>'
                f'<timecode>{_rate_xml(info["fps"])}<string>{c["clk"].tc(0)}</string><frame>{c["clk"].start}</frame>'
                f'<displayformat>{"DF" if c["clk"].drop else "NDF"}</displayformat></timecode>'
                f'<media><video><samplecharacteristics>{_rate_xml(info["fps"])}<width>{info["width"]}</width>'
                f'<height>{info["height"]}</height></samplecharacteristics></video>'
                + (f'<audio><samplecharacteristics><depth>16</depth><samplerate>{info["audio_rate"]}</samplerate>'
                   f'</samplecharacteristics><channelcount>{ach}</channelcount></audio>' if ach else '')
                + '</media></file>')
            ref = files_done[key]
        else:
            ref = f'<file id="{fid[key]}"/>'
        label = _esc(c.get('label') or info['name'])
        markers = ''.join(f'<marker><name>{_esc(l.get("text", "")[:60])}</name><comment>{_esc(", ".join(l.get("tags", [])))}</comment>'
                          f'<in>{c["clk"].frame(l["s"], "floor")}</in><out>-1</out></marker>'
                          for l in read_log(c['idx']) if c['in_frame'] <= c['clk'].frame(l['s']) < c['out_frame'])
        common = (f'<name>{label}</name><duration>{info["frames"]}</duration>{rate}'
                  f'<start>{rec}</start><end>{rec + dur}</end><in>{c["in_frame"]}</in><out>{c["out_frame"]}</out>')
        links = ''.join(f'<link><linkclipref>clip-{n}-{k}</linkclipref><mediatype>{"video" if k == "v" else "audio"}</mediatype>'
                        f'<trackindex>{1 if k in ("v", "a1") else 2}</trackindex><clipindex>{n}</clipindex></link>'
                        for k in (['v'] + (['a1', 'a2'][:min(info['audio_channels'], 2)] if info['audio_channels'] else [])))
        v_items.append(f'<clipitem id="clip-{n}-v">{common}{ref}{markers}{links}</clipitem>')
        for ch in range(1, min(info['audio_channels'] or 0, 2) + 1):
            a_items[ch].append(f'<clipitem id="clip-{n}-a{ch}">{common}<file id="{fid[key]}"/>'
                               f'<sourcetrack><mediatype>audio</mediatype><trackindex>{ch}</trackindex></sourcetrack>{links}</clipitem>')
        rec += dur
    name = _esc(plan.get('name', 'decupa'))
    audio_tracks = ''.join(f'<track>{"".join(a_items[ch])}</track>' for ch in (1, 2) if a_items[ch])
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4"><sequence id="seq-1">'
           f'<name>{name}</name><duration>{rec}</duration>{rate}'
           f'<timecode>{rate}<string>01:00:00:00</string><frame>{round(float(seq_fps)) * 3600}</frame><displayformat>NDF</displayformat></timecode>'
           f'<media><video><format><samplecharacteristics>{rate}<width>{W}</width><height>{H}</height>'
           f'<pixelaspectratio>square</pixelaspectratio></samplecharacteristics></format><track>{"".join(v_items)}</track></video>'
           f'<audio>{audio_tracks}</audio></media></sequence></xmeml>\n')
    open(out_path, 'w', encoding='utf-8').write(xml)
    # files must be emitted in full the first time they appear (done above) — keep order stable
    return {'path': out_path, 'nle_path': nle_path(out_path), 'clips': len(clips), 'duration_frames': rec,
            'duration_sec': round(rec / float(seq_fps), 2)}


def export_edl(plan, out_path, lang='pt'):
    clips = resolve_plan(plan, lang)
    seq_fps = Fraction(plan.get('fps') or clips[0]['info']['fps'])
    sclk = Clock(seq_fps, '01:00:00:00')
    lines = [f'TITLE: {plan.get("name", "decupa")}', 'FCM: ' + ('DROP FRAME' if sclk.drop else 'NON-DROP FRAME'), '']
    rec = 0
    for n, c in enumerate(clips, 1):
        dur = c['out_frame'] - c['in_frame']
        reel = re.sub(r'[^A-Za-z0-9]', '', os.path.splitext(c['info']['name'])[0])[:8] or 'AX'
        lines.append(f'{n:03d}  {reel:<8} AA/V  C        {c["clk"].tc(c["in_frame"])} {c["clk"].tc(c["out_frame"])} '
                     f'{sclk.tc(rec)} {sclk.tc(rec + dur)}')
        lines.append(f'* FROM CLIP NAME: {c["info"]["name"]}')
        if c.get('label'):
            lines.append(f'* COMMENT: {c["label"]}')
        lines.append('')
        rec += dur
    open(out_path, 'w').write('\n'.join(lines))
    return {'path': out_path, 'nle_path': nle_path(out_path), 'clips': len(clips)}


def plan_for_nle(plan, lang='pt'):
    """Per-clip numbers ready for the Resolve/Premiere MCP insert tools."""
    out, rec = [], 0
    for c in resolve_plan(plan, lang):
        dur = c['out_frame'] - c['in_frame']
        out.append({'label': c.get('label'), 'nle_path': c['info']['nle_path'], 'clip_name': c['info']['name'],
                    'fps': c['info']['fps'], 'in_frame': c['in_frame'], 'out_frame_exclusive': c['out_frame'],
                    'end_frame_inclusive': c['out_frame'] - 1,
                    'in_sec': round(c['clk'].sec(c['in_frame']), 4), 'out_sec': round(c['clk'].sec(c['out_frame']), 4),
                    'in_tc': c['clk'].tc(c['in_frame']), 'out_tc': c['clk'].tc(c['out_frame']),
                    'record_frame': rec, 'duration_frames': dur})
        rec += dur
    return out
