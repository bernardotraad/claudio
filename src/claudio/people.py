"""claudio.people — who appears and who speaks, the same person under the same name in every clip of a job.

  faces_clip(path)     faces found on the indexing thumbnails, recognised on up to SAMPLES keyframes at 960 px
                       (YuNet detector + SFace recogniser, OpenCV ONNX)
  voices_clip(path)    one voice print per utterance of the transcript (WeSpeaker ResNet34 via sherpa-onnx)
  people(folder)       faces and voices of every clip clustered across the folder → P1, P2… (faces) and V1, V2…
                       (voices), each with where it appears / what it says; names the editor gave are kept by the
                       person's centroid, so they survive new clips and re-runs
  name(folder, map)    {"P1": "Caio", "V2": "Caio"}

Everything runs on the CPU, no torch; the models (~66 MB) are downloaded once into ~/.cache/claudio/models.
A face is a likeness, not an identity check: the editor confirms the names.
"""
import json, os, threading, urllib.request
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image, ImageDraw

from claudio import core

VERSION = 1
SAMPLES = 6                    # hi-res keyframes per clip for recognition
FACE_MIN = 48                  # px, at 960 wide
FACE_T, VOICE_T = 0.42, 0.55   # average-linkage cosine thresholds (same person above)
MODELS = {
    'yunet.onnx': 'https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx',
    'sface.onnx': 'https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx',
    'wespeaker_en_voxceleb_resnet34_LM.onnx':
        'https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/wespeaker_en_voxceleb_resnet34_LM.onnx',
}
_lock = threading.Lock()
_m = {}


def _model(name):
    d = os.path.join(core.CACHE, 'models')
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, name)
    if not os.path.exists(p):
        tmp = p + '.part'
        urllib.request.urlretrieve(MODELS[name], tmp)
        os.replace(tmp, p)
    return p


def _rec():
    import cv2
    with _lock:
        if 'rec' not in _m:
            _m['rec'] = cv2.FaceRecognizerSF.create(_model('sface.onnx'), '')
        return _m['rec']


def _det(w, h, score=0.75):
    import cv2
    return cv2.FaceDetectorYN.create(_model('yunet.onnx'), '', (w, h), score, 0.3, 50)


def _voice():
    import sherpa_onnx
    with _lock:
        if 'voice' not in _m:
            _m['voice'] = sherpa_onnx.SpeakerEmbeddingExtractor(sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=_model('wespeaker_en_voxceleb_resnet34_LM.onnx'), num_threads=max(1, (os.cpu_count() or 4) - 1)))
        return _m['voice']


def _unit(e):
    e = np.asarray(e, np.float32).ravel()
    return e / max(float(np.linalg.norm(e)), 1e-9)


# ───────────────────────────── per clip ─────────────────────────────
def faces_clip(path, force=False):
    """{'faces': [{'t', 'box' (x,y,w,h at 960 wide), 'emb'}], 'with_faces': [thumb times with any face]}; crops saved
    as JPEG next to it for the boards."""
    import cv2
    path = core.local_path(path)
    d = core.cache_dir(path)
    rot = core.get_rotation(path)
    tag = f'faces_v{VERSION}' + (f'_r{rot}' if rot else '')        # per orientation (sideways footage)
    p = os.path.join(d, f'{tag}.npz')
    if os.path.exists(p) and not force:
        z = np.load(p, allow_pickle=True)
        return {'faces': list(z['faces']), 'with_faces': z['with_faces'].tolist()}
    info = core.probe(path)
    if not info.get('has_video'):
        np.savez(p, faces=np.array([], object), with_faces=np.array([]))
        return {'faces': [], 'with_faces': []}
    th = core.load_thumbs(d, info)
    if not th:
        from claudio import visual
        th = visual._frames(path)
    ts, ims = th
    cand, with_faces = [], []
    if ims:
        det = _det(*ims[0].size, score=0.7)
        for t, im in zip(ts, ims):
            _, fs = det.detect(cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2BGR))
            if fs is not None and len(fs):
                with_faces.append(t)
                cand.append((float(fs[:, 2].max()), t))
    # the frames with the biggest faces, spread over the clip
    best = sorted(cand, reverse=True)[:SAMPLES * 3]
    pick = []
    for _, t in sorted(best, key=lambda x: x[1]):
        if not pick or t - pick[-1] >= max(2.0, (ts[-1] if ts else 0) / (SAMPLES * 2)):
            pick.append(t)
    pick = sorted(sorted(pick, key=lambda t: -dict((tt, s) for s, tt in cand)[t])[:SAMPLES])
    with ThreadPoolExecutor(3) as ex:
        big = list(ex.map(lambda t: core.grab(path, t, 960, keyframe=True), pick))
    rec, faces = _rec(), []
    crops_dir = os.path.join(d, tag)
    os.makedirs(crops_dir, exist_ok=True)
    for t, im in zip(pick, big):
        a = cv2.cvtColor(np.asarray(im.convert('RGB')), cv2.COLOR_RGB2BGR)
        h, w = a.shape[:2]
        _, fs = _det(w, h, 0.8).detect(a)
        for k, f in enumerate(fs if fs is not None else []):
            if f[2] < FACE_MIN:
                continue
            al = rec.alignCrop(a, f)
            crop = os.path.join(crops_dir, f'{t:.1f}_{k}.jpg')
            cv2.imwrite(crop, al)
            faces.append({'t': round(float(t), 2), 'box': [int(v) for v in f[:4]], 'emb': _unit(rec.feature(al)), 'crop': crop})
    np.savez(p, faces=np.array(faces, object), with_faces=np.array(with_faces))
    return {'faces': faces, 'with_faces': with_faces}


def _speech_segments(words, gap=0.5, max_len=10.0, min_len=1.2):
    segs, cur = [], []
    for w in words:
        if cur and (w['s'] - cur[-1]['e'] > gap or w['e'] - cur[0]['s'] > max_len):
            segs.append(cur); cur = []
        cur.append(w)
    if cur:
        segs.append(cur)
    return [(c[0]['s'], c[-1]['e'], ' '.join(x['w'] for x in c)) for c in segs if c[-1]['e'] - c[0]['s'] >= min_len]


def voices_clip(path, force=False):
    """[{'s', 'e', 'text', 'emb'}] one voice print per utterance (≥ 1.2 s) of the clip's transcript."""
    path = core.local_path(path)
    d = core.cache_dir(path)
    p = os.path.join(d, f'voices_v{VERSION}.npz')
    if os.path.exists(p) and not force:
        return list(np.load(p, allow_pickle=True)['segs'])
    pcm = os.path.join(d, 'a16.pcm')
    wp = os.path.join(d, 'words.json')
    segs = []
    if os.path.exists(pcm) and os.path.exists(wp):
        x = np.fromfile(pcm, np.int16).astype(np.float32) / 32768.0
        ext = _voice()
        for s, e, txt in _speech_segments(json.load(open(wp))):
            a = x[int(s * core.SR):int(e * core.SR)]
            if len(a) < core.SR:
                continue
            st = ext.create_stream()
            st.accept_waveform(core.SR, a)
            st.input_finished()
            if not ext.is_ready(st):
                continue
            segs.append({'s': round(s, 2), 'e': round(e, 2), 'text': txt, 'emb': _unit(ext.compute(st))})
    np.savez(p, segs=np.array(segs, object))
    return segs


# ───────────────────────────── across the folder ─────────────────────────────
def _cluster(E, thr):
    """Average-linkage agglomerative clustering on cosine similarity. Returns a label per row."""
    n = len(E)
    if not n:
        return []
    S = E @ E.T
    groups = {i: [i] for i in range(n)}
    sim = {}
    keys = list(groups)
    for i in range(n):
        for j in range(i + 1, n):
            sim[(i, j)] = float(S[i, j])
    while len(groups) > 1:
        (a, b), s = max(sim.items(), key=lambda kv: kv[1]) if sim else ((None, None), -1)
        if s < thr:
            break
        ga, gb = groups[a], groups.pop(b)
        groups[a] = ga + gb
        sim = {k: v for k, v in sim.items() if b not in k and a not in k}
        for c in groups:
            if c == a:
                continue
            k = (min(a, c), max(a, c))
            sim[k] = float(S[np.ix_(groups[a], groups[c])].mean())
    lab = [0] * n
    for g, (_, members) in enumerate(sorted(groups.items(), key=lambda kv: -len(kv[1]))):
        for i in members:
            lab[i] = g
    return lab


def _names_path(folder):
    return os.path.join(core.data_dir(core.local_path(folder)), 'people.json')


def _load_names(folder):
    p = _names_path(folder)
    return json.load(open(p, encoding='utf-8')) if os.path.exists(p) else {'faces': [], 'voices': []}


def people(folder, progress=lambda *_: None, paths=None):
    """Cluster every face and voice of the folder. Returns persons (faces) and voices with ids, names, clips and
    times/quotes; ids are P1… by screen time and V1… by talk time."""
    from claudio import indexer
    files = [core.local_path(p) for p in paths] if paths else indexer.list_videos(folder)
    F, V = [], []
    for k, f in enumerate(files):
        progress(k / max(len(files), 1), os.path.basename(f))
        try:
            for x in faces_clip(f)['faces']:
                F.append((f, x))
        except Exception:
            pass
        try:
            for x in voices_clip(f):
                V.append((f, x))
        except Exception:
            pass
    named = _load_names(folder)
    out = {}
    for kind, items, thr, tag, key in (('faces', F, FACE_T, 'P', 't'), ('voices', V, VOICE_T, 'V', 's')):
        E = np.array([x['emb'] for _, x in items], np.float32) if items else np.zeros((0, 1), np.float32)
        lab = _cluster(E, thr)
        groups = {}
        for (f, x), g in zip(items, lab):
            groups.setdefault(g, []).append((f, x))
        rows = []
        for g, mem in groups.items():
            cen = _unit(np.mean([x['emb'] for _, x in mem], 0))
            name, best = None, thr
            for nm in named.get(kind, []):                       # names stick to the person, not to the id
                s = float(cen @ _unit(nm['centroid']))
                if s > best:
                    name, best = nm['name'], s
            clips = {}
            for f, x in mem:
                clips.setdefault(f, []).append(x)
            row = {'centroid': cen, 'name': name, 'count': len(mem),
                   'clips': [{'clip': os.path.basename(f), 'path': f, 'nle_path': core.nle_path(f),
                              'times': sorted({round(float(x[key]), 1) for x in xs})} for f, xs in clips.items()],
                   'members': mem}
            if kind == 'voices':
                row['seconds'] = round(sum(x['e'] - x['s'] for _, x in mem), 1)
                row['quotes'] = [f"{os.path.basename(f)} {x['s']:.1f}s: “{x['text'][:90]}”"
                                 for f, x in sorted(mem, key=lambda m: -(m[1]['e'] - m[1]['s']))[:3]]
            rows.append(row)
        rows.sort(key=lambda r: -(r.get('seconds') or r['count']))
        for i, r in enumerate(rows):
            r['id'] = f'{tag}{i + 1}'
        out[kind] = rows
    return out


def summary(res):
    """JSON-friendly view of people(): no embeddings."""
    def strip(r):
        return {k: v for k, v in r.items() if k not in ('centroid', 'members')}
    return {'faces': [strip(r) for r in res['faces']], 'voices': [strip(r) for r in res['voices']]}


def name(folder, mapping, res=None):
    """Remember names: {"P1": "Caio", "V2": "Caio"}. Stored by centroid in <folder>/_claudio/people.json."""
    res = res or people(folder)
    named = _load_names(folder)
    done = {}
    for kind in ('faces', 'voices'):
        for r in res[kind]:
            nm = mapping.get(r['id'])
            if not nm:
                continue
            named[kind] = [x for x in named.get(kind, []) if x['name'] != nm or float(_unit(x['centroid']) @ r['centroid']) < 0.3]
            named[kind].append({'name': nm, 'centroid': [round(float(v), 5) for v in r['centroid']]})
            done[r['id']] = nm
    json.dump(named, open(_names_path(folder), 'w', encoding='utf-8'), ensure_ascii=False)
    return done


def board(res, per=8):
    """One row per face cluster: label (id · name · count) and up to `per` crops."""
    rows = [r for r in res['faces'] if r['count'] >= 1][:12]
    if not rows:
        return None
    S = 96
    img = Image.new('RGB', (150 + S * per, S * len(rows)), (22, 21, 20))
    dr = ImageDraw.Draw(img)
    for y, r in enumerate(rows):
        dr.text((8, y * S + 30), f"{r['id']}  {r['name'] or '?'}", fill=(240, 230, 210))
        dr.text((8, y * S + 50), f"{r['count']} rostos · {len(r['clips'])} clipes", fill=(160, 150, 140))
        mem = r['members']
        step = max(1, len(mem) // per)
        for k, (_, x) in enumerate(mem[::step][:per]):
            try:
                im = Image.open(x['crop']).convert('RGB').resize((S - 4, S - 4))
                img.paste(im, (150 + k * S, y * S + 2))
            except Exception:
                pass
    return img
