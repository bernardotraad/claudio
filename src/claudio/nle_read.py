"""claudio.nle_read — read the editor's own finished timelines to learn how they cut (read-only).

  premiere_sequences(prproj)          every sequence: duration, items per track
  premiere_timeline(prproj, name)     clip items of one sequence: track, timeline in/out, source clip and in/out
  edit_patterns(prproj, name)         what the editor does: spine shot lengths, b-roll over speech, captions, end card, music

A .prproj is gzip-compressed XML; objects reference each other by ObjectID / ObjectUID. We resolve
TrackItem → SubClip → Clip (source in/out) and MasterClip (name). Nested sequences show up by their sequence name.
"""
import gzip, os, statistics
import xml.etree.ElementTree as ET

from claudio import core

TICKS = 254016000000


def _load(prproj):
    p = core.local_path(prproj)
    raw = open(p, 'rb').read()
    try:
        raw = gzip.decompress(raw)
    except OSError:
        pass
    root = ET.fromstring(raw)
    byid, byuid = {}, {}
    for el in root:
        if el.get('ObjectID'):
            byid[el.get('ObjectID')] = el
        if el.get('ObjectUID'):
            byuid[el.get('ObjectUID')] = el
    return root, byid, byuid


def _ref(el, byid, byuid):
    if el is None:
        return None
    r = el.get('ObjectRef') or el.get('ObjectURef')
    return byid.get(r) if r in byid else byuid.get(r)


def _items(seq, byid, byuid):
    rows = []
    for tg in seq.iter('TrackGroup'):
        sec = tg.find('Second')
        grp = _ref(sec if sec is not None else tg, byid, byuid)
        if grp is None:
            continue
        kind = 'V' if 'Video' in grp.tag else 'A'
        for ti, tr in enumerate(grp.iter('Track')):
            track = _ref(tr, byid, byuid)
            if track is None:
                continue
            for it in track.iter('TrackItem'):
                item = _ref(it, byid, byuid)
                if item is None:
                    continue
                st, en = item.find('.//Start'), item.find('.//End')
                if st is None or en is None:
                    continue
                name, cin, cout = None, None, None
                sub = _ref(item.find('.//SubClip'), byid, byuid)
                if sub is not None:
                    clip = _ref(sub.find('.//Clip'), byid, byuid)
                    mc = _ref(sub.find('.//MasterClip'), byid, byuid)
                    nm = mc.find('.//Name') if mc is not None else None
                    name = nm.text if nm is not None else None
                    if clip is not None:
                        ip, op = clip.find('.//InPoint'), clip.find('.//OutPoint')
                        cin = int(ip.text) / TICKS if ip is not None else None
                        cout = int(op.text) / TICKS if op is not None else None
                rows.append({'track': f'{kind}{ti + 1}', 'start': round(int(st.text) / TICKS, 3), 'end': round(int(en.text) / TICKS, 3),
                             'clip': name, 'src_in': round(cin, 3) if cin is not None else None,
                             'src_out': round(cout, 3) if cout is not None else None})
    return sorted(rows, key=lambda r: (r['track'][0] != 'V', int(r['track'][1:]), r['start']))


def premiere_sequences(prproj):
    root, byid, byuid = _load(prproj)
    out = []
    for seq in [e for e in root if e.tag == 'Sequence']:
        nm = seq.find('Name')
        rows = _items(seq, byid, byuid)
        if rows:
            out.append({'name': nm.text if nm is not None else '', 'duration': max(r['end'] for r in rows), 'items': len(rows),
                        'tracks': sorted({r['track'] for r in rows})})
    return out


def premiere_timeline(prproj, name):
    root, byid, byuid = _load(prproj)
    for seq in [e for e in root if e.tag == 'Sequence']:
        nm = seq.find('Name')
        if nm is not None and nm.text == name:
            return _items(seq, byid, byuid)
    raise KeyError(f'sequence not found: {name}')


GRAPHIC = {'Graphic', 'SyntheticCaption', None}


def edit_patterns(prproj, name):
    """How this sequence was cut — numbers an editor would recognise."""
    rows = premiere_timeline(prproj, name)
    dur = max(r['end'] for r in rows)
    v = [r for r in rows if r['track'].startswith('V') and r['clip'] not in GRAPHIC]
    caps = [r for r in rows if r['clip'] in ('Graphic', 'SyntheticCaption')]
    v1 = [r for r in v if r['track'] == 'V1']
    upper = [r for r in v if r['track'] != 'V1']
    lens = [r['end'] - r['start'] for r in v1]
    ulens = [r['end'] - r['start'] for r in upper]
    covered = sum(ulens)
    audio = [r for r in rows if r['track'].startswith('A') and r['clip'] not in GRAPHIC]
    music = sorted({r['clip'] for r in audio if r['clip'] and os.path.splitext(r['clip'])[1].lower() in ('.wav', '.mp3', '.aif', '.aiff', '.m4a')})
    last = max(v1, key=lambda r: r['end']) if v1 else None
    cap_lens = [r['end'] - r['start'] for r in caps if r['track'].startswith('V')]
    return {
        'sequence': name, 'duration_s': round(dur, 2),
        'spine_V1': {'shots': len(v1), 'median_len_s': round(statistics.median(lens), 2) if lens else None,
                     'clips': [r['clip'] for r in v1]},
        'layers_above': {'shots': len(upper), 'median_len_s': round(statistics.median(ulens), 2) if ulens else None,
                         'coverage_pct_of_duration': round(100 * covered / dur, 1) if dur else 0,
                         'clips': [r['clip'] for r in upper]},
        'captions': {'blocks': len(cap_lens), 'median_len_s': round(statistics.median(cap_lens), 2) if cap_lens else None},
        'ends_with': last['clip'] if last else None,
        'music': music,
        'starts_at_s': round(min(r['start'] for r in rows), 2),
    }


# ───────────────────────────── the timeline as it is NOW in the NLE (FCP7 XML exported by Premiere or Resolve) ─────
import re as _re
import urllib.parse as _up
from fractions import Fraction as _Fr


def _path_from_url(u):
    u = _up.unquote(u or '')
    u = _re.sub(r'^file://(localhost)?', '', u)
    if _re.match(r'^/[A-Za-z]:', u):
        u = u[1:]
    return core.local_path(u)


def read_fcp7(xml_path):
    """Parse an FCP7 XML timeline (Premiere export_as_fcp_xml / Resolve export_timeline FCP_7_XML): clip items per
    track with timeline/source frames and the media path, plus every marker (sequence and clip markers = notes)."""
    root = ET.parse(core.local_path(xml_path)).getroot()
    seq = root.find('.//sequence')
    tb = int(seq.findtext('rate/timebase') or 30)
    ntsc = (seq.findtext('rate/ntsc') or 'FALSE').upper() == 'TRUE'
    fps = _Fr(tb * 1000, 1001) if ntsc else _Fr(tb)
    F = float(fps)
    files = {}
    for f in root.iter('file'):
        if f.get('id') and f.findtext('pathurl'):
            files[f.get('id')] = _path_from_url(f.findtext('pathurl'))
    items, markers = [], []
    for kind in ('video', 'audio'):
        for ti, tr in enumerate(seq.findall(f'media/{kind}/track')):
            for c in tr.findall('clipitem'):
                fe = c.find('file')
                path = files.get(fe.get('id')) if fe is not None else None
                st, en = int(c.findtext('start') or -1), int(c.findtext('end') or -1)
                fi, fo = int(c.findtext('in') or 0), int(c.findtext('out') or 0)
                if st < 0 or en < 0:          # transitions report -1: skip
                    continue
                enabled = (c.findtext('enabled') or 'TRUE').upper() != 'FALSE'
                items.append({'track': f'{kind[0].upper()}{ti + 1}', 'name': c.findtext('name'), 'path': path,
                              'tl_in_f': st, 'tl_out_f': en, 'src_in_f': fi, 'src_out_f': fo, 'enabled': enabled,
                              'tl_in': st / F, 'tl_out': en / F, 'src_in': fi / F, 'src_out': fo / F})
                for m in c.findall('marker'):
                    mi = int(m.findtext('in') or 0)
                    markers.append({'where': 'clip', 'track': f'{kind[0].upper()}{ti + 1}', 'clip': c.findtext('name'),
                                    'tl': round((st + mi - fi) / F, 3), 'name': m.findtext('name'), 'comment': m.findtext('comment')})
    for m in seq.findall('marker'):
        mi = int(m.findtext('in') or 0)
        markers.append({'where': 'sequence', 'tl': round(mi / F, 3), 'name': m.findtext('name'), 'comment': m.findtext('comment')})
    return {'name': seq.findtext('name'), 'fps': f'{fps.numerator}/{fps.denominator}', 'duration': round(int(seq.findtext('duration') or 0) / F, 3),
            'items': items, 'markers': sorted(markers, key=lambda m: m['tl'])}


def diff_with_edit(tl, fit):
    """What the editor changed in the NLE vs the version Cláudio delivered: per shot kept / trimmed / moved / removed,
    clips the editor added, and the notes (markers) with the shot each one falls on."""
    mine = [{**s, 'layer': 'V1'} for s in fit['shots']] + [{**o, 'layer': 'V2'} for o in fit.get('overlays', [])]
    vids = [i for i in tl['items'] if i['track'].startswith('V') and i['path']]
    used, rows = set(), []
    for s in mine:
        sp = core.local_path(s['path'])
        cands = [(k, i) for k, i in enumerate(vids) if k not in used and i['path'] == sp
                 and min(i['src_out'], s['src_out']) - max(i['src_in'], s['src_in']) > 0.1]
        if not cands:
            rows.append({'shot': s['n'], 'layer': s['layer'], 'label': s.get('label'), 'status': 'removido'})
            continue
        k, i = max(cands, key=lambda ki: min(ki[1]['src_out'], s['src_out']) - max(ki[1]['src_in'], s['src_in']))
        used.add(k)
        ch = []
        if abs(i['src_in'] - s['src_in']) > 0.02: ch.append(f"entrada {i['src_in'] - s['src_in']:+.2f}s")
        if abs(i['src_out'] - s['src_out']) > 0.02: ch.append(f"saída {i['src_out'] - s['src_out']:+.2f}s")
        if abs(i['tl_in'] - s['tl_in']) > 0.02: ch.append(f"movido {i['tl_in'] - s['tl_in']:+.2f}s na timeline")
        if i['track'] != s['layer']: ch.append(f"trilha {s['layer']}→{i['track']}")
        if not i['enabled']: ch.append('desativado')
        rows.append({'shot': s['n'], 'layer': s['layer'], 'label': s.get('label'), 'status': 'alterado' if ch else 'igual', 'changes': ch,
                     'now': {k2: (round(i[k2], 3) if isinstance(i[k2], float) else i[k2]) for k2 in ('track', 'tl_in', 'tl_out', 'src_in', 'src_out')}})
    added = [{'track': i['track'], 'clip': i['name'], 'tl': f"{i['tl_in']:.2f}-{i['tl_out']:.2f}", 'src': f"{i['src_in']:.2f}-{i['src_out']:.2f}"}
             for k, i in enumerate(vids) if k not in used]
    notes = []
    for m in tl['markers']:
        on = next((r for r in rows if r.get('now') and r['now']['tl_in'] <= m['tl'] < r['now']['tl_out'] and r['layer'] == 'V2'), None) \
             or next((r for r in rows if r.get('now') and r['now']['tl_in'] <= m['tl'] < r['now']['tl_out']), None)
        notes.append({**m, 'on_shot': on['shot'] if on else None, 'shot_label': on['label'] if on else None})
    return {'timeline': tl['name'], 'duration_nle': tl['duration'], 'duration_delivered': fit['duration'],
            'shots': rows, 'added_by_editor': added, 'notes': notes,
            'summary': {k: sum(1 for r in rows if r['status'] == k) for k in ('igual', 'alterado', 'removido')} | {'adicionados': len(added), 'notas': len(notes)}}


def edit_from_timeline(tl, base_fit):
    """Turn the NLE timeline back into an edit spec that reproduces it exactly (V1 spine + V2 runs, no beat re-fitting),
    keeping Cláudio's settings (aspect, music, captions) — so the editor's manual changes become the new base."""
    vids = sorted([i for i in tl['items'] if i['path'] and i['enabled']], key=lambda i: i['tl_in_f'])
    v1 = [i for i in vids if i['track'] == 'V1']
    up = [i for i in vids if i['track'] != 'V1' and i['track'].startswith('V')]
    by_path = {core.local_path(s['path']): s for s in base_fit['shots'] + base_fit.get('overlays', [])}
    def keep(i, extra):
        prev = by_path.get(i['path'], {})
        return {'path': i['path'], 'in': i['src_in'], 'out': i['src_out'], 'len': round(i['tl_out'] - i['tl_in'], 4),
                'reframe_x': prev.get('reframe_x', 0.5), 'reframe_y': prev.get('reframe_y', 0.5), 'zoom': prev.get('zoom', 1.0),
                'grade': prev.get('grade'), 'label': prev.get('label') or i['name'], 'anchor': 'start', **extra}
    shots = [keep(i, {'audio': by_path.get(i['path'], {}).get('audio', 'dialogue'), 'keep': False}) for i in v1]
    runs, cur = [], None
    for i in up:
        if cur and abs(i['tl_in'] - cur['to']) < 0.02:
            cur['clips'].append(keep(i, {})); cur['to'] = i['tl_out']
        else:
            cur = {'from': i['tl_in'], 'to': i['tl_out'], 'clips': [keep(i, {})]}; runs.append(cur)
    edit = {k: base_fit[k] for k in ('aspect', 'fps', 'captions', 'texts', 'ambient_db', 'fade_in', 'fade_out', 'resolution') if k in base_fit}
    if base_fit.get('music'):
        edit['music'] = {k: v for k, v in base_fit['music'].items() if k != 'used_end'}
    edit.update(name=(tl['name'] or 'timeline') + ' (do NLE)', cut_on='none', shots=shots, overlays=runs, adopted_from_nle=True)
    return edit
