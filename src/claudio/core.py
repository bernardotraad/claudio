"""claudio.core — the "eyes and ears" for footage logging.

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

Indexes are cached in ~/.cache/claudio/<key>/ (key = path + size + mtime).
"""
import difflib, hashlib, io, json, math, os, re, subprocess, threading, time, unicodedata, urllib.parse
from fractions import Fraction

import numpy as np
from PIL import Image, ImageDraw, ImageFont

def env(name, default=None):
    """CLAUDIO_<name>, falling back to the pre-rename DECUPA_<name>."""
    return os.environ.get('CLAUDIO_' + name, os.environ.get('DECUPA_' + name, default))


def _migrate_cache():
    # the project was called decupa until v0.7: move its cache once, and leave a link so old absolute paths still resolve
    old, new = os.path.expanduser('~/.cache/decupa'), os.path.expanduser('~/.cache/claudio')
    if os.path.isdir(old) and not os.path.islink(old) and not os.path.exists(new):
        try:
            os.rename(old, new)
            os.symlink(new, old)
        except OSError:
            pass


if not env('CACHE'):
    _migrate_cache()
CACHE = os.path.expanduser(env('CACHE', '~/.cache/claudio'))
WHISPER_MODEL = env('WHISPER', 'large-v3-turbo')
INDEX_VERSION = 6
SR = 16000          # audio analysis rate
HOP = 0.02          # RMS hop (s)
QFPS = 4            # quality-analysis frame rate
PROXY_DIRS = {'proxies', 'proxy', 'proxys', 'cache', '_claudio', '_decupa', 'claudio_drift_corrected', 'decupa_drift_corrected',
              'pluraleyes_drift_corrected'}
DATA_DIR, OLD_DATA_DIR = '_claudio', '_decupa'      # per-folder data (contact sheets, progress); _decupa before v0.7


def data_dir(folder, create=True):
    """<folder>/_claudio. A pre-rename _decupa/ is renamed in place (no reindex) and the clip logs' links follow."""
    new, old = os.path.join(folder, DATA_DIR), os.path.join(folder, OLD_DATA_DIR)
    if os.path.isdir(old) and not os.path.exists(new):
        try:
            os.rename(old, new)
        except OSError:
            return old
        for f in os.listdir(folder):
            if f.lower().endswith('.md'):
                p = os.path.join(folder, f)
                try:
                    s = open(p, encoding='utf-8').read()
                    if '_decupa/' in s or 'decupa:' in s:
                        open(p, 'w', encoding='utf-8').write(
                            s.replace('(_decupa/', '(_claudio/').replace('<!-- decupa:', '<!-- claudio:'))
                except OSError:
                    pass
        old_md = os.path.join(folder, '_DECUPA.md')
        if os.path.exists(old_md):
            try:
                os.remove(old_md)               # fully regenerated as _CLAUDIO.md
            except OSError:
                pass
    if create:
        os.makedirs(new, exist_ok=True)
    return new


def is_proxy(path):
    """Premiere/Resolve proxies (Proxies/C0015_Proxy.mov, *_proxy.*) and our own folders are not camera media."""
    parts = [p.lower() for p in re.split(r'[\\/]', path)]
    return any(p in PROXY_DIRS for p in parts[:-1]) or re.search(r'_proxy\.[a-z0-9]+$', parts[-1]) is not None


def analysis_source(path):
    """What to decode for analysis: the clip's proxy when one exists AND has the same duration (an outdated proxy is
    ignored), else the original. Everything we write (.md, XML, timelines) always points at the original."""
    d = cache_dir(path)
    cp = os.path.join(d, 'proxy.json')
    if os.path.exists(cp):
        j = json.load(open(cp))
        if j.get('proxy') is None or os.path.exists(j['proxy']):
            return j.get('proxy') or path
    px = proxy_for(path)
    ok = None
    if px:
        try:
            a, b = probe(path), probe(px)
            if abs(a['duration'] - b['duration']) < 0.25 and b.get('audio_streams'):
                ok = px
        except Exception:
            ok = None
    json.dump({'proxy': ok}, open(cp, 'w'))
    return ok or path


def proxy_for(path):
    """An existing proxy of this clip (Premiere convention: <dir>/Proxies/<stem>_Proxy.*), or None."""
    d, f = os.path.split(path)
    stem = os.path.splitext(f)[0]
    for sub in ('Proxies', 'Proxy', 'proxies'):
        pd = os.path.join(d, sub)
        if os.path.isdir(pd):
            for x in os.listdir(pd):
                if os.path.splitext(x)[0].lower() in (stem.lower() + '_proxy', stem.lower()):
                    return os.path.join(pd, x)
    return None


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
    if p.startswith('file:'):
        p = urllib.parse.unquote(re.sub(r'^file:(//localhost(?=/)|//(?=/)|//(?=[A-Za-z]:)|//(?=wsl))?', '', p))
        m = re.match(r'^/*wsl(?:\.localhost|\$)/[^/]+(/.*)$', p, re.I)   # file://wsl.localhost/<distro>/… (a WSL file)
        if m:
            return m.group(1) if IS_WSL else '\\\\' + p.lstrip('/').replace('/', '\\')
        if re.match(r'^/?[A-Za-z]:[\\/]', p):                     # file://localhost/E:/… → E:/… (→ /mnt/e/… on WSL)
            return local_path(p.lstrip('/'))
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
    """subprocess.run that raises with ffmpeg's own error text (e.g. 'Permission denied') instead of an exit code."""
    r = subprocess.run(cmd, capture_output=True, **kw)
    if r.returncode:
        err = (r.stderr.decode('utf-8', 'replace') if isinstance(r.stderr, bytes) else str(r.stderr or '')).strip().splitlines()
        raise RuntimeError(f'{os.path.basename(cmd[0])} failed: ' + ' | '.join(err[-3:]))
    return r


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


_GPU_BAD = set()


def gpu_ffmpeg():
    """An ffmpeg with CUDA/NVDEC: $CLAUDIO_FFMPEG_GPU, else ~/.local/opt/ffmpeg-*/bin/ffmpeg. None if absent/disabled."""
    import glob
    if env('GPU', '1') == '0':
        return None
    cand = [env('FFMPEG_GPU')] + sorted(glob.glob(os.path.expanduser('~/.local/opt/ffmpeg-*/bin/ffmpeg')))
    return next((c for c in cand if c and os.path.exists(c)), None)


def nvdec_ok(path):
    """NVDEC (RTX 30xx) decodes H.264/HEVC 4:2:0 (8/10-bit), AV1, VP9 — not HEVC/H.264 4:2:2 (Canon R6 10-bit 4:2:2)."""
    try:
        j = json.loads(run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=codec_name,pix_fmt',
                            '-of', 'json', path]).stdout)['streams'][0]
    except Exception:
        return False
    codec, pf = j.get('codec_name'), j.get('pix_fmt') or ''
    if codec in _GPU_BAD or '422' in pf or '444' in pf and codec == 'h264':
        return False
    return codec in ('h264', 'hevc', 'av1', 'vp9') and gpu_ffmpeg() is not None


def heavy_codec(path):
    """4K or 10-bit / 4:2:2 long-GOP footage the CPU struggles to decode frame by frame."""
    try:
        j = json.loads(run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=codec_name,pix_fmt,width',
                            '-of', 'json', path]).stdout)['streams'][0]
    except Exception:
        return False
    pf = j.get('pix_fmt') or ''
    return j.get('codec_name') in ('hevc', 'h264') and ((j.get('width') or 0) >= 3000 or '10' in pf or '422' in pf)


THUMB_STEP, THUMB_W = 2.0, 256       # colour thumbnails kept from the same decode, for visual search (claudio.visual)


def thumb_size(info):
    return THUMB_W, max(2, int(round(THUMB_W * info['height'] / info['width'] / 2)) * 2)


def load_thumbs(d, info):
    """(times, images) saved by the picture analysis, or None for indexes made before v0.8."""
    p = os.path.join(d, 'thumbs.rgb')
    if not os.path.exists(p) or not info.get('width'):
        return None
    w, h = thumb_size(info)
    a = np.fromfile(p, np.uint8)
    n = len(a) // (w * h * 3)
    a = a[:n * w * h * 3].reshape(n, h, w, 3)
    return [k * THUMB_STEP for k in range(n)], [Image.fromarray(x) for x in a]


def _analyze_keyframes(path, d, info, qw, qh, progress):
    qf = 2
    qraw = os.path.join(d, 'q.gray')
    tw_, th_ = thumb_size(info)
    run(['ffmpeg', '-y', '-loglevel', 'error', '-skip_frame', 'nokey', *(['-hwaccel', 'videotoolbox'] if IS_MAC else []), '-i', path,
         '-filter_complex', f'[0:v:0]split=2[a][b];[a]fps={qf},scale={qw}:{qh}:flags=area,format=gray[q];'
         f'[b]fps=1/{THUMB_STEP}:round=near,scale={tw_}:{th_}:flags=area,format=rgb24[c]',
         '-map', '[q]', '-f', 'rawvideo', qraw, '-map', '[c]', '-f', 'rawvideo', os.path.join(d, 'thumbs.rgb')])
    progress('picture', 0.9)
    q = np.fromfile(qraw, np.uint8)
    q = q[:len(q) // (qw * qh) * qw * qh].reshape(-1, qh, qw).astype(np.float32)
    os.remove(qraw)
    rows, prev = [], None
    for i, f in enumerate(q):
        lap = (-4 * f[1:-1, 1:-1] + f[:-2, 1:-1] + f[2:, 1:-1] + f[1:-1, :-2] + f[1:-1, 2:])
        small = f[::2, ::2]
        dx, dy, conf = _phase_shift(prev, small) if prev is not None else (0, 0, 1.0)
        prev = small
        rows.append((i / qf, float(lap.var()), float(f.mean()), float((f > 250).mean()), float((f < 6).mean()), dx * 2, dy * 2, conf))
    m = np.array(rows, np.float32) if rows else np.zeros((0, 8), np.float32)
    np.save(os.path.join(d, 'q.npy'), m)
    progress('picture', 1)
    return {'cuts_frames': [], 'cuts_sec': [], 'samples': len(m), 'mode': 'keyframes',
            'luma_mean': round(float(m[:, 2].mean()), 1) if len(m) else 0}


def analyze_picture(path, d, info, progress=lambda *_: None):
    """One decode: tiny full-rate frames (cut detection) and 4 fps 320-px frames (quality + motion)."""
    W, H = info['width'], info['height']
    qw = 320; qh = max(2, int(round(qw * H / W / 2)) * 2)
    tw, th = 64, max(2, int(round(64 * H / W / 2)) * 2)
    qraw = os.path.join(d, 'q.gray')
    gpu = gpu_ffmpeg() if nvdec_ok(path) else None
    cw, ch = thumb_size(info)
    craw = os.path.join(d, 'thumbs.rgb')
    fast = not gpu and heavy_codec(path) and env('FAST', '1') != '0'
    if fast:     # keyframes only (~2/s): ~3× faster on 4K HEVC 4:2:2; in-file cuts are not detected (camera originals have none)
        return _analyze_keyframes(path, d, info, qw, qh, progress)
    if gpu:      # NVDEC decode + downscale on the GPU, only 320-px frames come back to the CPU (~2× faster on 4K)
        cmd = [gpu, '-y', '-loglevel', 'error', '-hwaccel', 'cuda', '-hwaccel_output_format', 'cuda', '-i', path,
               '-filter_complex', f'[0:v:0]scale_cuda={qw}:{qh},hwdownload,format=nv12,split=2[g][c0];[g]format=gray,split=2[a][b];'
               f'[a]scale={tw}:{th}:flags=area[t];[b]fps={QFPS}[q];[c0]fps=1/{THUMB_STEP}:round=near,scale={cw}:{ch}:flags=area,format=rgb24[c]',
               '-map', '[t]', '-f', 'rawvideo', 'pipe:1', '-map', '[q]', '-f', 'rawvideo', qraw, '-map', '[c]', '-f', 'rawvideo', craw]
    else:
        hw = ['-hwaccel', 'videotoolbox'] if IS_MAC else []          # Apple decoder; ffmpeg falls back to software
        cmd = ['ffmpeg', '-y', '-loglevel', 'error', '-threads', '0', '-skip_loop_filter', 'all', '-flags2', 'fast', *hw, '-i', path, '-filter_complex',
               f'[0:v:0]split=3[a][b][c0];[a]scale={tw}:{th}:flags=area,format=gray[t];'
               f'[b]fps={QFPS},scale={qw}:{qh}:flags=area,format=gray[q];[c0]fps=1/{THUMB_STEP}:round=near,scale={cw}:{ch}:flags=area,format=rgb24[c]',
               '-map', '[t]', '-f', 'rawvideo', 'pipe:1', '-map', '[q]', '-f', 'rawvideo', qraw, '-map', '[c]', '-f', 'rawvideo', craw]
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
    if gpu and (p.returncode != 0 or k < 0.9 * total):       # GPU path failed or stopped short: redo on the CPU
        _GPU_BAD.add(info.get('codec'))
        return analyze_picture(path, d, info, progress)
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


def motion_labels(m, width_px=320, rotate=0):
    """Per 4-fps sample: label camera behaviour from global shift (px/s at 320 px wide) and residual change.
    rotate: the clip's user rotation, so a pan of a camera shot on its side is still called a pan."""
    if not len(m):
        return []
    qf = 1.0 / float(np.median(np.diff(m[:, 0]))) if len(m) > 2 else QFPS
    vx = m[:, 5] * qf; vy = m[:, 6] * qf; conf = m[:, 7]
    if rotate == 90:
        vx, vy = -vy, vx
    elif rotate == 270:
        vx, vy = vy, -vx
    elif rotate == 180:
        vx, vy = -vx, -vy
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
    if env('BACKEND'):
        return env('BACKEND')
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


def apply_vocabulary(words, vocabulary, min_sim=0.72):
    """Replace misheard names with the right spelling: every window of transcript words whose letters are similar
    enough to a vocabulary term ("Enki Empreendimento" ≈ "INC Empreendimentos") takes the term, keeping timings."""
    if not vocabulary:
        return words
    terms = [t.strip() for t in re.split(r'[,;\n]', vocabulary) if t.strip()]
    for term in terms:
        tt = term.split(); target = norm(term.replace(' ', ''))
        def best_at(i):
            best = None
            for k in {max(1, len(tt) - 1), len(tt), len(tt) + 1}:
                if i + k > len(words):
                    continue
                cand = norm(''.join(w['w'] for w in words[i:i + k]))
                if not cand or abs(len(cand) - len(target)) > max(3, len(target) // 3):
                    continue
                sim = difflib.SequenceMatcher(None, cand, target).ratio()
                rank = sim + (0.06 if k == len(tt) else 0)      # same word count wins unless another is clearly better
                if sim >= min_sim and (best is None or rank > best[2]):
                    best = (sim, k, rank)
            return best

        i = 0
        while i < len(words):
            best = best_at(i)
            nxt = best_at(i + 1) if best else None
            if best and nxt and nxt[2] >= best[2]:      # the window starting one word later fits better
                i += 1
                continue
            if best and norm(''.join(w['w'] for w in words[i:i + best[1]])) != target:
                k = best[1]
                tail = re.search(r'[,.!?;:]+$', words[i + k - 1]['w'])
                seg = words[i:i + k]
                new = [{**seg[0], 'w': tt[0]}]
                for j, part in enumerate(tt[1:], 1):
                    src = seg[min(j, k - 1)]
                    new.append({**src, 'w': part})
                if tail:
                    new[-1]['w'] += tail.group(0)
                if len(new) > 1:   # spread the original span over the new words
                    s0, e0 = seg[0]['s'], seg[-1]['e']
                    step = (e0 - s0) / len(new)
                    for j, w in enumerate(new):
                        w['s'], w['e'] = round(s0 + j * step, 3), round(s0 + (j + 1) * step, 3)
                for w in new:
                    w['vocab'] = True
                if i > 0 and norm(words[i - 1]['w']) == norm(new[0]['w']):   # "INC" + corrected "INC Empreendimentos"
                    new[0]['s'] = words[i - 1]['s']
                    words[i - 1:i] = []
                    i -= 1
                words[i:i + k] = new
                i += len(new)
            else:
                i += 1
    return words


def dedupe_vocab(words):
    """Drop a word repeated right next to its vocabulary-corrected copy ("INC" "INC Empreendimentos")."""
    out = []
    for w in words:
        if out and norm(out[-1]['w']) == norm(w['w']) and (out[-1].get('vocab') or w.get('vocab')):
            out[-1] = {**w, 's': out[-1]['s'], 'vocab': True}
            continue
        out.append(w)
    return out


def _resegment(segments, words):
    """Rebuild segment texts from (corrected) words."""
    return [{**s, 'text': ' '.join(w['w'] for w in words if s['s'] - 0.01 <= w['s'] < s['e'] + 0.01) or s['text']} for s in segments]


def fix_word_times(words):
    """Whisper stretches the first word after a pause back across the silence ("família" 53.6→63.3 s when it is said
    at ~63 s). A word far longer than its letters allow is moved to the END of its span, where it was spoken."""
    for w in words:
        est = 0.075 * len(w['w']) + 0.35
        if w['e'] - w['s'] > max(1.2, 2.5 * est):
            w['s'] = round(w['e'] - est, 3)
            w['stretched'] = True
    return words


def transcribe(d, lang, progress=lambda *_: None, duration=1, vocabulary=None):
    audio = load_pcm(d)
    prompt = PROMPT_PT if (lang or '').startswith('pt') else None
    # vocabulary is NOT given to Whisper (in the prompt it gets hallucinated into the text; as hotwords it drops
    # words): we transcribe faithfully and correct names afterwards (apply_vocabulary)
    words, out = [], []
    if whisper_backend() == 'mlx':
        import mlx_whisper
        r = mlx_whisper.transcribe(audio, path_or_hf_repo=env('MLX_MODEL', 'mlx-community/whisper-large-v3-turbo'),
                                   language=lang or None, word_timestamps=True, condition_on_previous_text=False,
                                   initial_prompt=prompt, verbose=None)
        for s in r['segments']:
            words.extend({'w': w['word'].strip(), 's': round(float(w['start']), 3), 'e': round(float(w['end']), 3),
                          'p': round(float(w.get('probability', 1)), 3)} for w in s.get('words', []) if w['word'].strip())
            out.append({'s': round(float(s['start']), 2), 'e': round(float(s['end']), 2), 'text': s['text'].strip()})
        progress('speech', 1)
        words = apply_vocabulary(fix_word_times(words), vocabulary)
        return {'language': r.get('language', lang), 'segments': _resegment(out, words) if vocabulary else out, 'words': words}
    segs, info = whisper().transcribe(
        audio, language=lang or None, word_timestamps=True, vad_filter=True,
        vad_parameters={'min_silence_duration_ms': 300},
        condition_on_previous_text=False,   # keep repeated takes instead of "deduplicating" them
        initial_prompt=prompt)
    for s in segs:
        ws = [{'w': w.word.strip(), 's': round(w.start, 3), 'e': round(w.end, 3), 'p': round(w.probability, 3)}
              for w in (s.words or []) if w.word.strip()]
        words.extend(ws)
        out.append({'s': round(float(s.start), 2), 'e': round(float(s.end), 2), 'text': clean_text(s.text)})
        progress('speech', s.end / max(duration, 1))
    words = apply_vocabulary(fix_word_times(words), vocabulary)
    return {'language': info.language, 'segments': _resegment(out, words) if vocabulary else out, 'words': words}


def clean_text(t):
    """Typography of transcribed text: "599 ,00" → "599,00", "R $ 150" → "R$ 150", no space before punctuation."""
    t = re.sub(r'\s+([,.!?;:])', r'\1', t)
    t = re.sub(r'(?i)\bR\s*\$\s*', 'R$ ', t)
    t = re.sub(r'(\d)\s*,\s*(\d)', r'\1,\2', t)
    return re.sub(r'\s{2,}', ' ', t).strip()


def norm(w):
    return re.sub(r'[^a-z0-9%]', '', unicodedata.normalize('NFKD', w.lower()).encode('ascii', 'ignore').decode())


FILLERS = {'ha', 'han', 'hm', 'hmm', 'eh', 'ehh', 'ahn', 'uh', 'uhm', 'um', 'hum'}   # not 'é' (verb) / 'né' / 'tipo' (often meaningful)


CREW = {'acao', 'action', 'tres', 'dois', 'um', 'uma', '1', '2', '3', 'camera', 'rodando', 'gravando', 'corta', 'vamos', 'la',
        'take', 'atencao', 'one', 'two', 'three', 'cena', 'sena', 'pode', 'ir', 'podemos',
        'beleza', 'ta', 'foi', 'som', 'ok', 'show', 'boa', 'pera', 'perai', 'espera', 'isso', 'valendo', 'gravou', 'rolando'}
CRITIQUE = ('gaguejei', 'engoli', 'buguei', 'errei', 'desculpa', 'denovo', 'maisum', 'maisuma', 'perdi', 'naoficou',
            'ficouruim', 'travei', 'esqueci', 'perai', 'peraai', 'troquei', 'falelerrado', 'stumbled', 'sorry', 'again',
            'onemore', 'mybad', 'messedup', 'perdao', 'ficouestranho', 'ficoumal', 'naoficoubom', 'repete', 'vourepetir',
            'deixaeurepetir', 'faltou', 'saiuerrado', 'falei errado'.replace(' ', ''))


def _is_critique(ws):
    flat = ''.join(norm(w['w']) for w in ws)
    return any(c in flat for c in CRITIQUE)


def utterances(words, gap=0.7):
    """Split the word stream at pauses >= gap, strip a leading countdown/slate ("camera rodando, 3, 2, 1, acao"),
    split off a trailing self-critique ("...do aluguel. Mas eu gaguejei") and mark chatter (< 4 content words)."""
    raw, cur = [], []
    for w in words:
        if cur and w['s'] - cur[-1]['e'] >= gap:
            raw.append(cur); cur = []
        cur.append(w)
    if cur:
        raw.append(cur)
    split = []
    for u in raw:
        cur = []
        for w in u:
            cur.append(w)
            if norm(w['w']) in ('acao', 'action') and len(cur) > 1:
                split.append(cur); cur = []
        if cur:
            split.append(cur)
    raw = split
    out = []
    for u in raw:
        k = 0
        while k < len(u) - 1 and norm(u[k]['w']) in CREW:
            k += 1
        u = u[k:] if sum(norm(x['w']) not in CREW for x in u[k:]) >= 3 else u
        parts, flagged = [u], False
        for j in range(4, len(u)):                    # line followed by the speaker's critique -> split it off
            before = sum(norm(x['w']) not in CREW for x in u[:j])
            if _is_critique(u[j:j + 3]) and (re.search(r'[.?!,]$', u[j - 1]['w']) or before >= 5):
                parts, flagged = [u[:j], u[j:]], True
                break
        for pi, p in enumerate(parts):
            content = [x for x in p if norm(x['w']) not in CREW]
            out.append({'s': p[0]['s'], 'e': p[-1]['e'], 'text': ' '.join(x['w'] for x in p), 'n': len(p),
                        'words': p, 'conf': round(float(np.mean([x['p'] for x in p])), 3),
                        'fillers': sum(norm(x['w']) in FILLERS for x in p),
                        'chatter': len(content) < 4 or (pi == 1 and flagged) or (len(parts) == 1 and _is_critique(p) and len(content) < 8),
                        'self_critique_after': flagged and pi == 0})
    # a critique uttered right after a take (next utterance, within 3 s) flags that take
    for i in range(len(out) - 1):
        if not out[i]['chatter'] and out[i + 1]['s'] - out[i]['e'] < 3.0 and _is_critique(out[i + 1]['words']):
            out[i]['self_critique_after'] = True
    return out


def _toks(u):
    return [norm(w['w']) for w in u['words'] if norm(w['w']) and norm(w['w']) not in CREW]


def group_takes(utts, sim=0.55, window=240.0):
    """Cluster repeated attempts of the same line (false starts join as a prefix). Chatter is ignored. Attempts of one
    line can be minutes apart with chatter between them, so grouping is by time window, not by position."""
    groups = []
    for i, u in enumerate(utts):
        if u['chatter']:
            continue
        t = _toks(u)
        best, bs = None, 0
        for g in groups:
            if u['s'] - utts[g[-1]]['e'] > window:
                continue
            for j in g[-3:]:
                ref = _toks(utts[j])
                r = difflib.SequenceMatcher(None, ref, t, autojunk=False).ratio()
                short, long_ = (t, ref) if len(t) < len(ref) else (ref, t)
                pref = difflib.SequenceMatcher(None, short, long_[:len(short)], autojunk=False).ratio() if len(short) >= 3 else 0
                sc = max(r, pref * 0.95)
                if sc > bs:
                    best, bs = g, sc
        if best is not None and bs >= sim:
            best.append(i)
        else:
            groups.append([i])
    return groups


# ───────────────────────────── index (orchestration) ─────────────────────────────
def migrate(idx, d):
    """Bring an older index up to date WITHOUT re-analysing the footage: only the parts whose logic changed."""
    v = idx.get('version', 0)
    if v < 5 and not os.path.exists(os.path.join(d, 'a16.pcm')) and os.path.exists(os.path.join(d, 'a16.wav')):
        import wave
        w = wave.open(os.path.join(d, 'a16.wav'))
        open(os.path.join(d, 'a16.pcm'), 'wb').write(w.readframes(w.getnframes()))
    if v < 6:                                       # word-timing fix + transcript typography
        wp = os.path.join(d, 'words.json')
        if os.path.exists(wp):
            json.dump(fix_word_times(json.load(open(wp))), open(wp, 'w'), ensure_ascii=False)
        if idx.get('speech'):
            for s in idx['speech']['segments']:
                s['text'] = clean_text(s['text'])
    idx['version'] = INDEX_VERSION
    idx['migrated_from'] = v
    json.dump(idx, open(os.path.join(d, 'index.json'), 'w'), ensure_ascii=False, indent=1)
    return idx


def build_index(path, lang='pt', progress=lambda *_: None, force=False, vocabulary=None):
    """vocabulary: names Whisper must get right ("INC Empreendimentos, Juiz de Fora"). A new vocabulary on an existing
    index re-runs only the transcript."""
    path = local_path(path)
    d = cache_dir(path)
    ip = os.path.join(d, 'index.json')
    if os.path.exists(ip) and not force:
        idx = json.load(open(ip))
        idx['cache'] = d
        if idx.get('version', 0) < INDEX_VERSION and idx.get('version', 0) >= 3:
            idx = migrate(idx, d)
        if idx.get('version') == INDEX_VERSION:
            if vocabulary and vocabulary != idx.get('vocabulary') and idx.get('speech'):
                # vocabulary is a post-correction: re-apply it to the stored words, no re-transcription
                wp, raw = os.path.join(d, 'words.json'), os.path.join(d, 'words_raw.json')
                base = json.load(open(raw)) if os.path.exists(raw) else json.load(open(wp))
                words = dedupe_vocab(apply_vocabulary([dict(w) for w in base], vocabulary))
                json.dump(words, open(wp, 'w'), ensure_ascii=False)
                idx['speech']['segments'] = [{**s, 'text': clean_text(s['text'])} for s in _resegment(idx['speech']['segments'], words)]
                idx['vocabulary'] = vocabulary
                json.dump(idx, open(ip, 'w'), ensure_ascii=False, indent=1)
            return idx
    t0 = time.time()
    info = probe(path)
    src = analysis_source(path)
    sinfo = info if src == path else {**probe(src), 'frames': info.get('frames'), 'fps': info.get('fps')}
    progress('probe', 1)
    idx = {'version': INDEX_VERSION, 'info': info, 'cache': d}
    if src != path:
        idx['analysed_from'] = nle_path(src)
    idx['audio'] = analyze_audio(src, d, sinfo)
    progress('audio', 1)
    if info['has_video']:
        idx['picture'] = analyze_picture(src, d, sinfo, progress)
    sp = None
    if idx['audio'] and idx['audio']['speech_ratio'] > 0.02:
        sp = transcribe(d, lang, progress, info['duration'], None)
        json.dump(sp['words'], open(os.path.join(d, 'words_raw.json'), 'w'), ensure_ascii=False)
        if vocabulary:
            sp['words'] = dedupe_vocab(apply_vocabulary([dict(w) for w in sp['words']], vocabulary))
            sp['segments'] = [{**s, 'text': clean_text(s['text'])} for s in _resegment(sp['segments'], sp['words'])]
        json.dump(sp['words'], open(os.path.join(d, 'words.json'), 'w'), ensure_ascii=False)
        idx['vocabulary'] = vocabulary
    idx['speech'] = {'language': sp['language'], 'segments': sp['segments'], 'n_words': len(sp['words'])} if sp else None
    idx['seconds_to_index'] = round(time.time() - t0, 1)
    json.dump(idx, open(ip, 'w'), ensure_ascii=False, indent=1)
    return idx


def light_index(path, lang='pt'):
    """The full index if it exists, else just probe + cache dir (enough to cut/render/export a clip without
    paying for the full analysis — e.g. 20-min multicam angles)."""
    path = local_path(path)
    d = cache_dir(path)
    ip = os.path.join(d, 'index.json')
    if os.path.exists(ip):
        idx = json.load(open(ip))
        idx['cache'] = d
        if idx.get('version') == INDEX_VERSION:
            return idx
    lp = os.path.join(d, 'probe.json')
    if not os.path.exists(lp):
        json.dump(probe(path), open(lp, 'w'))
    return {'version': INDEX_VERSION, 'info': json.load(open(lp)), 'cache': d, 'light': True, 'picture': {}, 'audio': None}


def words_only(path, lang='pt'):
    """Word timestamps without the full analysis (for sync fallbacks): reuse the index if there is one, else
    transcribe just the audio (from the proxy when available) and cache it."""
    p = local_path(path)
    d = cache_dir(p)
    if os.path.exists(os.path.join(d, 'index.json')):
        return load_words(build_index(p, lang))
    wq = os.path.join(d, 'words_quick.json')
    if os.path.exists(wq):
        return json.load(open(wq))
    if not os.path.exists(os.path.join(d, 'a16.pcm')):
        run(['ffmpeg', '-v', 'error', '-y', '-i', analysis_source(p), '-vn', '-map', '0:a:0', '-ac', '1', '-ar', str(SR),
             '-f', 's16le', os.path.join(d, 'a16.pcm')])
    w = transcribe(d, lang)['words']
    json.dump(w, open(wq, 'w'), ensure_ascii=False)
    return w


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
    labs = motion_labels(m, rotate=get_rotation(idx['info']['path']))
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
    """Repeated attempts of each line, ranked. Completeness is measured against the CONSENSUS line (medoid of the
    attempts); a take followed by the speaker's own critique ("gaguejei", "desculpa", "mais um") is penalised;
    countdowns and chatter are ignored; each take is trimmed to the line (no trailing comments)."""
    words = performance_words(idx) if slate(idx)['has_slate'] else load_words(idx)
    if not words:
        return {'groups': [], 'note': 'no speech found in this clip'}
    U = utterances(words, gap)
    G = group_takes(U)
    sm = lambda a, b: difflib.SequenceMatcher(None, a, b, autojunk=False)
    groups = []
    for gi, g in enumerate(G):
        toks = [_toks(U[i]) for i in g]
        # the line as meant: the attempt that shares the most words with the others (a false start is a prefix of the
        # full line, so the full line wins; by ratio the short one could, and the complete take then looked "too long")
        cons = max(toks, key=lambda a: sum(sum(bl.size for bl in sm(a, b).get_matching_blocks()) for b in toks)) if len(toks) > 1 else toks[0]
        cands = []
        for k, i in enumerate(g):
            u = U[i]
            t = toks[k]
            matched = sum(bl.size for bl in sm(cons, t).get_matching_blocks())
            coverage = matched / max(len(cons), 1)
            extra = max(0, len(t) - matched)
            q = range_quality(idx, u['s'], u['e'])
            score = (3.0 * coverage + 1.2 * u['conf'] - 0.4 * u['fillers'] - 0.15 * extra
                     - 2.5 * u['self_critique_after'] + 0.6 * min(q.get('sharpness', 1), 1)
                     - 0.5 * (q.get('motion') in ('shaky', 'chaotic')) - 0.02 * q.get('clipped_highlights_pct', 0)
                     + (0.6 * k / max(len(g) - 1, 1) if prefer == 'last' else 0))
            cands.append({'take': k + 1, 's': u['s'], 'e': u['e'], 'text': u['text'], 'words': u['n'],
                          'coverage': round(coverage, 2), 'complete': coverage >= 0.9, 'extra_words': extra,
                          'asr_conf': u['conf'], 'fillers': u['fillers'], 'self_critique_after': u['self_critique_after'],
                          'picture': q, 'score': round(score, 2)})
        best = max(cands, key=lambda c: c['score'])
        if prefer == 'last':        # the editor's rule: the LAST complete attempt the speaker didn't criticise
            ok = [c for c in cands if c['coverage'] >= 0.7 and not c['self_critique_after']]   # rephrased last take still counts
            if ok:
                best = ok[-1]
        groups.append({'group': gi + 1, 'attempts': len(g), 'line': ' '.join(cons), 'best_take': best['take'],
                       'best': {'s': best['s'], 'e': best['e']}, 'candidates': cands})
    groups.sort(key=lambda g: g['candidates'][0]['s'])
    return {'groups': groups, 'utterances': len(U), 'chatter_ignored': sum(u['chatter'] for u in U),
            'how_to_verify': 'timeline_strip the best take (and a close runner-up); a take the speaker criticised is out'}


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
    labs = motion_labels(m, rotate=get_rotation(idx['info']['path']))
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
            for w in words:                       # in-point inside a word → before it (≤ 0.6 s: more = bad timestamp)
                if w['s'] - pad_in < s < w['e'] and s - (w['s'] - pad_in) <= 0.6:
                    s = max(0, w['s'] - pad_in); break
            for w in reversed(words):             # out-point inside a word → after it
                if w['s'] < e < w['e'] + pad_out and (w['e'] + pad_out) - e <= 0.6:
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


ROT_VF = {90: 'transpose=1', 270: 'transpose=2', 180: 'hflip,vflip'}


def get_rotation(path):
    """Extra rotation set by the user/Claude for clips shot sideways without metadata (0/90/180/270, clockwise)."""
    p = os.path.join(cache_dir(local_path(path)), 'orientation.json')
    return json.load(open(p)).get('rotate', 0) if os.path.exists(p) else 0


def set_rotation(path, degrees):
    degrees = int(degrees) % 360
    assert degrees in (0, 90, 180, 270), 'rotation must be 0, 90, 180 or 270 (clockwise)'
    json.dump({'rotate': degrees}, open(os.path.join(cache_dir(local_path(path)), 'orientation.json'), 'w'))
    return degrees


def rot_vf(path):
    r = ROT_VF.get(get_rotation(path))
    return r + ',' if r else ''


def oriented(info):
    """(width, height) as the picture is meant to be seen (after the user rotation)."""
    w, h = info['width'], info['height']
    return (h, w) if get_rotation(info['path']) in (90, 270) else (w, h)


def _fast(path, width):
    """Proxy for small frames (≤ proxy width), original otherwise. Rotation always comes from the original."""
    try:
        src = analysis_source(path)
    except Exception:
        return path
    if src != path:
        pw = json.load(open(os.path.join(cache_dir(path), 'proxy.json'))).get('width')
        if pw is None:
            pw = probe(src).get('width') or 0
            json.dump({'proxy': src, 'width': pw}, open(os.path.join(cache_dir(path), 'proxy.json'), 'w'))
        if width <= pw * 1.05:
            return src
    return path


# containers that carry their stream layout in the header: ffmpeg needs no probing (by default it decodes several
# frames to "learn" the stream — 3–9 s per call on 4K HEVC 4:2:2 over /mnt; with this, ~1.4 s)
NO_PROBE = ['-probesize', '256000', '-analyzeduration', '0']


def _probe_opts(src):
    return NO_PROBE if os.path.splitext(src)[1].lower() in ('.mp4', '.mov', '.m4v', '.mxf') else []


def grab(path, t, width, keyframe=False):
    """One frame as PIL image (accurate seek, metadata + user rotation applied; proxy used when it is big enough).
    keyframe=True takes the keyframe at/after t instead (much cheaper on long-GOP 4K; ±0.5 s on camera originals)."""
    src = _fast(path, width)
    raw = run(['ffmpeg', '-loglevel', 'error', *_probe_opts(src), *(['-skip_frame', 'nokey'] if keyframe else []),
               '-ss', f'{max(t, 0):.3f}', '-i', src, '-frames:v', '1',
               '-vf', f'{rot_vf(path)}scale={width}:-2', '-f', 'image2pipe', '-vcodec', 'png', '-']).stdout
    return Image.open(io.BytesIO(raw)).convert('RGB')


def grab_many(path, s, e, n, width):
    """n evenly spaced frames in [s, e): one decode when they are close together, one seek per frame when they are
    far apart (decoding a whole 20-min 4K range to keep 8 frames takes minutes)."""
    dur = max(e - s, 0.05)
    if dur / max(n, 1) > 3.0:
        ts = [s + (k + 0.5) * dur / n for k in range(n)]
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(4) as ex:
            ims = list(ex.map(lambda t: grab(path, t, width), ts))
        return list(zip(ts, ims))
    src = _fast(path, width)
    raw = run(['ffmpeg', '-loglevel', 'error', *_probe_opts(src), '-ss', f'{s:.3f}', '-t', f'{dur:.3f}', '-i', src,
               '-vf', f'{rot_vf(path)}fps={n / dur:.6f}:round=down,scale={width}:-2', '-frames:v', str(n),
               '-pix_fmt', 'rgb24', '-f', 'image2pipe', '-vcodec', 'ppm', '-']).stdout
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
    portrait = oriented(info)[1] > oriented(info)[0]
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


# ───────────────────────────── camera + colour space ─────────────────────────────
_CAM_PATTERNS = [
    (rb'<Device manufacturer="([^"]+)" modelName="([^"]+)"', lambda m: f'{m.group(1).decode()} {m.group(2).decode()}'),
    (rb'(Canon EOS [A-Z0-9][A-Z0-9 ]{0,12})', lambda m: m.group(1).decode().strip()),
    (rb'(Canon EOS C[0-9]{2,3})', lambda m: m.group(1).decode()),
    (rb'(Blackmagic [A-Za-z0-9 ]{3,30}Camera[A-Za-z0-9 ]{0,12})', lambda m: m.group(1).decode().strip()),
    (rb'(DJI [A-Za-z0-9 ]{2,20})', lambda m: m.group(1).decode().strip()),
    (rb'(GoPro ?[A-Za-z0-9 ]{0,12})', lambda m: m.group(1).decode().strip()),
    (rb'(Panasonic|DC-[A-Z]{1,3}[0-9]{1,4}[A-Z]?)', lambda m: m.group(1).decode()),
    (rb'(NIKON Z ?[0-9a-z]{1,4}|NIKON [A-Z0-9]{2,6})', lambda m: m.group(1).decode()),
    (rb'(FUJIFILM [A-Z0-9-]{2,10})', lambda m: m.group(1).decode()),
]
LOG_BY_BRAND = {'sony': 'S-Log3 (ou S-Log2)', 'canon': 'C-Log3 (ou C-Log)', 'blackmagic': 'Blackmagic Film / Gen 5',
                'panasonic': 'V-Log', 'nikon': 'N-Log', 'fujifilm': 'F-Log', 'dji': 'D-Log', 'apple': 'Apple Log',
                'gopro': 'Protune flat'}


def camera_of(path, info=None):
    """Camera make/model from container tags or the maker metadata embedded in the file (first/last 4 MB)."""
    tags = {}
    try:
        j = json.loads(run(['ffprobe', '-v', 'error', '-show_entries', 'format_tags:stream_tags', '-of', 'json', path]).stdout)
        tags = {k.lower(): v for t in [j.get('format', {}).get('tags', {})] + [s.get('tags', {}) for s in j.get('streams', [])] for k, v in t.items()}
    except Exception:
        pass
    for k in ('com.apple.quicktime.model', 'model', 'com.android.model'):
        if tags.get(k, '').strip():
            mk = tags.get('com.apple.quicktime.make') or tags.get('make') or ''
            return f'{mk} {tags[k]}'.strip()
    sz = os.path.getsize(path)
    with open(path, 'rb') as fh:
        b = fh.read(4_000_000)
        if sz > 8_000_000:
            fh.seek(sz - 4_000_000); b += fh.read()
    for pat, fmt in _CAM_PATTERNS:
        m = re.search(pat, b)
        if m:
            return fmt(m)
    return None


def color_profile(idx):
    """What the picture is encoded in, so a flat image is not mistaken for a defect. Metadata first (transfer tags,
    Sony CaptureGammaEquation), then the image: log has a hard raised black floor, highlights below clip and low
    saturation; Rec.709 reaches true black and saturates. The real correction (LUT/grade) belongs to the edit."""
    cp = os.path.join(idx['cache'], 'color.json')
    if os.path.exists(cp):
        return json.load(open(cp))
    info = idx['info']; path = info['path']
    cam = camera_of(path, info)
    j = json.loads(run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                        'stream=color_transfer,color_primaries,color_space,pix_fmt', '-of', 'json', path]).stdout)['streams'][0]
    out = {'camera': cam, 'pix_fmt': j.get('pix_fmt'), 'transfer_tag': j.get('color_transfer'), 'primaries_tag': j.get('color_primaries')}
    with open(path, 'rb') as fh:
        head = fh.read(4_000_000)
    g = re.search(rb'CaptureGammaEquation value="([^"]+)"', head)
    if g:
        out.update(profile=g.group(1).decode(), source='metadata', confidence='alta')
    elif j.get('color_transfer') in ('arib-std-b67',):
        out.update(profile='HLG (HDR)', source='metadata', confidence='alta')
    elif j.get('color_transfer') in ('smpte2084',):
        out.update(profile='PQ / HDR10', source='metadata', confidence='alta')
    if info.get('has_video'):
        d = info['duration']
        fr = grab_many(path, d * 0.1, d * 0.9, 6, 320)
        a = np.concatenate([np.asarray(im, np.float32).reshape(-1, 3) / 255 for _, im in fr])
        Y = a @ np.array([0.2126, 0.7152, 0.0722], np.float32)
        mx, mn = a.max(1), a.min(1)
        S = np.where(mx > 0.05, (mx - mn) / np.maximum(mx, 1e-6), 0)
        st = {'black_floor': round(float(np.percentile(Y, 0.5)), 3), 'p2': round(float(np.percentile(Y, 2)), 3),
              'white_p99_5': round(float(np.percentile(Y, 99.5)), 3), 'saturation_p95': round(float(np.percentile(S, 95)), 3)}
        out['stats'] = st
        if 'profile' not in out and st['white_p99_5'] - st['black_floor'] < 0.12:
            out.update(profile='indefinido (imagem quase uniforme)', log=False, source='imagem', confidence='baixa')
        if 'profile' not in out:
            hard = st['p2'] - st['black_floor'] < 0.02
            brand = next((k for k in LOG_BY_BRAND if cam and k in cam.lower()), None)
            flavour = LOG_BY_BRAND.get(brand, 'log')
            if st['black_floor'] >= 0.075 and hard and st['saturation_p95'] < 0.5:
                conf = 'alta' if st['black_floor'] >= 0.09 and st['saturation_p95'] < 0.42 else 'média'
                out.update(profile=f'provável {flavour}', log=True, source='imagem', confidence=conf)
            elif st['black_floor'] >= 0.06 and st['saturation_p95'] < 0.45:
                out.update(profile='possível log (ou imagem lavada: névoa, contraluz)', log=True, source='imagem', confidence='baixa')
            else:
                out.update(profile='Rec.709 / perfil padrão', log=False, source='imagem', confidence='média')
    out.setdefault('log', 'log' in (out.get('profile') or '').lower())
    out['note'] = ('imagem lavada/sem contraste é o esperado em log — não é defeito; a correção (LUT + cor) vem na edição'
                   if out['log'] else 'imagem já em espaço de exibição (Rec.709)')
    json.dump(out, open(cp, 'w'), ensure_ascii=False)
    return out


# ───────────────────────────── log (annotations) ─────────────────────────────
def log_path(idx):
    return os.path.join(idx['cache'], 'log.json')


def read_log(idx):
    p = log_path(idx)
    return json.load(open(p)) if os.path.exists(p) else []


def annotate(idx, s, e, text, tags=(), rating=None, replace=False):
    """Add one line to the clip's log. replace=True first removes every line that overlaps [s, e] (to rewrite a
    description instead of stacking a second one)."""
    L = read_log(idx)
    if replace:
        L = [l for l in L if l['e'] <= s or l['s'] >= e]
    L.append({'s': round(s, 2), 'e': round(e, 2), 'text': text, 'tags': list(tags), 'rating': rating,
              'tc_in': clock_of(idx['info']).tc(clock_of(idx['info']).frame(s, 'floor'))})
    L.sort(key=lambda x: x['s'])
    json.dump(L, open(log_path(idx), 'w'), ensure_ascii=False, indent=1)
    return L


def relabel_log(idx, mapping):
    """Replace phrases in the clip's log (texts and tags), case-insensitive: {"violonista de jaqueta vermelha": "Rayo"}.
    Returns how many lines changed."""
    L = read_log(idx)
    n = 0
    for l in L:
        old = (l['text'], list(l.get('tags') or []))
        for a, b in sorted(mapping.items(), key=lambda kv: -len(kv[0])):      # longest phrase first
            pat = re.compile(re.escape(a), re.I)
            l['text'] = pat.sub(b, l['text'])
            l['tags'] = [pat.sub(b, t) for t in l.get('tags') or []]
        if (l['text'], l['tags']) != old:
            n += 1
    if n:
        json.dump(L, open(log_path(idx), 'w'), ensure_ascii=False, indent=1)
    return n


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
    name = _esc(plan.get('name', 'Cláudio'))
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
    lines = [f'TITLE: {plan.get("name", "Claudio")}', 'FCM: ' + ('DROP FRAME' if sclk.drop else 'NON-DROP FRAME'), '']
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
