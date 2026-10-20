"""Event recap / aftermovie from a folder of event footage + a song: the best moments of the night (steady, sharp,
loud — crowd, chorus, applause — with people in frame, little talking), the strongest one as the hook, the rest in
the order they happened, cut on the beat by the montage engine. Deterministic choice; the AI (or the editor) reviews
the storyboard and swaps moments."""
import math, os
from fractions import Fraction

import numpy as np

from claudio import core, montage

VIDEO_EXT = ('.mp4', '.mov', '.mxf', '.mts', '.m4v', '.avi', '.mkv')


def _videos(folder_or_paths):
    if isinstance(folder_or_paths, (list, tuple)):
        return [core.local_path(p) for p in folder_or_paths]
    d = core.local_path(folder_or_paths)
    out = []
    for root, dirs, files in os.walk(d):
        dirs[:] = [x for x in dirs if not x.startswith(('_', '#', '.')) and x.lower() not in ('proxy', 'proxies')]
        out += [os.path.join(root, f) for f in sorted(files) if f.lower().endswith(VIDEO_EXT) and not core.is_proxy(os.path.join(root, f))]
    return sorted(out)


def _energy(idx):
    try:
        db = np.load(os.path.join(core.cache_dir(idx['info']['path']), 'rms_db.npy'))
    except Exception:
        return None
    return db


def candidates(paths, faces=True, progress=lambda *_: None):
    """Every usable moment of every clip with its score and why. Clips must be indexed (index_folder) first."""
    out, skipped = [], []
    for k, p in enumerate(paths):
        progress(k / max(len(paths), 1))
        try:
            idx = core.build_index(p) if os.path.exists(os.path.join(core.cache_dir(p), 'index.json')) else None
        except Exception:
            idx = None
        if not idx or not len(core.load_quality(idx)):
            skipped.append(os.path.basename(p))
            continue
        db = _energy(idx)
        med = float(np.median(db)) if db is not None and len(db) else None
        fts = []
        if faces:
            try:
                from claudio import people
                fts = people.faces_clip(p)['with_faces']
            except Exception:
                fts = []
        t0 = os.path.getmtime(p) - idx['info']['duration']           # cameras stamp the file when the take ends
        for w in core.broll_windows(idx, min_len=1.2)['windows']:
            s, e = w['s'], w['e']
            why, score = [], w['score']
            peak = (s + e) / 2
            if db is not None and med is not None:
                a, b = int(s / core.HOP), max(int(e / core.HOP), int(s / core.HOP) + 1)
                seg = db[a:b]
                if len(seg):
                    sm = np.convolve(seg, np.ones(5) / 5, mode='same') if len(seg) > 5 else seg
                    peak = s + float(np.argmax(sm)) * core.HOP
                    lift = float(np.clip((np.percentile(seg, 80) - med) / 10, -0.5, 0.8))
                    score += lift
                    if lift > 0.3:
                        why.append('som forte (público/refrão)')
            if fts and any(s <= t <= e for t in fts):
                score += 0.25; why.append('pessoas')
            if w['speech_words'] > 3:
                score -= 0.15 * min(w['speech_words'], 12) / 12; why.append('tem fala')
            why.append(w['camera'])
            out.append({'path': p, 'clip': os.path.basename(p), 's': s, 'e': e, 'peak': round(peak, 2), 'score': round(score, 3),
                        'when': t0 + peak, 'why': ', '.join(why)})
    progress(1.0)
    return out, skipped


def select(cands, n, per_clip=2, gap=6.0):
    """n moments, best first, at most per_clip from one file and gap s apart inside it (relaxed when the pool is
    small); returned as: the best one (the hook), then the rest in the order they happened."""
    pool = sorted(cands, key=lambda c: -c['score'])
    for pc, g in ((per_clip, gap), (per_clip * 2, gap / 2), (99, 1.5)):
        got, used = [], {}
        for c in pool:
            mine = used.setdefault(c['path'], [])
            if len(mine) >= pc or any(abs(c['peak'] - x) < g for x in mine):
                continue
            got.append(c); mine.append(c['peak'])
            if len(got) == n:
                break
        if len(got) >= n or pc == 99:
            break
    if not got:
        return []
    hook = got[0]
    return [hook] + interleave(sorted(got[1:], key=lambda c: c['when']), hook['path'])


def interleave(seq, before=None):
    """Keep the order but never the same clip twice in a row (a jump cut): pull the next different clip forward."""
    seq, out = list(seq), []
    prev = before
    while seq:
        k = next((i for i, c in enumerate(seq) if c['path'] != prev), 0)
        out.append(seq.pop(k))
        prev = out[-1]['path']
    return out


def recap_edit(source, music, duration=45, aspect='9:16', pace='fast', music_start=None, name=None, end_card=None,
               audio='mute', faces=True, progress=lambda *_: None):
    paths = _videos(source)
    if not paths:
        return {'error': 'nenhum vídeo na pasta'}, None, None
    m = montage.music_map(core.local_path(music))
    beat = 60.0 / m['bpm'] if m.get('bpm') else 0.5
    if music_start is None:
        wins = montage.music_windows(m, duration)
        music_start = (wins[0]['start'] if wins else (m['downbeats'] or [0])[0])
    shot_s = max(montage.PACE_BEATS.get(pace, 1) * beat, 0.7)
    tail = 0.0
    if end_card:
        tail = core.light_index(core.local_path(end_card))['info']['duration']
    n = max(4, int(round((duration - tail) / shot_s)))
    cands, skipped = candidates(paths, faces=faces, progress=progress)
    if not cands:
        return {'error': 'nenhum clipe indexado — rode index_folder na pasta do evento antes', 'not_indexed': skipped}, None, None
    pick = select(cands, n, per_clip=max(2, math.ceil(n / max(len({c['path'] for c in cands}), 1))))

    def build(pick):
        shots = []
        for k, c in enumerate(pick):
            half = max(shot_s, 2.0)
            a, b = max(c['s'], c['peak'] - half), min(c['e'], c['peak'] + half)
            shots.append({'path': c['path'], 'in': round(a, 3), 'out': round(b, 3), 'audio': audio, 'anchor': 'center',
                          'label': ('gancho · ' if k == 0 else '') + c['clip']})
        if end_card:
            shots.append({'path': core.local_path(end_card), 'in': 0, 'out': tail, 'audio': 'mute', 'len': tail, 'label': 'vinheta'})
        return {'name': name or 'Recap', 'aspect': aspect, 'cut_on': 'beat', 'pace': pace, 'pace_follows_music': True,
                'music': {'path': core.local_path(music), 'start': music_start, 'gain_db': -2, 'fade_out': 1.5},
                'shots': shots, 'fade_in': 0.2, 'fade_out': 0.8}

    # the music decides each shot's length (slower sections hold longer): fit, then drop the weakest moments until it fits
    for _ in range(6):
        edit = build(pick)
        fit = montage.fit_edit(edit)
        over = fit['duration'] - duration
        if over <= 1.0 or len(pick) <= 4:
            break
        lens = sorted(((s['tl_out_f'] - s['tl_in_f']) / float(Fraction(fit['fps'])) for s in fit['shots'][:len(pick)]))
        avg = sum(lens) / len(lens)
        drop = max(1, int(round(over / avg)))
        weakest = sorted(range(1, len(pick)), key=lambda i: pick[i]['score'])[:drop]
        pick = [pick[0]] + interleave([c for i, c in enumerate(pick) if i and i not in weakest], pick[0]['path'])
    info = {'moments': [{'clip': c['clip'], 'at': c['peak'], 'score': c['score'], 'why': c['why']} for c in pick],
            'pool': len(cands), 'clips': len(paths), 'not_indexed': skipped, 'bpm': m.get('bpm'), 'music_start': music_start,
            'duration': fit['duration'], 'target': duration}
    return edit, info, fit
