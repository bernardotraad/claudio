"""claudio.stereo — audio that plays on one side only (a lav or a single mic recorded on L or R) made to play on both.

  stereo_fix(nle_xml, out_xml)   reads the editor's timeline (FCP7 XML), measures L and R of every audio clip over the
                                 part that is used, and where one side is (near) silent makes both tracks of the clip
                                 play the live channel (dual mono). Two live channels (two mics) are left alone.
                                 normalize=True also brings every clip with speech to the same dialogue loudness
                                 (Audio Levels, never above +12 dB, keyframed levels untouched). Writes a NEW sequence.
"""
import copy, json, os, subprocess
import xml.etree.ElementTree as ET

import numpy as np

from claudio import core

SILENT_DB = -60.0       # a channel this quiet is empty
GAP_DB = 20.0           # …or this much quieter than the other one
SR = 16000
MAX_GAIN = 3.98107      # +12 dB, Premiere's Audio Levels ceiling
DIALOGUE_LUFS = -18.0   # per-clip speech target: lands a typical mix near −14 LUFS with music and ambience


def _db(x):
    return float(20 * np.log10(np.sqrt(np.mean(np.square(x))) + 1e-9)) if len(x) else -120.0


def channels(path, s, e):
    """(L, R) samples (16 kHz) of a source range, plus their RMS in dB. Cached per range."""
    lp = core.local_path(path)
    d = core.cache_dir(lp)
    key = f'{s:.2f}_{e:.2f}'
    cp = os.path.join(d, 'channels.json')
    cache = json.load(open(cp)) if os.path.exists(cp) else {}
    raw = subprocess.run(['ffmpeg', '-v', 'error', *core._probe_opts(lp), '-ss', f'{max(s, 0):.3f}', '-t', f'{max(e - s, 0.1):.3f}',
                          '-i', lp, '-vn', '-map', '0:a:0', '-ac', '2', '-ar', str(SR), '-f', 's16le', '-'],
                         capture_output=True).stdout
    x = np.frombuffer(raw, np.int16).reshape(-1, 2).astype(np.float32) / 32768.0 if raw else np.zeros((0, 2), np.float32)
    lv = cache.get(key) or [_db(x[:, 0]), _db(x[:, 1])]
    cache[key] = lv
    try:
        json.dump(cache, open(cp, 'w'))
    except OSError:
        pass
    return x, lv[0], lv[1]


def _rate(el):
    tb = int(el.findtext('rate/timebase') or 30)
    return tb * 1000 / 1001 if (el.findtext('rate/ntsc') or 'FALSE').upper() == 'TRUE' else float(tb)


def _levels_filter(c):
    for f in c.findall('filter'):
        if f.findtext('effect/name') == 'Audio Levels':
            for par in f.iter('parameter'):
                if par.findtext('parameterid') == 'level':
                    return par
    return None


def _set_gain(c, factor):
    """Multiply the clip's Audio Levels by factor (adds the filter when missing). False when levels are keyframed."""
    par = _levels_filter(c)
    if par is not None and par.findall('keyframe'):
        return False
    if par is None:
        f = ET.SubElement(c, 'filter')
        ef = ET.SubElement(f, 'effect')
        for k, v in (('name', 'Audio Levels'), ('effectid', 'audiolevels'), ('effectcategory', 'audiolevels'),
                     ('effecttype', 'audiolevels'), ('mediatype', 'audio')):
            ET.SubElement(ef, k).text = v
        par = ET.SubElement(ef, 'parameter', {'authoringApp': 'PremierePro'})
        for k, v in (('parameterid', 'level'), ('name', 'Level'), ('valuemin', '0'), ('valuemax', '3.98109'), ('value', '1')):
            ET.SubElement(par, k).text = v
    v = par.find('value')
    v.text = f'{min(MAX_GAIN, float(v.text or 1) * factor):.6g}'
    return True


def stereo_fix(nle_xml, out_xml=None, normalize=False, flatten=True, target_lufs=DIALOGUE_LUFS):
    from claudio import nle_read, qc
    src = core.local_path(nle_xml)
    out = core.local_path(out_xml) if out_xml else os.path.splitext(src)[0] + '_estereo.xml'
    work = src
    if flatten:                                   # the editor works without nests (see flatten_timeline)
        work = out + '.flat.xml'
        nle_read.flatten_fcp7(src, work, name=nle_read.read_fcp7(src)['name'])
    root = ET.parse(work).getroot()
    files = {f.get('id'): nle_read._path_from_url(f.findtext('pathurl')) for f in root.iter('file') if f.findtext('pathurl')}
    top = root.find('sequence') if root.find('sequence') is not None else root.find('.//sequence')
    F = _rate(top)
    # the clip items of one source clip on the timeline: one per channel (A1 = source ch 1, A2 = source ch 2)
    groups = {}
    for seq in root.iter('sequence'):
        for tr in seq.findall('media/audio/track'):
            for c in tr.findall('clipitem'):
                fe = c.find('file')
                if fe is None or fe.get('id') not in files or c.find('sourcetrack/trackindex') is None:
                    continue
                key = (id(seq), fe.get('id'), c.findtext('start'), c.findtext('end'), c.findtext('in'))
                groups.setdefault(key, []).append(c)
    fixed, normalized, two_mics, skipped = [], [], [], []
    for (_, fid, st, en, i0), items in groups.items():
        path = files[fid]
        if not os.path.exists(path):
            continue
        c0 = items[0]
        Fc = _rate(c0)
        s, e = int(c0.findtext('in') or 0) / Fc, int(c0.findtext('out') or 0) / Fc
        x, l, r = channels(path, s, e)
        name = c0.findtext('name')
        live = None
        if l > SILENT_DB and (r <= SILENT_DB or l - r >= GAP_DB):
            live = 1
        elif r > SILENT_DB and (l <= SILENT_DB or r - l >= GAP_DB):
            live = 2
        if live and len(items) >= 2:
            for c in items:
                c.find('sourcetrack/trackindex').text = str(live)
            fixed.append({'clip': name, 'tl': round(int(st) / F, 2), 'side': 'L' if live == 1 else 'R',
                          'L_db': round(l, 1), 'R_db': round(r, 1)})
        elif l > SILENT_DB and r > SILENT_DB:
            two_mics.append(name)
        if normalize and len(x):
            words = os.path.join(core.cache_dir(core.local_path(path)), 'words.json')
            ws = [w for w in json.load(open(words))] if os.path.exists(words) else []
            spoken = sum(min(w['e'], e) - max(w['s'], s) for w in ws if w['e'] > s and w['s'] < e)
            if spoken < 1.0:                          # no speech in this range: music, ambience, b-roll — untouched
                continue
            mono = x[:, live - 1] if live else x.mean(1)
            L = qc.loudness(mono).get('lufs')
            if L is None or L < -70:
                continue
            cur = 1.0
            par = _levels_filter(c0)
            if par is not None and par.find('value') is not None and not par.findall('keyframe'):
                cur = float(par.findtext('value') or 1)
            want = 10 ** ((target_lufs - L) / 20)
            factor = min(MAX_GAIN / max(cur, 1e-6), want / max(cur, 1e-6)) if cur > 1e-3 else 0
            if cur <= 1e-3:                           # the editor muted it: respect that
                skipped.append(f'{name} (mudo na timeline)')
                continue
            ok = all(_set_gain(c, factor * cur / max(cur, 1e-6)) for c in items)
            if ok:
                normalized.append({'clip': name, 'tl': round(int(st) / F, 2), 'measured_lufs': round(L, 1),
                                   'gain_db': round(float(20 * np.log10(min(MAX_GAIN, want))), 1),
                                   'capped': bool(want > MAX_GAIN)})
            else:
                skipped.append(f'{name} (volume com keyframes)')
    top.find('name').text = f'{top.findtext("name")} (estéreo)'
    ET.ElementTree(root).write(out, encoding='UTF-8', xml_declaration=True)
    if work != src:
        try:
            os.remove(work)
        except OSError:
            pass
    return {'path': out, 'nle_path': core.nle_path(out), 'sequence': top.findtext('name'),
            'made_dual_mono': fixed, 'two_live_channels_kept': sorted(set(two_mics)),
            'normalized': normalized, 'skipped': skipped, 'target_lufs': target_lufs if normalize else None}
