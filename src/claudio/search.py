"""claudio.search — the panel's search box, WITHOUT any AI model (no credits): one query, every kind of evidence.

  search(folder, query)       what is SAID (transcripts), what the LOGS say (describe_shots / annotate), WHO (people named
                              with name_people) and what is SEEN (CLIP): one list per kind, best first, each hit with a
                              small thumbnail the panel shows
  similar(folder, path, t)    the moments that look like that one (Match Similar / the frame in the Source Monitor)
  phrase_hits(idxs, phrase)   fuzzy search over word-level transcripts (also behind the find_phrase tool)

Only clips already indexed are searched (nothing is decoded or transcribed here); clips without visual embeddings yet
are counted, not embedded — "Descrever planos" prepares them.
"""
import base64, difflib, io, os

from claudio import core

KINDS = ('fala', 'notas', 'pessoa', 'visual')


def phrase_hits(idxs, phrase, max_hits=10, min_ratio=0.6):
    """Where the line is said: [{path, s, e, said, similarity}], repeated takes as separate hits, best first."""
    q = [core.norm(w) for w in phrase.split() if core.norm(w)]
    hits = []
    for idx in idxs:
        W = core.load_words(idx)
        toks = [core.norm(w['w']) for w in W]
        n = len(q)
        for i in range(0, max(1, len(W) - n + 1)):
            for L in (n - 1, n, n + 1):
                if L < 1 or i + L > len(W):
                    continue
                r = difflib.SequenceMatcher(None, q, toks[i:i + L], autojunk=False).ratio()
                if r >= min_ratio:
                    hits.append({'path': idx['info']['nle_path'], 's': W[i]['s'], 'e': W[i + L - 1]['e'],
                                 'said': ' '.join(w['w'] for w in W[i:i + L]), 'similarity': round(r, 2)})
    hits.sort(key=lambda h: (-h['similarity'], h['s']))
    best = []
    for h in hits:  # drop overlapping duplicates
        if not any(b['path'] == h['path'] and b['s'] < h['e'] and h['s'] < b['e'] for b in best):
            best.append(h)
    return best[:max_hits]


def log_hits(idxs, query, max_hits=12):
    """Log lines (descriptions, notes, tags) that contain every word of the query, accents and case ignored."""
    q = [core.norm(w) for w in query.split() if core.norm(w)]
    out = []
    for idx in idxs:
        for l in core.read_log(idx):
            toks = {core.norm(w) for w in ' '.join([l['text'], *(l.get('tags') or [])]).split()}
            if q and all(any(t.startswith(w) for t in toks) for w in q):
                out.append({'path': idx['info']['nle_path'], 's': l['s'], 'e': l['e'], 'text': l['text'],
                            'auto': 'auto' in (l.get('tags') or [])})
    out.sort(key=lambda h: (h['auto'], h['path'], h['s']))       # the editor's / Claude's notes before the automatic ones
    return out[:max_hits]


def person_hits(folder, query, max_hits=12, gap=4.0):
    """Where a person named with name_people appears (face windows) and speaks (voice lines). Nothing when the query
    is not a saved name — clustering is never run for a plain search."""
    from claudio import people as P
    names = P._load_names(folder)
    q = core.norm(query.replace(' ', ''))
    known = {n['name'] for k in ('faces', 'voices') for n in names.get(k, [])}
    who = [n for n in known if q and (core.norm(n.replace(' ', '')) == q or core.norm(n.split()[0]) == q)]
    if not who:
        return []
    res = P.people(folder)
    out = []
    for r in res['faces']:
        if r['name'] in who:
            for c in r['clips']:
                ts = c['times']
                a = ts[0]
                for x, y in zip(ts, ts[1:] + [None]):
                    if y is None or y - x > gap:
                        out.append({'path': c['nle_path'], 's': max(0.0, a - 1), 'e': x + 1, 'text': f"{r['name']} (rosto)"})
                        if y is not None:
                            a = y
    for r in res['voices']:
        if r['name'] in who:
            for f, x in sorted(r['members'], key=lambda m: -(m[1]['e'] - m[1]['s'])):
                out.append({'path': core.nle_path(f), 's': x['s'], 'e': x['e'], 'text': f"{r['name']}: “{x['text'][:90]}”"})
    out.sort(key=lambda h: -(h['e'] - h['s']))
    return out[:max_hits]


def _indexed(folder):
    from claudio import indexer
    idxs, missing = [], 0
    for p in indexer.list_media(folder):
        try:
            if os.path.exists(os.path.join(core.cache_dir(p), 'index.json')):
                idxs.append(core.build_index(p))
                continue
        except Exception:
            pass
        missing += 1
    return idxs, missing


def _thumbs(cards, width=200):
    """Each card gets a small JPEG (data: URI) at its peak: from the colour thumbnails the indexing kept (no decode),
    else one frame grabbed (4 at a time) and kept in the clip's cache, so the next search is instant."""
    from concurrent.futures import ThreadPoolExecutor
    kept = {}

    def one(c):
        lp, t = core.local_path(c['nle_path']), c['peak']
        try:
            d, got = core.cache_dir(lp), kept.get(lp)
            if got and got[0]:
                im = got[1][min(range(len(got[0])), key=lambda k: abs(got[0][k] - t))]
            else:
                p = os.path.join(d, 'search_thumbs', f'{t:.1f}.jpg')
                if not os.path.exists(p):
                    os.makedirs(os.path.dirname(p), exist_ok=True)
                    im = core.grab(lp, t, width, keyframe=True)
                    im.convert('RGB').save(p, 'JPEG', quality=72)
                return 'data:image/jpeg;base64,' + base64.b64encode(open(p, 'rb').read()).decode()
        except Exception:
            return None
        if im.width > width:
            im = im.resize((width, max(1, round(im.height * width / im.width))))
        b = io.BytesIO()
        im.convert('RGB').save(b, 'JPEG', quality=72)
        return 'data:image/jpeg;base64,' + base64.b64encode(b.getvalue()).decode()
    for c in cards:                     # one load of each clip's thumbnails, before the threads share them
        lp = core.local_path(c['nle_path'])
        if lp not in kept:
            try:
                kept[lp] = core.load_thumbs(core.cache_dir(lp), core.probe(lp))
            except Exception:
                kept[lp] = None
    with ThreadPoolExecutor(4) as ex:
        for c, t in zip(cards, ex.map(one, cards)):
            c['thumb'] = t
    return cards


def _visual_files(idxs):
    from claudio import visual
    vids = [i['info']['path'] for i in idxs if i['info'].get('has_video', True) and i['info'].get('width')]
    ready = [p for p in vids if visual.is_embedded(p)]
    return ready, len(vids) - len(ready)


def _card(kind, h, s, e, peak=None, text=None, score=None):
    path = core.local_path(h.get('nle_path') or h['path'])
    return {'kind': kind, 'clip': os.path.basename(path), 'nle_path': core.nle_path(path),
            's': round(float(s), 2), 'e': round(float(e), 2), 'peak': round(float(peak if peak is not None else s), 2),
            'text': text, 'score': score}


def search(folder, query, kinds=KINDS, top=8):
    idxs, missing = _indexed(folder)
    out = {'query': query, 'clips': len(idxs), 'not_indexed': missing, 'groups': {}}
    if 'fala' in kinds:
        speech = [i for i in idxs if i.get('speech')]
        out['groups']['fala'] = [_card('fala', h, h['s'], h['e'], text=h['said'], score=h['similarity'])
                                 for h in phrase_hits(speech, query, top, 0.75 if len(query.split()) > 1 else 0.8)]
    if 'notas' in kinds:
        out['groups']['notas'] = [_card('notas', h, h['s'], h['e'], text=h['text']) for h in log_hits(idxs, query, top)]
    if 'pessoa' in kinds:
        try:
            out['groups']['pessoa'] = [_card('pessoa', h, h['s'], h['e'], text=h['text'])
                                       for h in person_hits(folder, query, top)]
        except Exception as e:
            out['groups']['pessoa'], out['people_error'] = [], f'{type(e).__name__}: {e}'
    if 'visual' in kinds:
        from claudio import visual
        files, out['not_embedded'] = _visual_files(idxs)
        out['groups']['visual'] = [_card('visual', h, h['start'], h['end'], h['peak'], score=h['score'])
                                   for h in (visual.search(files, query, top) if files else [])]
    _thumbs([c for g in out['groups'].values() for c in g])
    return out


def similar(folder, path, t, top=12):
    """Shots that look like `path` at t, across the folder's embedded clips (+ the reference frame itself)."""
    from claudio import visual
    lp = core.local_path(path)
    info = core.probe(lp)
    t, dur = float(t), float(info.get('duration') or t)
    if t > dur and info.get('start_tc'):          # a position counted from the camera's timecode, not from 0
        clk = core.clock_of(info)
        t -= float(clk.sec(clk.start))
    t = max(0.0, min(t, dur))
    idxs, missing = _indexed(folder)
    files, not_emb = _visual_files(idxs)
    hits = visual.similar(files, lp, t, top) if files else []
    ref = {'clip': os.path.basename(lp), 'nle_path': core.nle_path(lp), 'peak': round(t, 2)}
    cards = [_card('visual', h, h['start'], h['end'], h['peak'], score=h['score']) for h in hits]
    _thumbs([ref, *cards])
    return {'ref': ref, 'clips': len(idxs), 'not_indexed': missing, 'not_embedded': not_emb, 'groups': {'visual': cards}}
