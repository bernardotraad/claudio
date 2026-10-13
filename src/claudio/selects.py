"""claudio.selects — what an assistant editor hands over before anyone cuts: selects + markers, inside the NLE.

  build_selects(paths)   from indexed clips:
      • SELECTS — falas         best take of every repeated line (slate order when there is a slate, else recording
                                order), alternates named in the marker
      • SELECTS — falas únicas  long lines said once (not chatter, not criticised by the speaker)
      • SELECTS — b-roll        usable stretches of no-speech clips (focus/exposure/steady), labelled with what was seen
      • markers on every source clip: ✅ best / ✗ criticised / alternate takes, 👁 scene notes, b-roll windows
  export_selects_xml()   FCP7 XML: one bin per camera folder (master clips with markers) + the selects sequences.
                         Premiere: import the XML (bins + sequences). Resolve: one XML per sequence (it imports a
                         timeline per file) — written alongside.
Nothing is cut and nothing is rendered: the editor browses, reorders and decides.
"""
import os, uuid
from collections import Counter
from fractions import Fraction

from claudio import core


def _snap(idx, s, e):
    try:
        r = core.snap(idx, s, e, pad_in=0.25, pad_out=0.45, mode='speech')
        return r['in_sec'], r['out_sec']
    except Exception:
        return max(0.0, s - 0.25), min(idx['info']['duration'], e + 0.45)


def build_selects(paths, min_broll=2.0, single_min_words=8, include_single='auto'):
    talk, single, broll, markers = [], [], [], {}
    for p in paths:
        idx = core.build_index(p)
        info = idx['info']; P = info['path']
        mk = markers.setdefault(P, [])
        for l in core.read_log(idx):
            mk.append({'s': l['s'], 'e': l['e'], 'name': '👁 ' + l['text'][:60], 'comment': ', '.join(l.get('tags', [])), 'color': 'blue'})
        has_speech = bool(idx.get('speech')) and idx['speech']['n_words'] > max(8, info['duration'] / 6)
        sl = core.slate(idx) if idx.get('speech') else {'has_slate': False}
        if has_speech:
            tk = core.takes_report(idx)
            used = set()
            for g in tk['groups']:
                if g['attempts'] > 1:
                    for c in g['candidates']:
                        used.add((round(c['s'], 2), round(c['e'], 2)))
                if g['attempts'] > 1:
                    best = next(c for c in g['candidates'] if c['take'] == g['best_take'])
                    alts = [c for c in g['candidates'] if c is not best]
                    for c in g['candidates']:
                        tag = '✅ melhor' if c is best else ('✗ criticado' if c['self_critique_after'] else ('incompleto' if not c['complete'] else 'alternativa'))
                        mk.append({'s': c['s'], 'e': c['e'], 'name': f"{tag} · take {c['take']}/{g['attempts']}", 'comment': c['text'][:200],
                                   'color': 'green' if c is best else ('red' if c['self_critique_after'] else 'yellow')})
                    s, e = _snap(idx, best['s'], best['e'])
                    talk.append({'path': P, 'in': s, 'out': e, 'label': core.clean_text(best['text'])[:60],
                                 'order': ((sl.get('scene') or 0, sl.get('part') or 0, sl.get('take') or 0) if sl.get('has_slate') else (0, 0, 0),
                                           info.get('start_tc') or '', best['s']),
                                 'marker': f"✅ take {best['take']}/{g['attempts']} · alternativas: " +
                                           ', '.join(f"{a['take']} ({'criticado' if a['self_critique_after'] else 'cobertura ' + str(int(a['coverage'] * 100)) + '%'})" for a in alts)})
            # long lines said once — only from whoever is ON the mic (crew behind the camera sounds ≥ 8 dB quieter)
            import numpy as np
            U = core.utterances(core.performance_words(idx) if sl.get('has_slate') else core.load_words(idx))
            dbp = os.path.join(idx['cache'], 'rms_db.npy')
            db = np.load(dbp) if os.path.exists(dbp) else None
            def level(u):
                if db is None:
                    return 0.0
                v = db[int(u['s'] / core.HOP): int(u['e'] / core.HOP) + 1]
                return float(np.mean(v[v >= np.percentile(v, 40)])) if len(v) else -90.0
            lv = [level(u) for u in U if not u['chatter']]
            loudest = max(lv) if lv else 0.0
            for u in U:
                if u['chatter'] or u['self_critique_after'] or level(u) < loudest - 8:
                    continue
                flat = core.norm(u['text'])
                if sum(core.norm(w['w']) in core.CREW for w in u['words']) > 0.25 * len(u['words']):
                    continue
                if any(abs(u['s'] - a) < 0.3 for a, _ in used):
                    continue
                content = [w for w in u['words'] if core.norm(w['w']) not in core.CREW]
                if len(content) < single_min_words:
                    continue
                s, e = _snap(idx, u['s'], u['e'])
                single.append({'path': P, 'in': s, 'out': e, 'label': core.clean_text(u['text'])[:60],
                               'order': (info.get('start_tc') or '', u['s']),
                               'marker': 'fala única (verificar) · ' + core.clean_text(u['text'])[:180]})
        elif info.get('has_video'):
            b = core.broll_windows(idx, min_len=min_broll)
            log = core.read_log(idx)
            for w in sorted(b['windows'], key=lambda w: w['s'])[:6]:
                seen = next((l['text'] for l in log if l['s'] < w['e'] and l['e'] > w['s']), '')
                broll.append({'path': P, 'in': w['s'], 'out': w['e'], 'label': (seen or f"{w['camera']} · {info['name']}")[:60],
                              'order': (info.get('start_tc') or '', w['s']),
                              'marker': f"b-roll · {w['camera']} · nitidez {w['picture']['sharpness']}" + (f' · {seen}' if seen else '')})
                mk.append({'s': w['s'], 'e': w['e'], 'name': f"b-roll {w['camera']}", 'comment': seen, 'color': 'cyan'})
    # scripted shoot (many repeated lines) → one-off lines are mostly chatter: only on request
    if include_single == 'auto' and len(talk) >= 10:
        single = []
    elif include_single is False:
        single = []
    for lst in (talk, single, broll):
        lst.sort(key=lambda x: x['order'])
    return {'falas': talk, 'falas_unicas': single, 'broll': broll, 'markers': markers}


# ───────────────────────────── XML ─────────────────────────────
MARKER_COLORS = {'green': 'Green', 'red': 'Red', 'yellow': 'Yellow', 'blue': 'Blue', 'cyan': 'Cyan'}


def export_selects_xml(sel, out_path, gap_frames=12, name='Cláudio — selects'):
    paths = sorted({x['path'] for k in ('falas', 'falas_unicas', 'broll') for x in sel[k]} | set(sel['markers']))
    infos = {p: core.light_index(p)['info'] for p in paths}
    fps = Fraction(Counter(i['fps'] for i in infos.values() if i.get('fps')).most_common(1)[0][0])
    F = float(fps); rate = core._rate_xml(fps); E = core._esc
    W, H = Counter((i['width'], i['height']) for i in infos.values() if i.get('width')).most_common(1)[0][0]
    fid = {p: str(uuid.uuid4()).upper() for p in paths}
    declared = set()

    def file_el(p):
        if p in declared:
            return f'<file id="{fid[p]}"/>'
        declared.add(p)
        i = infos[p]
        ach = min(int(i.get('audio_channels') or 0), 2)
        tc = ''
        if i.get('start_tc'):
            clk = core.clock_of(i)
            tc = (f'<timecode>{core._rate_xml(i["fps"])}<string>{i["start_tc"]}</string><frame>{clk.start}</frame>'
                  f'<displayformat>{"DF" if clk.drop else "NDF"}</displayformat></timecode>')
        return (f'<file id="{fid[p]}"><name>{E(i["name"])}</name><pathurl>{E(core.file_url(p))}</pathurl>{core._rate_xml(i["fps"])}'
                f'<duration>{int(i["duration"] * float(Fraction(i["fps"])))}</duration>{tc}<media><video><samplecharacteristics>'
                f'{core._rate_xml(i["fps"])}<width>{i["width"]}</width><height>{i["height"]}</height></samplecharacteristics></video>'
                + (f'<audio><samplecharacteristics><depth>16</depth><samplerate>{i.get("audio_rate") or 48000}</samplerate>'
                   f'</samplecharacteristics><channelcount>{ach}</channelcount></audio>' if ach else '') + '</media></file>')

    def marker(m, base=0.0):
        return (f'<marker><name>{E(m["name"])}</name><comment>{E(m.get("comment", ""))}</comment>'
                f'<in>{int(round((m["s"] - base) * F))}</in><out>{int(round((m["e"] - base) * F))}</out>'
                f'<color>{MARKER_COLORS.get(m.get("color"), "Green")}</color></marker>')

    def motion(p):
        rot = core.get_rotation(p)
        if not rot:
            return ''
        i = infos[p]
        ow, oh = core.oriented(i)
        scale = min(W / ow, H / oh) * 100
        return (f'<filter><effect><name>Basic Motion</name><effectid>basic</effectid><effectcategory>motion</effectcategory>'
                f'<effecttype>motion</effecttype><mediatype>video</mediatype>'
                f'<parameter><parameterid>scale</parameterid><name>Scale</name><valuemin>0</valuemin><valuemax>1000</valuemax><value>{scale:.2f}</value></parameter>'
                f'<parameter><parameterid>rotation</parameterid><name>Rotation</name><valuemin>-8640</valuemin><valuemax>8640</valuemax><value>{rot}</value></parameter>'
                f'</effect></filter>')

    def sequence(title, items):
        v, a1, a2, rec = [], [], [], 0
        for k, it in enumerate(items):
            p = it['path']; i = infos[p]
            fin = int(round(it['in'] * F)); fout = max(fin + 1, int(round(it['out'] * F)))
            d = fout - fin
            dur = int(i['duration'] * F)
            common = (f'<name>{E(it["label"])}</name><duration>{dur}</duration>{rate}<start>{rec}</start><end>{rec + d}</end>'
                      f'<in>{fin}</in><out>{fout}</out>')
            mk = marker({'s': it['in'], 'e': it['out'], 'name': it['label'], 'comment': it.get('marker', ''), 'color': 'green'}, 0)
            ach = min(int(i.get('audio_channels') or 0), 2)
            cid = f'{title[:6]}-{k}'
            links = ''.join(f'<link><linkclipref>{cid}-{x}</linkclipref></link>' for x in ['v'] + [f'a{c}' for c in range(1, ach + 1)])
            v.append(f'<clipitem id="{cid}-v">{common}{file_el(p)}{motion(p)}{mk}{links}</clipitem>')
            for c, lst in ((1, a1), (2, a2)):
                if c <= ach:
                    lst.append(f'<clipitem id="{cid}-a{c}">{common}<file id="{fid[p]}"/><sourcetrack><mediatype>audio</mediatype>'
                               f'<trackindex>{c}</trackindex></sourcetrack>{links}</clipitem>')
            rec += d + gap_frames
        at = ''.join(f'<track>{"".join(l)}</track>' for l in (a1, a2) if l)
        return (f'<sequence id="{uuid.uuid4()}"><name>{E(title)}</name><duration>{max(rec, 1)}</duration>{rate}'
                f'<timecode>{rate}<string>01:00:00:00</string><frame>{round(F) * 3600}</frame><displayformat>NDF</displayformat></timecode>'
                f'<media><video><format><samplecharacteristics>{rate}<width>{W}</width><height>{H}</height>'
                f'<pixelaspectratio>square</pixelaspectratio></samplecharacteristics></format><track>{"".join(v)}</track></video>'
                f'<audio>{at}</audio></media></sequence>')

    seqs = [(t, k) for t, k in (('SELECTS — falas', 'falas'), ('SELECTS — falas únicas', 'falas_unicas'), ('SELECTS — b-roll', 'broll')) if sel[k]]
    seq_xml = [sequence(t, sel[k]) for t, k in seqs]
    # bins by folder with master clips carrying all markers
    by_dir = {}
    for p in paths:
        by_dir.setdefault(os.path.basename(os.path.dirname(p)) or 'clips', []).append(p)
    bins = ''
    for dname, ps in sorted(by_dir.items()):
        clips = ''
        for p in ps:
            i = infos[p]; dur = int(i['duration'] * F)
            mks = ''.join(marker(m) for m in sorted(sel['markers'].get(p, []), key=lambda m: m['s']))
            clips += (f'<clip id="mc-{fid[p]}"><name>{E(i["name"])}</name><duration>{dur}</duration>{rate}'
                      f'<media><video><track><clipitem id="mci-{fid[p]}"><name>{E(i["name"])}</name>{file_el(p)}</clipitem></track></video></media>'
                      f'{mks}</clip>')
        bins += f'<bin><name>{E(dname)}</name><children>{clips}</children></bin>'
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4"><project>'
           f'<name>{E(name)}</name><children>{"".join(seq_xml)}<bin><name>Cláudio — clipes com marcadores</name><children>{bins}</children></bin>'
           f'</children></project></xmeml>\n')
    open(out_path, 'w', encoding='utf-8').write(xml)
    # Resolve imports one timeline per file
    singles = []
    for (t, k), sx in zip(seqs, seq_xml):
        declared.clear()
        one = sequence(t, sel[k])
        sp = os.path.splitext(out_path)[0] + f' ({k}).xml'
        open(sp, 'w', encoding='utf-8').write('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4">' + one + '</xmeml>\n')
        singles.append(core.nle_path(sp))
    return {'premiere_xml': core.nle_path(out_path), 'resolve_xmls': singles,
            'sequences': {t: len(sel[k]) for t, k in seqs}, 'clips_with_markers': sum(1 for p in paths if sel['markers'].get(p)),
            'markers': sum(len(v) for v in sel['markers'].values()), 'sequence_format': f'{W}x{H} @ {fps}'}
