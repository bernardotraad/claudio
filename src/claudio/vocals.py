"""claudio.vocals — the lead vocal of a song, isolated (UVR MDX-Net, ONNX, no torch).

Whisper hears the lyrics over the band but places the first word of each sung line back across the instrumental
before it (the caption showed up ~1 s before the voice). With the vocal alone, where singing starts and stops is
just its loudness. The model (UVR-MDX-NET-Voc_FT, 67 MB) is downloaded once to ~/.cache/claudio/models; the stem is
cached next to the clip's index (vocals.f32, mono 16 kHz) for the timing pass.
"""
import os, subprocess, urllib.request

import numpy as np

from claudio import core

MODEL = 'UVR-MDX-NET-Voc_FT.onnx'
URL = 'https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models/' + MODEL
SR, N_FFT, HOP, DIM_F, DIM_T, COMPENSATE = 44100, 7680, 1024, 3072, 256, 1.021
ENV_SR = 100                                   # envelope rate: 10 ms


def model_path():
    d = os.path.join(os.path.expanduser('~/.cache/claudio'), 'models')
    p = os.path.join(d, MODEL)
    if not os.path.exists(p):
        os.makedirs(d, exist_ok=True)
        urllib.request.urlretrieve(URL, p + '.part')
        os.replace(p + '.part', p)
    return p


_sess = None


def session():
    global _sess
    if _sess is None:
        import onnxruntime as ort
        prov = [p for p in ('CUDAExecutionProvider', 'CPUExecutionProvider') if p in ort.get_available_providers()]
        _sess = ort.InferenceSession(model_path(), providers=prov)
    return _sess


_WIN = np.hanning(N_FFT + 1)[:-1].astype(np.float32)    # periodic Hann, as torch.hann_window


def _stft(x):
    """[2, n] → [4, DIM_F, DIM_T] (L re, L im, R re, R im), torch.stft(center=True, reflect) semantics."""
    pad = N_FFT // 2
    x = np.pad(x, ((0, 0), (pad, pad)), mode='reflect')
    idx = np.arange(DIM_T)[:, None] * HOP + np.arange(N_FFT)[None]
    S = np.fft.rfft(x[:, idx] * _WIN, axis=-1)[..., :DIM_F]          # [2, T, F]
    S = S.transpose(0, 2, 1)                                          # [2, F, T]
    return np.stack([S[0].real, S[0].imag, S[1].real, S[1].imag]).astype(np.float32)


def _istft(spec, n):
    S = np.zeros((2, N_FFT // 2 + 1, DIM_T), np.complex64)
    S[0, :DIM_F] = spec[0] + 1j * spec[1]
    S[1, :DIM_F] = spec[2] + 1j * spec[3]
    fr = np.fft.irfft(S.transpose(0, 2, 1), n=N_FFT, axis=-1) * _WIN   # [2, T, N_FFT]
    total = N_FFT + HOP * (DIM_T - 1)
    out, norm = np.zeros((2, total), np.float32), np.zeros(total, np.float32)
    for t in range(DIM_T):
        out[:, t * HOP:t * HOP + N_FFT] += fr[:, t]
        norm[t * HOP:t * HOP + N_FFT] += _WIN ** 2
    pad = N_FFT // 2
    return (out / np.maximum(norm, 1e-8))[:, pad:pad + n]


def separate(path, progress=lambda f: None):
    """The vocal stem of an audio/video file: float32 [2, n] at 44.1 kHz."""
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', core.local_path(path), '-vn', '-ac', '2', '-ar', str(SR),
                          '-f', 'f32le', '-'], capture_output=True, check=True).stdout
    mix = np.frombuffer(raw, np.float32).reshape(-1, 2).T.copy()
    n = mix.shape[1]
    chunk = HOP * (DIM_T - 1)
    trim = N_FFT // 2
    gen = chunk - 2 * trim
    pad_end = gen - n % gen
    m = np.pad(mix, ((0, 0), (trim, trim + pad_end)))
    out = np.zeros((2, n + pad_end), np.float32)
    starts = list(range(0, n + pad_end, gen))
    s = session()
    for k, i in enumerate(starts):
        spec = s.run(None, {'input': _stft(m[:, i:i + chunk])[None]})[0][0]
        wav = _istft(spec, chunk)
        out[:, i:i + gen] = wav[:, trim:trim + gen]
        progress((k + 1) / len(starts))
    return out[:, :n] * COMPENSATE


def envelope(path, d=None, progress=lambda f: None):
    """Loudness of the isolated vocal in dB, one value per 10 ms (cached in the clip's index folder)."""
    d = d or core.cache_dir(core.local_path(path))
    p = os.path.join(d, 'vocals_env.npy')
    if os.path.exists(p):
        return np.load(p)
    v = separate(path, progress).mean(axis=0)
    hop = SR // ENV_SR
    n = len(v) // hop
    rms = np.sqrt((v[:n * hop].reshape(n, hop) ** 2).mean(axis=1))
    env = (20 * np.log10(rms + 1e-6)).astype(np.float32)
    np.save(p, env)
    return env


def snap_words(words, env, floor_db=None):
    """Move each word's start forward to where the voice actually starts, when Whisper put it in the instrumental
    before (a sung line's first word) or over the held last note of the line before ("pensava…" 10.2–12.3 s, a
    pause, "Mudou" at 14.2 s that Whisper started at 11.2 s). The start goes to the end of the LAST pause in the
    voice (≥ 200 ms) inside the word. Then each end runs on while the voice holds the note, up to the next word
    ("pensava" ended at 11.2 s in Whisper, sung to 12.3 s: the caption left 20 frames early)."""
    if not len(env) or not words:
        return words, 0
    loud = np.percentile(env, 90)
    floor = floor_db if floor_db is not None else loud - 22      # singing vs. bleed left in the stem
    on = env > floor
    moved = 0
    out = []
    for w in words:
        a, b = int(w['s'] * ENV_SR), min(int(w['e'] * ENV_SR), len(on))
        if b - a < 20:
            out.append(w); continue
        seg = on[a:b - 8]                                         # the word keeps at least ~80 ms
        start, k = None, 0
        while k < len(seg):
            if not seg[k]:
                j = k
                while j < len(seg) and not seg[j]:
                    j += 1
                if j - k >= 20 and j < len(seg):                  # a pause (not a consonant), then voice again
                    start = j
                k = j
            else:
                k += 1
        if start is not None:
            ns = round((a + start) / ENV_SR - 0.05, 3)            # captions read better a hair before the syllable
            out.append({**w, 's': ns, 'snapped': round(ns - w['s'], 2)}); moved += 1
        else:
            out.append(w)
    for k, w in enumerate(out):
        nxt = int(out[k + 1]['s'] * ENV_SR) if k + 1 < len(out) else len(on)
        e = int(round(w['e'] * ENV_SR))
        if not (0 < e < min(nxt, len(on)) and on[e - 1]):
            continue
        j, quiet = e, 0
        while j < min(nxt, len(on)) and quiet < 10:               # dips under 100 ms are vibrato, not the end
            quiet = quiet + 1 if not on[j] else 0
            j += 1
        ne = round(min(j - quiet, nxt) / ENV_SR, 3)
        if ne - w['e'] >= 0.1:
            out[k] = {**w, 'e': ne, 'held': round(ne - w['e'], 2)}; moved += 1
    return out, moved
