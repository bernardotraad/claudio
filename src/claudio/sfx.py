"""Sound design for social cuts: a whoosh into every b-roll run and a light pop when a caption keyword lands — on a
NEW audio track of a copy of the sequence, quiet (−20 / −24 dB), easy to mute or delete. Deterministic, no AI.
Sounds: the editor's own (sfx_dir with whoosh*.wav / pop*.wav, or the client profile's "sfx"), else generated
here once (filtered-noise whoosh, sine pop) and cached."""
import os, re, wave

import numpy as np

from claudio import core, nle_read

SR = 48000
LEVEL = {'whoosh': -20.0, 'pop': -24.0}
PEAK_AT = {'whoosh': 0.72, 'pop': 0.0}          # where the sound "hits", as a fraction of its length → put there on the cut


def _write(path, x):
    with wave.open(path, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())


def builtin(kind):
    """The generated default sounds (cached): whoosh = band-passed noise sweeping up, swelling to the cut and tailing off;
    pop = a short pitched-down sine blip."""
    d = os.path.join(core.env('CACHE') or os.path.expanduser('~/.cache/claudio'), 'sfx')
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f'{kind}.wav')
    if os.path.exists(p):
        return p
    rng = np.random.default_rng(7)
    if kind == 'whoosh':
        n = int(0.55 * SR); t = np.arange(n) / SR
        noise = rng.standard_normal(n)
        f = 300 + 3200 * (t / t[-1]) ** 2                   # sweep up
        y = np.zeros(n); lp = 0.0; bp = 0.0
        for i in range(n):                                    # state-variable band-pass following the sweep
            g = 2 * np.sin(np.pi * f[i] / SR)
            lp += g * bp; hp = noise[i] - lp - 0.6 * bp; bp += g * hp
            y[i] = bp
        env = np.where(t < PEAK_AT['whoosh'] * t[-1], (t / (PEAK_AT['whoosh'] * t[-1])) ** 2,
                       np.exp(-(t - PEAK_AT['whoosh'] * t[-1]) * 18))
        x = y * env
    else:
        n = int(0.09 * SR); t = np.arange(n) / SR
        f = 900 * np.exp(-t * 18) + 380
        x = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 45) * np.minimum(1, t / 0.002)
    x = x / (np.abs(x).max() + 1e-9) * 0.89
    _write(p + '.part', x.astype(np.float32))
    os.replace(p + '.part', p)
    return p


def library(sfx_dir=None):
    """{kind: [paths]}: the editor's sounds when given, the built-in ones for what is missing."""
    lib = {'whoosh': [], 'pop': []}
    if sfx_dir and os.path.isdir(core.local_path(sfx_dir)):
        d = core.local_path(sfx_dir)
        for f in sorted(os.listdir(d)):
            for k in lib:
                if f.lower().startswith(k) and f.lower().endswith(('.wav', '.aif', '.aiff', '.mp3')):
                    lib[k].append(os.path.join(d, f))
    for k in lib:
        if not lib[k]:
            lib[k] = [builtin(k)]
    return lib


GRAPHIC_EXT = ('.aegraphic', '.prgraphic', '.mogrt', '.png', '.jpg', '.jpeg', '.psd', '.ai', '.svg', '.tif', '.tiff', '.gif')


def _is_broll(i, dur):
    """Camera footage over the spine — not a caption/title (MOGRT), a still, or an overlay laid over the whole cut."""
    p = (i['path'] or '').lower()
    if not p or p.endswith(GRAPHIC_EXT):
        return False
    return not (i['tl_in'] < 0.1 and i['tl_out'] - i['tl_in'] > 0.8 * dur)


def broll_entries(tl, bridge=0.5):
    """Timeline times where a b-roll run starts: footage on V2+ after at least `bridge` s with no b-roll on screen
    (back-to-back b-roll clips are one run, one whoosh)."""
    dur = tl['duration'] or max((i['tl_out'] for i in tl['items']), default=0)
    its = sorted([i for i in tl['items'] if i['track'].startswith('V') and i['track'] != 'V1' and i['enabled'] and _is_broll(i, dur)],
                 key=lambda i: i['tl_in'])
    out, covered_to = [], None
    for i in its:
        if (covered_to is None or i['tl_in'] - covered_to > bridge) and i['tl_in'] > 0.3:
            out.append(round(i['tl_in'], 3))
        covered_to = max(covered_to or 0, i['tl_out'])
    return out


def keyword_hits(nle_xml, words=None, extra=()):
    """Times of caption keywords (price, numbers, brand… — the same ones the dynamic captions pop)."""
    from claudio import motion
    W = words if words is not None else motion.corrected_timeline_words(nle_xml, remember=False)['words']
    return [round(w['s'], 3) for w in motion.mark_emphasis(W, extra) if w.get('em')]


def _space(ts, gap):
    out = []
    for t in sorted(ts):
        if not out or t - out[-1] >= gap:
            out.append(t)
    return out


def _info(path):
    with wave.open(path) as w:
        return w.getnframes() / w.getframerate(), w.getnchannels(), w.getframerate()


def _track(events, F, rate_xml, start_id):
    E = core._esc
    files, items = {}, []
    for k, (t, kind, path) in enumerate(events):
        dur, ch, sr = _info(path) if path.lower().endswith('.wav') else (core.probe(path)['duration'], 1, SR)
        n = max(1, int(round(dur * F)))
        st = max(0, int(round((t - PEAK_AT[kind] * dur) * F)))
        if path not in files:
            files[path] = f'sfx-file-{start_id + len(files)}'
            fe = (f'<file id="{files[path]}"><name>{E(os.path.basename(path))}</name><pathurl>{E(core.file_url(path))}</pathurl>'
                  f'{rate_xml}<duration>{n}</duration><media><audio><samplecharacteristics><depth>16</depth><samplerate>{sr}</samplerate>'
                  f'</samplecharacteristics><channelcount>{ch}</channelcount></audio></media></file>')
        else:
            fe = f'<file id="{files[path]}"/>'
        g = 10 ** (LEVEL[kind] / 20)
        items.append((st, f'<clipitem id="sfx-{k}"><name>{kind}</name><enabled>TRUE</enabled><duration>{n}</duration>{rate_xml}'
                          f'<start>{st}</start><end>{st + n}</end><in>0</in><out>{n}</out>{fe}'
                          f'<sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack>'
                          f'<filter><effect><name>Audio Levels</name><effectid>audiolevels</effectid><effectcategory>audiolevels</effectcategory>'
                          f'<effecttype>audiolevels</effecttype><mediatype>audio</mediatype><parameter><parameterid>level</parameterid>'
                          f'<name>Level</name><valuemin>0</valuemin><valuemax>3.98109</valuemax><value>{g:.5f}</value></parameter>'
                          f'</effect></filter><labels><label2>Caribbean</label2></labels></clipitem>'))
    # clips on one track cannot overlap: a later one starts after the earlier ends
    out, last_end = [], -1
    for st, x in sorted(items):
        e = int(re.search(r'<end>(\d+)</end>', x).group(1))
        if st < last_end:
            continue
        out.append(x); last_end = e
    return f'<track>{"".join(out)}</track>', len(out)


def add_sfx(nle_xml, out_xml=None, whoosh=True, pops=True, sfx_dir=None, words=None, extra_keywords=(), min_gap=0.6):
    tl = nle_read.read_fcp7(nle_xml)
    out_xml = core.local_path(out_xml) if out_xml else re.sub(r'\.xml$', '', core.local_path(nle_xml), flags=re.I) + ' (com SFX).xml'
    lib = library(sfx_dir)
    here = os.path.join(os.path.dirname(out_xml), 'claudio_sfx')        # the sounds travel with the job, not the WSL cache
    os.makedirs(here, exist_ok=True)
    for k, ps in lib.items():
        for n, p in enumerate(ps):
            dst = os.path.join(here, os.path.basename(p))
            if not os.path.exists(dst) or os.path.getsize(dst) != os.path.getsize(p):
                import shutil
                shutil.copyfile(p, dst)
            ps[n] = dst
    ev = []
    if whoosh:
        for k, t in enumerate(_space(broll_entries(tl), min_gap)):
            ev.append((t, 'whoosh', lib['whoosh'][k % len(lib['whoosh'])]))
    if pops:
        busy = [t for t, *_ in ev]
        hits = [t for t in _space(keyword_hits(nle_xml, words, extra_keywords), 1.2) if all(abs(t - b) > 0.35 for b in busy)]
        for k, t in enumerate(hits):
            ev.append((t, 'pop', lib['pop'][k % len(lib['pop'])]))
    if not ev:
        return {'error': 'nada para sonorizar: sem b-roll acima da V1 e sem palavras-chave nas falas'}
    root, top, layers, ctx = nle_read.flat_timeline(nle_xml)
    F = nle_read._rate(top)
    rate_xml = ET_rate(top)
    track, n = _track(sorted(ev), F, rate_xml, len(ctx['files']) + 1)
    name = f'{tl["name"]} (com SFX)'
    res = nle_read.write_layers(root, top, layers, ctx, out_xml, name, 'sequence-sfx', duration=tl['duration'] or None,
                                extra_tracks={'audio': [track]})
    res.update(whooshes=sum(1 for e in ev if e[1] == 'whoosh'), pops=sum(1 for e in ev if e[1] == 'pop'), placed=n,
               sfx_track=f'A{res["tracks_audio"] + 1}', sounds={k: [core.nle_path(p) for p in v] for k, v in lib.items()},
               levels_db=LEVEL)
    return res


def ET_rate(top):
    import xml.etree.ElementTree as ET
    r = top.find('rate')
    return ET.tostring(r, encoding='unicode') if r is not None else '<rate><timebase>30</timebase><ntsc>FALSE</ntsc></rate>'
