"""claudio.nle_read — read the editor's own finished timelines to learn how they cut (read-only).

  premiere_sequences(prproj)          every sequence: duration, items per track
  premiere_timeline(prproj, name)     clip items of one sequence: track, timeline in/out, source clip and in/out
  edit_patterns(prproj, name)         what the editor does: spine shot lengths, b-roll over speech, captions, end card, music

A .prproj is gzip-compressed XML; objects reference each other by ObjectID / ObjectUID. We resolve
TrackItem → SubClip → Clip (source in/out) and MasterClip (name). Nested sequences show up by their sequence name.
"""
import copy, gzip, os, statistics
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


def _gain(c):
    """Linear gain of a clip's Audio Levels (Premiere); keyframed levels → their mean. 1.0 when there is none."""
    g = 1.0
    for f in c.findall('filter'):
        if f.findtext('effect/name') != 'Audio Levels':
            continue
        for par in f.iter('parameter'):
            if par.findtext('parameterid') != 'level':
                continue
            ks = [float(k.findtext('value')) for k in par.findall('keyframe') if k.findtext('value')]
            if ks:
                g *= sum(ks) / len(ks)
            elif par.findtext('value'):
                g *= float(par.findtext('value'))
    return g


def read_fcp7(xml_path):
    """Parse an FCP7 XML timeline (Premiere export_as_fcp_xml / Resolve export_timeline FCP_7_XML): clip items per
    track with timeline/source frames and the media path, plus every marker (sequence and clip markers = notes).
    Nested sequences are read through (their clips, where they play), so captions and notes work on nested edits.
    Source in/out use each clip's own rate (a 59.94 clip in a 23.976 sequence)."""
    root, top, layers, ctx = flat_timeline(xml_path)
    F0 = _rate(top)
    fps = _Fr(round(F0 * 1001 / 1000) * 1000, 1001) if abs(F0 - round(F0)) > 1e-6 else _Fr(int(round(F0)))
    F = float(fps)
    items, markers = [], []
    for kind in ('video', 'audio'):
        n = 0
        for tpl, its in layers.get(kind, []):
            if not its:
                continue
            n += 1
            tname = f'{kind[0].upper()}{n}'
            for c, s, e in sorted(its, key=lambda x: x[1]):
                fe = c.find('file')
                path = _path_from_url(ctx['files'][fe.get('id')].findtext('pathurl'))
                Fc = _rate(c)
                fi, fo = int(c.findtext('in') or 0), int(c.findtext('out') or 0)
                st, en = int(round(s * F)), int(round(e * F))
                enabled = (c.findtext('enabled') or 'TRUE').upper() != 'FALSE'
                items.append({'track': tname, 'name': c.findtext('name'), 'path': path,
                              'tl_in_f': st, 'tl_out_f': en, 'src_in_f': fi, 'src_out_f': fo, 'enabled': enabled,
                              'tl_in': s, 'tl_out': e, 'src_in': fi / Fc, 'src_out': fo / Fc,
                              **({'gain': _gain(c), 'channel': int(c.findtext('sourcetrack/trackindex') or 1)} if kind == 'audio' else {})})
                for m in c.findall('marker'):
                    mi = int(m.findtext('in') or 0)
                    markers.append({'where': 'clip', 'track': tname, 'clip': c.findtext('name'),
                                    'tl': round(s + (mi - fi) / Fc, 3), 'name': m.findtext('name'), 'comment': m.findtext('comment')})
    for m in top.findall('marker'):
        mi = int(m.findtext('in') or 0)
        markers.append({'where': 'sequence', 'tl': round(mi / F, 3), 'name': m.findtext('name'), 'comment': m.findtext('comment')})
    return {'name': top.findtext('name'), 'fps': f'{fps.numerator}/{fps.denominator}', 'duration': round(int(top.findtext('duration') or 0) / F, 3),
            'items': items, 'markers': sorted(markers, key=lambda m: m['tl']), 'nested': sorted(n_ for _, n_ in ctx['nests']),
            'not_read': ctx['dropped']}


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


# ───────────────────────────── nested sequences → clips straight on the timeline ─────────────────────────────
TICKS = 254016000000            # Premiere ticks per second (pproTicksIn/Out)


def _rate(el):
    """fps of a sequence/clipitem from its <rate>."""
    tb = int(el.findtext('rate/timebase') or 30)
    return tb * 1000 / 1001 if (el.findtext('rate/ntsc') or 'FALSE').upper() == 'TRUE' else float(tb)


def _blank_track(tr):
    t = copy.deepcopy(tr)
    for ch in list(t):
        if ch.tag in ('clipitem', 'transitionitem', 'generatoritem'):
            t.remove(ch)
    return t


def _expand(seq, lo, hi, shift, seqs, ctx, inst=''):
    """Clip items of `seq` inside [lo, hi) (seconds of `seq`), moved by `shift` seconds, with every nested sequence
    replaced by its own clips. Returns {kind: [layer, ...]}, a layer being (track template, [(clipitem copy, start s,
    end s)]). One parent track that holds nests becomes as many tracks as the deepest nest needs."""
    F = _rate(seq)
    out = {}
    for kind in ('video', 'audio'):
        layers = []
        for tr in seq.findall(f'media/{kind}/track'):
            mine = [(_blank_track(tr), [])]
            for c in tr.findall('clipitem'):
                st, en = int(c.findtext('start') or -1), int(c.findtext('end') or -1)
                if st < 0 or en <= st:
                    continue
                a, b = st / F, en / F
                wa, wb = max(a, lo), min(b, hi)
                if wb - wa <= 1e-6:
                    continue
                nest = c.find('sequence')
                if nest is not None:
                    sid = nest.get('id')
                    inner = seqs.get(sid)
                    if inner is None:
                        ctx['dropped'].append(f'{c.findtext("name")} (sequência aninhada sem conteúdo no XML)')
                        continue
                    key = (inst, sid, st, en, c.findtext('in'))
                    if kind == 'audio' and key in ctx['audio_done']:
                        continue                      # the same nest on A1 and A2 is one stereo nest: expand once
                    if kind == 'audio':
                        ctx['audio_done'].add(key)
                    ctx['nests'].add((sid, inner.findtext('name')))
                    cin = int(c.findtext('in') or 0) / _rate(c)
                    sub = _expand(inner, cin + (wa - a), cin + (wb - a), shift + (a - cin), seqs, ctx, inst=f'{inst}{sid}@{st}.')
                    sl = sub.get(kind, [])
                    while len(mine) < len(sl):
                        mine.append((sl[len(mine)][0], []))
                    for k, (tpl, its) in enumerate(sl):
                        mine[k][1].extend(its)
                    continue
                fe = c.find('file')
                fid = fe.get('id') if fe is not None else None
                full = ctx['files'].get(fid)
                if full is None or full.findtext('pathurl') is None:
                    ctx['dropped'].append(f'{c.findtext("name")} ({kind}, {a + shift:.2f}–{b + shift:.2f} s: '
                                          + ('gerado no Premiere, ex.: adjustment layer' if fid else 'sem mídia') + ')')
                    continue
                cc = copy.deepcopy(c)
                i0, o0 = int(c.findtext('in') or 0), int(c.findtext('out') or 0)
                fa, fb = (wa - a) / (b - a), (wb - a) / (b - a)
                cc.find('in').text = str(int(round(i0 + fa * (o0 - i0))))
                cc.find('out').text = str(int(round(i0 + fb * (o0 - i0))))
                ti, to = cc.find('pproTicksIn'), cc.find('pproTicksOut')
                if ti is not None and to is not None and ti.text and to.text:
                    t0, t1 = int(ti.text), int(to.text)
                    ti.text, to.text = str(int(round(t0 + fa * (t1 - t0)))), str(int(round(t0 + fb * (t1 - t0))))
                if inst:                                    # ids stay unique when one nest is used twice
                    cc.set('id', f'{c.get("id")}-{abs(hash(inst)) % 10 ** 8}')
                    for lk in cc.findall('link/linkclipref'):
                        lk.text = f'{lk.text}-{abs(hash(inst)) % 10 ** 8}'
                mine[0][1].append((cc, wa + shift, wb + shift))
            layers.extend(mine)
        out[kind] = layers
    return out


def flat_timeline(xml_path):
    """The top sequence of an FCP7 XML with every nested sequence expanded into its clips (any depth), each clip
    trimmed to the part of the nest that is used and placed where it plays. Speed, effects, levels, labels and links
    of each clip are kept. Items with no media file (adjustment layers, generated slugs) can't round-trip through XML
    and are listed in `dropped`."""
    root = ET.parse(core.local_path(xml_path)).getroot()
    top = root.find('sequence') if root.find('sequence') is not None else root.find('.//sequence')
    seqs = {s.get('id'): s for s in root.iter('sequence') if s.get('id') and s.find('media') is not None}
    files = {}
    for f in root.iter('file'):
        if f.get('id') and (f.get('id') not in files or (files[f.get('id')].find('pathurl') is None and f.find('pathurl') is not None)):
            if len(f) or f.get('id') not in files:
                files[f.get('id')] = f
    ctx = {'files': files, 'dropped': [], 'nests': set(), 'audio_done': set()}
    dur = int(top.findtext('duration') or 0) / _rate(top)
    layers = _expand(top, 0.0, max(dur, 1e9), 0.0, seqs, ctx)
    return root, top, layers, ctx


def flatten_fcp7(xml_path, out_path, name=None):
    """Write a NEW sequence with the clips of every nested sequence straight on its tracks (see flat_timeline): the
    editor can grade take by take under the adjustment layer without opening nests."""
    root, top, layers, ctx = flat_timeline(xml_path)
    F = _rate(top)
    seq = copy.deepcopy(top)
    seq.set('id', 'sequence-flat')
    seq.find('name').text = name or f'{top.findtext("name")} (sem aninhadas)'
    seen, ids = set(), set()
    for kind in ('video', 'audio'):
        holder = seq.find(f'media/{kind}')
        if holder is None:
            continue
        for tr in holder.findall('track'):
            holder.remove(tr)
        for tpl, items in layers.get(kind, []):
            if not items:
                continue
            t = copy.deepcopy(tpl)
            for cc, s, e in sorted(items, key=lambda x: x[1]):
                cc.find('start').text = str(int(round(s * F)))
                cc.find('end').text = str(int(round(e * F)))
                fe = cc.find('file')
                fid = fe.get('id')
                if fid not in seen:                          # the first use of a file carries its full description
                    cc.remove(fe)
                    full = copy.deepcopy(ctx['files'][fid])
                    cc.insert(list(cc).index(cc.find('out')) + 1 if cc.find('pproTicksOut') is None
                              else list(cc).index(cc.find('pproTicksOut')) + 1, full)
                    seen.add(fid)
                elif len(fe):
                    for ch in list(fe):
                        fe.remove(ch)
                ids.add(cc.get('id'))
                t.append(cc)
            holder.append(t)
    for c in seq.iter('clipitem'):                           # links to clips that were trimmed away
        for lk in list(c.findall('link')):
            if lk.findtext('linkclipref') not in ids:
                c.remove(lk)
    proj = ET.Element('xmeml', {'version': root.get('version', '4')})
    proj.append(seq)
    out_path = core.local_path(out_path)
    ET.ElementTree(proj).write(out_path, encoding='UTF-8', xml_declaration=True)
    n = {k: sum(len(its) for _, its in layers.get(k, [])) for k in ('video', 'audio')}
    return {'path': out_path, 'nle_path': core.nle_path(out_path), 'sequence': seq.findtext('name'),
            'nests_expanded': sorted(n_ for _, n_ in ctx['nests']), 'clips_video': n['video'], 'clips_audio': n['audio'],
            'tracks_video': sum(1 for _, i in layers.get('video', []) if i), 'tracks_audio': sum(1 for _, i in layers.get('audio', []) if i),
            'dropped': ctx['dropped']}
