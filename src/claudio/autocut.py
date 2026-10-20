"""Multicam cut by who is speaking (podcast / interview / panel): every person has a mic, every camera frames someone
(or everyone: the wide). From the mics, decide who talks when; cut to their camera, never shorter than min_hold, the
wide when two talk at once or nobody does for a while, and a cutaway on long monologues. Deterministic, no AI.
Input: the synced sequence (sync_multicam or the editor's own) — output: a NEW sequence with the cut on a new top
video track and the camera tracks kept below, disabled, so any alternate angle is one click away."""
import os, re, xml.etree.ElementTree as ET

import numpy as np

from claudio import core, nle_read

HOP = 0.1          # activity resolution (s)


def _channel_db(path, channel):
    """dB per HOP of one channel of a file (cached next to the clip's other analysis)."""
    p = core.local_path(path)
    c = os.path.join(core.cache_dir(p), f'act_ch{channel}.npy')
    if os.path.exists(c):
        return np.load(c)
    raw = core.run(['ffmpeg', '-v', 'error', '-i', core.analysis_source(p), '-vn', '-map', '0:a:0',
                    '-af', f'pan=mono|c0=c{max(channel - 1, 0)}', '-ar', '8000', '-f', 's16le', '-']).stdout
    x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    hop = int(8000 * HOP)
    n = len(x) // hop
    db = (10 * np.log10((x[:n * hop].reshape(n, hop) ** 2).mean(1) + 1e-10)).astype(np.float32) if n else np.zeros(0, np.float32)
    np.save(c, db)
    return db


def _track_names(layers, kind):
    """Track names exactly as read_fcp7 gives them (empty tracks are not counted)."""
    names, n = {}, 0
    for k, (_, its) in enumerate(layers.get(kind, [])):
        if its:
            n += 1
            names[f'{kind[0].upper()}{n}'] = k
    return names


def activity(tl, mics, dur):
    """{speaker: dB array on the timeline clock} from the mic tracks (A3, …) of the sequence."""
    T = int(np.ceil(dur / HOP)) + 1
    out = {}
    for spk, tr in mics.items():
        a = np.full(T, -100.0, np.float32)
        for it in [i for i in tl['items'] if i['track'] == tr.upper() and i['path'] and i['enabled']]:
            db = _channel_db(it['path'], it.get('channel', 1))
            s0 = int(round(it['tl_in'] / HOP)); n = int(round((it['tl_out'] - it['tl_in']) / HOP))
            k0 = int(round(it['src_in'] / HOP))
            seg = db[k0:k0 + n]
            a[s0:s0 + len(seg)] = np.maximum(a[s0:s0 + len(seg)], seg + 20 * np.log10(max(it.get('gain', 1.0), 1e-4)))
        out[spk] = a
    return out


def who_speaks(act, margin_db=6.0, over_floor_db=14.0, hang=0.6, min_turn=0.5):
    """Per HOP: the set of people talking. A person talks when their mic is well above its own floor and not just the
    bleed of someone louder in it; short gaps inside a turn are bridged (hang) and blips dropped (min_turn)."""
    names = list(act)
    A = np.stack([act[n] for n in names])
    floor = np.percentile(A, 15, axis=1, keepdims=True)
    loud = A > floor + over_floor_db
    best = A.max(0, keepdims=True)
    talk = loud & (A >= best - margin_db)
    h, m = max(1, int(hang / HOP)), max(1, int(min_turn / HOP))
    for k in range(len(names)):
        talk[k] = _runs(_runs(talk[k], False, h), True, m)
    return names, talk


def _runs(x, value, shorter_than):
    """Flip runs of `value` shorter than n frames (inside the signal) — False runs: bridge gaps; True runs: drop blips."""
    x = x.copy()
    n = len(x)
    i = 0
    while i < n:
        j = i
        while j < n and x[j] == x[i]:
            j += 1
        inner = (i > 0 and j < n) if not value else True
        if x[i] == value and j - i < shorter_than and inner:
            x[i:j] = not value
        i = j
    return x


def plan(names, talk, cameras, dur, min_hold=2.0, idle_wide=2.5, max_hold=12.0, cutaway=2.5, lead=0.15):
    """[(start, end, camera track, why)]: the speaker's own camera (a two-shot/wide that frames them if they have
    none), the wide on crosstalk or after idle_wide of silence, a cutaway (wide, else a listener) every max_hold of
    one person talking. Cuts land `lead` s before the voice; no shot shorter than min_hold."""
    single = {}
    wide = [c for c, ppl in cameras.items() if not ppl or len(ppl) > 1]
    for c, ppl in cameras.items():
        if len(ppl) == 1:
            single.setdefault(ppl[0], c)
    def cam_for(p):
        return single.get(p) or next((c for c in wide if p in cameras[c]), None) or (wide[0] if wide else next(iter(cameras)))
    T = talk.shape[1]
    want = []
    for t in range(T):
        on = [names[k] for k in range(len(names)) if talk[k, t]]
        want.append(('wide', None) if len(on) > 1 else (('person', on[0]) if on else ('idle', None)))
    shots, cur, since, idle, run_p, run_t = [], None, 0, 0, None, 0
    first = next((cam_for(w[1]) for w in want if w[0] == 'person'), wide[0] if wide else next(iter(cameras)))
    cur, why = first, 'início'
    hold = int(min_hold / HOP)
    for t in range(T):
        kind, p = want[t]
        idle = idle + 1 if kind == 'idle' else 0
        if kind == 'person':
            run_t = run_t + 1 if p == run_p else 1
            run_p = p
        target, reason = cur, None
        if kind == 'person' and cam_for(p) != cur and (cur not in wide or not run_t < 2):
            target, reason = cam_for(p), f'fala: {p}'
        elif kind == 'wide' and wide and cur not in wide:
            target, reason = wide[0], 'falam juntos'
        elif kind == 'idle' and idle * HOP >= idle_wide and wide and cur not in wide:
            target, reason = wide[0], 'pausa'
        elif kind == 'person' and run_t * HOP >= max_hold and t - since >= hold:
            alt = wide[0] if wide else next((single[n] for n in names if n != p and n in single), None)
            if alt and alt != cur:
                target, reason, run_t = alt, f'respiro no monólogo de {p}', 0
        if target != cur and t - since >= hold:
            at = max(since + hold, t - int(lead / HOP)) if reason and reason.startswith('fala') else t
            shots.append((since * HOP, at * HOP, cur, why))
            cur, why, since = target, reason, at
            if reason.startswith('respiro'):
                # come back to the speaker after the cutaway
                back = min(T, at + int(cutaway / HOP))
                shots.append((at * HOP, back * HOP, cur, why))
                cur, why, since = cam_for(p), f'fala: {p}', back
    shots.append((since * HOP, dur, cur, why))
    out = []
    for a, b, c, w in shots:
        a, b = round(min(a, dur), 3), round(min(b, dur), 3)
        if b - a <= 1e-3:
            continue
        if out and out[-1][2] == c:
            out[-1] = (out[-1][0], b, c, out[-1][3])
        else:
            out.append((a, b, c, w))
    return out


def auto_cut(nle_xml, cameras, mics, out_xml=None, min_hold=2.0, max_hold=12.0, margin_db=6.0, name=None):
    """cameras: {"V1": ["Ana"], "V2": ["Robson"], "V3": []} (empty / several people = wide or two-shot);
    mics: {"Ana": "A3", "Robson": "A5"} — the audio track of each person's mic in this sequence."""
    tl = nle_read.read_fcp7(nle_xml)
    root, top, layers, ctx = nle_read.flat_timeline(nle_xml)
    vnames = _track_names(layers, 'video')
    bad = [c for c in cameras if c.upper() not in vnames] + [t for t in mics.values() if t.upper() not in _track_names(layers, 'audio')]
    if bad:
        return {'error': f'trilhas que não existem nesta sequência: {", ".join(bad)}',
                'video_tracks': list(vnames), 'audio_tracks': list(_track_names(layers, 'audio'))}
    cameras = {c.upper(): list(p or []) for c, p in cameras.items()}
    dur = tl['duration'] or max(i['tl_out'] for i in tl['items'])
    names, talk = who_speaks(activity(tl, mics, dur), margin_db=margin_db)
    shots = plan(names, talk, cameras, dur, min_hold=min_hold, max_hold=max_hold)
    cut = []
    for k, (a, b, cam, why) in enumerate(shots):
        tpl, its = layers['video'][vnames[cam]]
        for c, s, e in its:
            wa, wb = max(a, s), min(b, e)
            if wb - wa > 1e-3:
                cc = nle_read.trim_clip(c, s, e, wa, wb, f'cut{k}')
                for lk in list(cc.findall('link')):
                    cc.remove(lk)                                  # the cut is picture only; audio stays as synced
                cut.append((cc, wa, wb))
    for tpl, its in layers['video']:                                # camera tracks below, off — alternates at hand
        for c, _, _ in its:
            en = c.find('enabled')
            if en is None:
                en = ET.SubElement(c, 'enabled')
            en.text = 'FALSE'
    layers['video'].append((nle_read._blank_track(layers['video'][vnames[next(iter(cameras))]][0]), cut))
    seq_name = name or f'{tl["name"]} (corte automático)'
    out_xml = core.local_path(out_xml) if out_xml else re.sub(r'\.xml$', '', core.local_path(nle_xml), flags=re.I) + ' (corte automático).xml'
    res = nle_read.write_layers(root, top, layers, ctx, out_xml, seq_name, 'sequence-autocut', duration=dur)
    talk_s = {n: round(float(talk[k].sum() * HOP), 1) for k, n in enumerate(names)}
    per_cam = {}
    for a, b, c, _ in shots:
        per_cam[c] = round(per_cam.get(c, 0) + b - a, 1)
    res.update(shots=len(shots), seconds_per_camera=per_cam, seconds_talking=talk_s,
               cut_list=[{'from': round(a, 2), 'to': round(b, 2), 'camera': c, 'why': w} for a, b, c, w in shots[:400]],
               how_it_is_built=f'corte na V{sum(1 for _, i in layers["video"] if i)} (nova, em cima); as câmeras originais '
                               'ficam embaixo, desativadas: para trocar um plano, ative/corte a câmera que quiser')
    return res
