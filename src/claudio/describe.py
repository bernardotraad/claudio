"""claudio.describe — what is seen in each stretch of a clip, written into its log without any AI model (no credits).

  describe_clip(path)    splits the clip where the picture changes, and for each stretch combines
                           · CLIP zero-shot on the visual-search embeddings: framing, place, things in shot, day/night
                           · who appears (faces matched to the names confirmed with name_people)
                           · what is said (transcript)
                         → annotate(…, tags=['auto', …]). Earlier 'auto' entries are replaced; descriptions written
                         by Claude or the editor are never touched.

A first pass for searching and logging, not a verdict: rich or subtle shots still deserve a look (contact_sheet).
"""
import json, os

import numpy as np

from claudio import core, visual

AUTO = 'auto'
# (group, label shown, CLIP prompt). One winner per group in 'pick'; any number above the bar in 'many'.
PROMPTS = [
    ('plano', 'close de rosto', "a close-up photo of a person's face"),
    ('plano', 'plano médio', 'a medium shot of a person from the waist up'),
    ('plano', 'plano aberto', 'a wide shot of a whole scene'),
    ('plano', 'detalhe', 'an extreme close-up detail of an object or hands'),
    ('lugar', 'palco / show', 'a band performing on a stage with stage lights'),
    ('lugar', 'plateia', 'a crowd of people at a concert'),
    ('lugar', 'estrada', 'a road seen from a moving vehicle'),
    ('lugar', 'dentro do carro/van', 'the inside of a car or a van'),
    ('lugar', 'rua', 'a city street with buildings'),
    ('lugar', 'bar / restaurante', 'the inside of a bar or a restaurant'),
    ('lugar', 'bastidores', 'a backstage room with music equipment'),
    ('lugar', 'interior', 'a room inside a house'),
    ('lugar', 'natureza', 'trees and nature outdoors'),
    ('lugar', 'fachada', 'the front of a building seen from the street'),
    ('lugar', 'escritório', 'an office with desks and computers'),
    ('many', 'violão/guitarra', 'a guitar'),
    ('many', 'bateria', 'a drum kit'),
    ('many', 'microfone', 'a microphone'),
    ('many', 'teclado', 'a keyboard instrument'),
    ('many', 'comida/bebida', 'food or drinks on a table'),
    ('many', 'celular', 'a person holding a mobile phone'),
    ('many', 'letreiro/placa', 'a sign with written text'),
    ('many', 'carro', 'a car'),
    ('many', 'criança', 'a child'),
    ('luz', 'noite', 'a dark scene at night'),
    ('luz', 'dia', 'a bright scene in daylight'),
]
GROUPS_PICK = ('plano', 'lugar', 'luz')
MIN_PICK = 0.30          # probability within the group to name it
MANY_MARGIN = 0.022      # cosine above the median of the 'many' prompts to list an object
SPLIT = 0.86             # consecutive-frame similarity below this starts a new stretch
MAX_LEN = 12.0           # seconds; long static shots are cut into readable stretches
_txt = {}


def _text_bank():
    if not _txt:
        _txt['E'] = np.stack([visual.embed_text(p) for _, _, p in PROMPTS])
    return _txt['E']


def _segments(times, emb, dur, cuts):
    """Stretches [a, b) where the picture is continuous: the index's cuts, a drop in frame-to-frame similarity, or MAX_LEN."""
    if not len(times):
        return []
    bounds = [0.0]
    for k in range(1, len(times)):
        t = times[k]
        hard = any(bounds[-1] < c <= t for c in cuts)
        soft = float(emb[k] @ emb[k - 1]) < SPLIT
        if (hard or soft or t - bounds[-1] >= MAX_LEN) and t - bounds[-1] >= 2.0:
            bounds.append(t)
    bounds.append(max(dur, times[-1] + visual.STEP / 2))
    return [(a, b) for a, b in zip(bounds, bounds[1:]) if b - a > 0.5]


def _people_names(folder):
    """Confirmed names → face centroids (people.json), to say who is in a stretch."""
    p = os.path.join(core.data_dir(folder, create=False), 'people.json')
    if not os.path.exists(p):
        return []
    return [(n['name'], np.asarray(n['centroid'], np.float32)) for n in json.load(open(p, encoding='utf-8')).get('faces', [])]


def describe_clip(path, names=None):
    path = core.local_path(path)
    idx = core.build_index(path)
    info = idx['info']
    if not info.get('has_video'):
        return {'clip': os.path.basename(path), 'stretches': 0, 'skipped': 'sem vídeo'}
    e = visual.embed_clip(path)
    times, emb = list(e['times']), np.asarray(e['emb'], np.float32)
    if not len(times):
        return {'clip': os.path.basename(path), 'stretches': 0, 'skipped': 'sem frames'}
    T = _text_bank()
    cuts = idx.get('picture', {}).get('cuts_sec', []) or []
    segs = _segments(times, emb, float(info.get('duration') or times[-1]), cuts)
    words = []
    wp = os.path.join(idx['cache'], 'words.json')
    if os.path.exists(wp):
        words = json.load(open(wp))
    faces = []
    if names is None:
        names = _people_names(os.path.dirname(path))
    if names:
        try:
            from claudio import people
            faces = people.faces_clip(path)['faces']
        except Exception:
            faces = []
    log = [l for l in core.read_log(idx) if AUTO not in (l.get('tags') or [])]       # keep Claude's / the editor's
    groups = [g for g, _, _ in PROMPTS]
    out = []
    for a, b in segs:
        sel = [k for k, t in enumerate(times) if a <= t < b] or [int(np.argmin([abs(t - a) for t in times]))]
        v = emb[sel].mean(0)
        v = v / max(float(np.linalg.norm(v)), 1e-9)
        sim = T @ v
        parts, tags = [], []
        for g in GROUPS_PICK:
            ks = [k for k, gg in enumerate(groups) if gg == g]
            z = np.exp(100 * (sim[ks] - sim[ks].max()))
            pr = z / z.sum()
            j = int(np.argmax(pr))
            if pr[j] >= MIN_PICK:
                parts.append(PROMPTS[ks[j]][1]); tags.append(PROMPTS[ks[j]][1])
        ks = [k for k, gg in enumerate(groups) if gg == 'many']
        med = float(np.median(sim[ks]))
        objs = [PROMPTS[k][1] for k in sorted(ks, key=lambda k: -sim[k]) if sim[k] >= med + MANY_MARGIN][:2]
        if objs:
            parts.append(', '.join(objs)); tags += objs
        who = []
        for f in faces:
            if a <= f['t'] < b and names:
                nm, s = max(((n, float(c @ f['emb'])) for n, c in names), key=lambda x: x[1])
                if s > 0.42 and nm not in who:
                    who.append(nm)
        if who:
            parts.append(' e '.join(who)); tags += who
        said = [w['w'] for w in words if a <= w['s'] < b]
        if said:
            txt = ' '.join(said)
            parts.append(f'fala: “{txt[:80]}{"…" if len(txt) > 80 else ""}”')
            tags.append('fala')
        else:
            tags.append('sem fala')
        text = ' · '.join(parts) or 'plano sem classificação'
        log.append({'s': round(a, 2), 'e': round(b, 2), 'text': text, 'tags': [AUTO] + tags, 'rating': None,
                    'tc_in': core.clock_of(info).tc(core.clock_of(info).frame(a, 'floor'))})
        out.append({'s': round(a, 2), 'e': round(b, 2), 'text': text})
    log.sort(key=lambda x: x['s'])
    json.dump(log, open(core.log_path(idx), 'w'), ensure_ascii=False, indent=1)
    try:
        from claudio import sidecar
        sidecar.write_clip_md(idx)
    except Exception:
        pass
    return {'clip': os.path.basename(path), 'nle_path': core.nle_path(path), 'stretches': len(out), 'sample': out[:3]}


def describe(paths, progress=lambda *_: None):
    res = []
    folders = {}
    for k, p in enumerate(paths):
        progress(k / max(len(paths), 1), os.path.basename(p))
        lp = core.local_path(p)
        d = os.path.dirname(lp)
        if d not in folders:
            folders[d] = _people_names(d)
        try:
            res.append(describe_clip(lp, folders[d]))
        except Exception as e:
            res.append({'clip': os.path.basename(lp), 'error': str(e)})
    for d in folders:
        try:
            from claudio import sidecar
            sidecar.write_folder_md(d)
        except Exception:
            pass
    return res
