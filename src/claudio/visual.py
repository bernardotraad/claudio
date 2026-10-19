"""claudio.visual — find shots by describing them ("hands on the guitar neck", "crowd with phones up", "road by day").

  embed_clip(path)        one CLIP image embedding every STEP seconds of the clip (from the proxy when there is one;
                          keyframes only on heavy long-GOP footage), cached next to the index
  search(paths, query)    the moments that best match a text description, grouped into windows per clip

CLIP ViT-B/16 (OpenAI) in ONNX on onnxruntime: no torch, runs on the CPU, ~600 MB downloaded once into the
Hugging Face cache. CLIP understands English best: write the query in English.
"""
import json, os, subprocess, threading

import numpy as np
from PIL import Image

from claudio import core

REPO = 'Xenova/clip-vit-base-patch16'
STEP = 2.0                     # seconds between embedded frames
VERSION = 1
MEAN = np.array([0.48145466, 0.4578275, 0.40821073], np.float32)
STD = np.array([0.26862954, 0.26130258, 0.27577711], np.float32)
_lock = threading.Lock()
_models = {}


def _sessions():
    with _lock:
        if not _models:
            import onnxruntime as ort
            from huggingface_hub import hf_hub_download
            from tokenizers import Tokenizer
            opt = ort.SessionOptions()
            opt.intra_op_num_threads = max(1, (os.cpu_count() or 4) - 1)
            prov = ['CPUExecutionProvider']
            _models['vision'] = ort.InferenceSession(hf_hub_download(REPO, 'onnx/vision_model.onnx'), opt, providers=prov)
            _models['text'] = ort.InferenceSession(hf_hub_download(REPO, 'onnx/text_model.onnx'), opt, providers=prov)
            _models['tok'] = Tokenizer.from_file(hf_hub_download(REPO, 'tokenizer.json'))
        return _models


def _prep(img):
    """CLIP preprocessing: shortest side 224 (bicubic), centre crop 224, normalise."""
    img = img.convert('RGB')
    w, h = img.size
    s = 224 / min(w, h)
    img = img.resize((max(224, round(w * s)), max(224, round(h * s))), Image.BICUBIC)
    w, h = img.size
    l, t = (w - 224) // 2, (h - 224) // 2
    a = (np.asarray(img.crop((l, t, l + 224, t + 224)), np.float32) / 255.0 - MEAN) / STD
    return a.transpose(2, 0, 1)


def embed_images(imgs, batch=16):
    m = _sessions()
    out = []
    for i in range(0, len(imgs), batch):
        x = np.stack([_prep(im) for im in imgs[i:i + batch]])
        out.append(m['vision'].run(None, {'pixel_values': x})[0])
    e = np.concatenate(out) if out else np.zeros((0, 512), np.float32)
    return e / np.maximum(np.linalg.norm(e, axis=1, keepdims=True), 1e-9)


def embed_text(query):
    m = _sessions()
    ids = m['tok'].encode(query).ids[:77]
    e = m['text'].run(None, {'input_ids': np.array([ids], np.int64)})[0][0]
    return e / max(float(np.linalg.norm(e)), 1e-9)


def _frames(path, step=STEP, width=256):
    """(times, images) every `step` s. Indexing keeps colour thumbnails from its own decode, so normally nothing is
    decoded here; clips indexed before v0.8 are decoded once (keyframes only when the codec is heavy)."""
    info = core.probe(path)
    got = core.load_thumbs(core.cache_dir(path), info)
    if got and got[0]:
        return got
    src = core.analysis_source(path)
    dur = float(info.get('duration') or 0)
    if dur <= 0 or not info.get('has_video', True):
        return [], []
    vf = f'{core.rot_vf(path)}scale={width}:-2'
    if core.heavy_codec(src):
        # 4K long-GOP 10-bit (R6 HEVC 4:2:2): decoding every keyframe costs ~0.3 s each and there are 2 per second, so
        # jump to the keyframe at each sample time instead and decode just that one, 4 at a time (~10x faster)
        from concurrent.futures import ThreadPoolExecutor
        ts = [k * step for k in range(int(dur // step) + 1)]

        def one(t):
            r = subprocess.run(['ffmpeg', '-v', 'error', *core._probe_opts(src), '-skip_frame', 'nokey', '-ss', f'{t:.3f}', '-i', src, '-an',
                                '-frames:v', '1', '-vf', vf, '-pix_fmt', 'rgb24', '-f', 'image2pipe', '-vcodec', 'ppm', '-'],
                               capture_output=True).stdout
            return _ppm(r)[:1]
        with ThreadPoolExecutor(4) as ex:
            got = list(ex.map(one, ts))
        keep = [(t, g[0]) for t, g in zip(ts, got) if g]
        return [t for t, _ in keep], [im for _, im in keep]
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', src, '-an', '-vf', f'fps=1/{step}:round=near,{vf}',
                          '-pix_fmt', 'rgb24', '-f', 'image2pipe', '-vcodec', 'ppm', '-'], capture_output=True).stdout
    ims = _ppm(raw)
    return [min(dur, k * step) for k in range(len(ims))], ims


def _ppm(raw):
    ims, i = [], 0
    while i < len(raw):
        hdr = raw[i:i + 64].split(maxsplit=4)
        w, h = int(hdr[1]), int(hdr[2])
        off = i + len(b' '.join(hdr[:4])) + 1
        ims.append(Image.frombytes('RGB', (w, h), raw[off:off + w * h * 3]))
        i = off + w * h * 3
    return ims


def embed_clip(path, force=False):
    """Cached image embeddings of a clip: {'times': [...], 'emb': (n, 512) float16}."""
    path = core.local_path(path)
    rot = core.get_rotation(path)
    p = os.path.join(core.cache_dir(path), f'clip_v{VERSION}' + (f'_r{rot}' if rot else '') + '.npz')   # per orientation
    if os.path.exists(p) and not force:
        z = np.load(p)
        return {'times': z['times'].tolist(), 'emb': z['emb'].astype(np.float32)}
    ts, ims = _frames(path)
    emb = embed_images(ims) if ims else np.zeros((0, 512), np.float32)
    np.savez_compressed(p, times=np.array(ts, np.float32), emb=emb.astype(np.float16))
    return {'times': ts, 'emb': emb}


def is_embedded(path):
    lp = core.local_path(path)
    rot = core.get_rotation(lp)
    return os.path.exists(os.path.join(core.cache_dir(lp), f'clip_v{VERSION}' + (f'_r{rot}' if rot else '') + '.npz'))


def search(paths, query, top=8, min_gap=6.0):
    """Best-matching moments for `query` across the clips: windows (start, end, peak, score) per clip, best first.
    Scores are CLIP cosine similarities (~0.2 unrelated, ~0.3 a good match): compare them, don't read them as %."""
    q = embed_text(query)
    hits = []
    for p in paths:
        e = embed_clip(p)
        if not len(e['times']):
            continue
        sim = e['emb'] @ q
        for k in np.argsort(-sim)[:max(top, 4)]:
            hits.append((float(sim[k]), p, float(e['times'][k]), sim, e['times']))
    hits.sort(key=lambda h: -h[0])
    out = []
    for score, p, t, sim, times in hits:
        if any(o['path'] == p and abs(o['peak'] - t) < min_gap for o in out):
            continue
        # grow the window while neighbours stay close to the peak score
        k = times.index(t) if t in times else int(np.argmin(np.abs(np.array(times) - t)))
        a = b = k
        while a > 0 and sim[a - 1] >= score - 0.02:
            a -= 1
        while b < len(times) - 1 and sim[b + 1] >= score - 0.02:
            b += 1
        out.append({'path': p, 'clip': os.path.basename(p), 'nle_path': core.nle_path(p), 'peak': round(t, 2),
                    'start': round(max(0.0, times[a] - STEP / 2), 2), 'end': round(times[b] + STEP / 2, 2),
                    'score': round(score, 3)})
        if len(out) >= top:
            break
    return out


def board(hits, width=320):
    """Contact sheet of the hits (the peak frame of each), labelled clip · time · score, for Claude to LOOK at."""
    from PIL import ImageDraw
    tiles = []
    for h in hits:
        try:
            im = core.grab(h['path'], h['peak'], width)
        except Exception:
            continue
        dr = ImageDraw.Draw(im)
        lab = f"{h['clip']}  {h['peak']:.1f}s  {h['score']:.3f}"
        dr.rectangle([0, im.height - 18, im.width, im.height], fill=(0, 0, 0))
        dr.text((4, im.height - 15), lab, fill=(255, 255, 255))
        tiles.append(im)
    if not tiles:
        return None
    cols = min(4, len(tiles))
    rows = -(-len(tiles) // cols)
    W, H = max(t.width for t in tiles), max(t.height for t in tiles)
    sheet = Image.new('RGB', (cols * W, rows * H), (20, 20, 20))
    for i, t in enumerate(tiles):
        sheet.paste(t, ((i % cols) * W, (i // cols) * H))
    return sheet
