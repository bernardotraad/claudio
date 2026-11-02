"""Slow motion that holds up: the clips the editor slowed down in the NLE (speed under 100 %, constant) get their
in-between frames made by RIFE (optical-flow interpolation, github.com/hzwer/Practical-RIFE, MIT) instead of the
NLE's frame blending, as new media on a NEW video track right above, at 100 % and with the clip's own effects
(Motion, Lumetri, crop). The original clips stay under it: switch the new track off to compare.

  slowmo_timeline(nle_xml)                     every slowed clip → <out>.xml (+ claudio_slowmo/*.mov next to it)
  interpolate(path, src_in, n_in, factor, out)  one source range → ProRes 422 HQ, 10 bits (log footage stays log)

RIFE needs torch, which the Cláudio itself never loads: it runs in the GPU venv (claudio.gpu; claudio-doctor --fix-gpu).
Measured on the RTX 3060: 4K 2x ≈ 2.2 output frames/s (a 3 s shot at 50 % ≈ 80 s), 1080p ≈ 6/s.
A clip that already has the frames (60p at 40 % in a 24p sequence) is left to the NLE: nothing to invent.
"""
import copy, json, math, os, re, subprocess, time
import xml.etree.ElementTree as ET

from claudio import core, gpu, nle_read


def available():
    """None when RIFE can run, else what is missing."""
    return gpu.status()['rife']


def speed_of(c):
    """Constant speed of a clip item in % (100 when it has no Time Remap); None when it is variable or reversed."""
    for f in c.findall('filter'):
        e = f.find('effect')
        if e is None or e.findtext('effectid') != 'timeremap':
            continue
        sp, var, rev = 100.0, False, False
        for p in e.findall('parameter'):
            pid = p.findtext('parameterid')
            if pid == 'speed':
                sp = float(p.findtext('value') or 100)
                var = var or p.find('keyframe') is not None
            elif pid == 'variablespeed':
                var = var or (p.findtext('value') or '0') not in ('0', 'FALSE', 'false')
            elif pid == 'reverse':
                rev = (p.findtext('value') or 'FALSE').upper() == 'TRUE'
        return None if var or rev or sp < 0 else sp
    return 100.0


def _matrix(info):
    cs = (info.get('color_space') or '').lower()
    return 'bt601' if cs in ('bt470bg', 'smpte170m') else 'bt2020' if cs.startswith('bt2020') else 'bt709'


def _stream(path):
    j = json.loads(core.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                             'stream=width,height,color_space,color_primaries,color_transfer,color_range', '-of', 'json',
                             path]).stdout)['streams'][0]
    return j


def interpolate(path, src_in, n_in, factor, out_path, fps_out, n_out=None, scale=None):
    """Frames [src_in s, + n_in frames) of `path` made `factor` times longer by RIFE, written at fps_out as ProRes 422
    HQ 10-bit (the source's colour tags kept). The picture is not rotated (the NLE's own rotation/motion applies)."""
    miss = available()
    if miss:
        raise RuntimeError(miss)
    st = _stream(path)
    W, H = int(st['width']), int(st['height'])
    n_out = n_out or int(round(n_in * factor))
    m = _matrix(st)
    rng = 'pc' if st.get('color_range') == 'pc' else 'tv'
    tags = [x for k, flag in (('color_primaries', '-color_primaries'), ('color_transfer', '-color_trc'),
                              ('color_space', '-colorspace')) if st.get(k) and st[k] != 'unknown' for x in (flag, st[k])]
    dec = ['ffmpeg', '-v', 'error', '-noautorotate', '-ss', f'{src_in:.4f}', '-i', path, '-an', '-frames:v', str(n_in),
           '-vf', f'scale=in_color_matrix={m}:in_range={rng}:flags=accurate_rnd+full_chroma_int,format=rgb48le',
           '-f', 'rawvideo', '-pix_fmt', 'rgb48le', '-']
    wk = [gpu.python(), gpu.worker('rife_worker.py'), gpu.rife_dir(), str(W), str(H), repr(float(factor)), str(n_in)] + ([str(scale)] if scale else [])
    enc = ['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb48le', '-s', f'{W}x{H}', '-r', str(fps_out), '-i', '-',
           '-frames:v', str(n_out), '-vf', f'scale=out_color_matrix={m}:out_range={rng}:flags=accurate_rnd+full_chroma_int,format=yuv422p10le',
           '-c:v', 'prores_ks', '-profile:v', '3', '-vendor', 'apl0', '-color_range', rng, *tags, out_path]
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    t0 = time.time()
    p1 = subprocess.Popen(dec, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    p2 = subprocess.Popen(wk, stdin=p1.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    p1.stdout.close()
    p3 = subprocess.Popen(enc, stdin=p2.stdout, stderr=subprocess.PIPE)
    p2.stdout.close()
    e3 = p3.communicate()[1]
    e2 = p2.communicate()[1]
    e1 = p1.communicate()[1]
    for p, e, who in ((p2, e2, 'RIFE'), (p3, e3, 'ffmpeg (ProRes)'), (p1, e1, 'ffmpeg (leitura)')):   # the cause first
        if p.returncode not in (0, None) and not (p is not p3 and p3.returncode == 0):   # upstream of a finished encode: had enough
            raise RuntimeError(f'{who} falhou: ' + e.decode('utf-8', 'replace').strip()[-600:])
    return {'path': out_path, 'frames': n_out, 'seconds': round(time.time() - t0, 1)}


def _file_el(fid, path, n, W, H, rate_xml):
    E = core._esc
    return ET.fromstring(f'<file id="{fid}"><name>{E(os.path.basename(path))}</name><pathurl>{E(core.file_url(path))}</pathurl>'
                         f'{rate_xml}<duration>{n}</duration><media><video><samplecharacteristics>{rate_xml}<width>{W}</width>'
                         f'<height>{H}</height></samplecharacteristics></video></media></file>')


def plan(nle_xml, min_speed=5, max_speed=99.5):
    """The video clips slowed down in the sequence, with what RIFE has to make for each (no rendering)."""
    root, top, layers, ctx = nle_read.flat_timeline(nle_xml)
    F = nle_read._rate(top)
    out = []
    for tpl, items in layers.get('video', []):
        for c, s, e in items:
            sp = speed_of(c)
            if sp is None or not (min_speed <= sp <= max_speed):
                continue
            if (c.findtext('enabled') or 'TRUE').upper() == 'FALSE':
                continue
            fid = c.find('file').get('id')
            path = nle_read._path_from_url(ctx['files'][fid].findtext('pathurl'))
            if not os.path.exists(path) or not core.probe(path).get('has_video'):
                continue
            Fc = float(core.probe(path)['fps_float'])
            n_tl = int(round(e * F)) - int(round(s * F))
            i0, o0 = int(c.findtext('in') or 0), int(c.findtext('out') or 0)
            src_s = i0 / nle_read._rate(c)
            # source seconds the clip covers: in/out are source frames (Premiere); if they span the timeline length
            # instead, the speed says how much source is used
            used = (o0 - i0) / nle_read._rate(c)
            tl_dur = n_tl / F
            if abs(used / tl_dur - sp / 100) > 0.05 and abs(used / tl_dur - 1) < 0.05:
                used = tl_dur * sp / 100
            factor = F / (Fc * sp / 100)          # output frames per source frame
            item = {'clip': c, 'start': s, 'end': e, 'path': path, 'speed': sp, 'src_in': src_s, 'src_used': used,
                    'frames_tl': n_tl, 'factor': factor, 'fps_src': Fc}
            if factor <= 1.02:
                item['skip'] = f'já tem quadros ({Fc:g} fps a {sp:g} % numa sequência de {F:.3f})'
            out.append(item)
    return root, top, layers, ctx, F, out


def slowmo_timeline(nle_xml, out_xml=None, scale=None, dry_run=False):
    """A NEW sequence = the editor's, plus one video track on top with every slowed clip remade by RIFE."""
    root, top, layers, ctx, F, items = plan(nle_xml)
    tl_name = top.findtext('name') or 'sequência'
    out_xml = core.local_path(out_xml) if out_xml else re.sub(r'\.xml$', '', core.local_path(nle_xml), flags=re.I) + ' (câmera lenta).xml'
    todo = [i for i in items if not i.get('skip')]
    rep = [{'clip': os.path.basename(i['path']), 'tl': round(i['start'], 2), 'speed': i['speed'], 'factor': round(i['factor'], 2),
            **({'skip': i['skip']} if i.get('skip') else {})} for i in items]
    if not todo:
        return {'error': 'nenhum clipe em câmera lenta para refazer: deixe a velocidade do clipe abaixo de 100 % '
                         '(Ctrl+R) no Premiere e aperte de novo', 'clips': rep}
    if dry_run:
        return {'clips': rep, 'to_make': len(todo), 'frames': sum(i['frames_tl'] for i in todo)}
    miss = available()
    if miss:
        raise RuntimeError(miss)
    here = os.path.join(os.path.dirname(out_xml), 'claudio_slowmo')        # the media travels with the job
    os.makedirs(here, exist_ok=True)
    rate_xml = ET.tostring(top.find('rate'), encoding='unicode') if top.find('rate') is not None else \
        '<rate><timebase>30</timebase><ntsc>FALSE</ntsc></rate>'
    fps_out = F
    clips, made, spent = [], [], 0.0
    for k, it in enumerate(todo):
        n_in = min(int(math.ceil(it['frames_tl'] / it['factor'])) + 1, max(2, int(round(it['src_used'] * it['fps_src'])) + 1))
        stem = os.path.splitext(os.path.basename(it['path']))[0]
        dst = os.path.join(here, f'{stem}_{it["speed"]:g}pct_{int(round(it["src_in"] * it["fps_src"]))}.mov')
        if not (os.path.exists(dst) and abs(core.probe(dst).get('frames', 0) - it['frames_tl']) <= 1):
            r = interpolate(it['path'], it['src_in'], n_in, it['factor'], dst, f'{fps_out:.6f}', n_out=it['frames_tl'], scale=scale)
            spent += r['seconds']
        made.append(core.nle_path(dst))
        st = _stream(dst)
        cc = copy.deepcopy(it['clip'])
        cc.set('id', f'slowmo-{k}')
        for ch in list(cc):
            if ch.tag in ('link', 'file', 'rate', 'pproTicksIn', 'pproTicksOut', 'sourcetrack'):
                cc.remove(ch)
            elif ch.tag == 'filter' and ch.findtext('effect/effectid') == 'timeremap':
                cc.remove(ch)
        n = it['frames_tl']
        for tag, val in (('start', int(round(it['start'] * F))), ('end', int(round(it['start'] * F)) + n), ('in', 0), ('out', n), ('duration', n)):
            el = cc.find(tag)
            if el is None:
                el = ET.SubElement(cc, tag)
            el.text = str(val)
        cc.find('name').text = os.path.basename(dst)
        at = list(cc).index(cc.find('out')) + 1
        cc.insert(at, ET.fromstring(rate_xml))
        cc.insert(at + 1, _file_el(f'slowmo-file-{k}', core.local_path(dst), n, st['width'], st['height'], rate_xml))
        clips.append(cc)
    tracks = []                                   # clips on one track can't overlap: two slowed at once → two tracks
    for c in sorted(clips, key=lambda c: int(c.findtext('start'))):
        t = next((t for t in tracks if int(t[-1].findtext('end')) <= int(c.findtext('start'))), None)
        if t is None:
            tracks.append(t := [])
        t.append(c)
    raw = ['<track>' + ''.join(ET.tostring(c, encoding='unicode') for c in t) + '</track>' for t in tracks]
    name = f'{tl_name} (câmera lenta)'
    res = nle_read.write_layers(root, top, layers, ctx, out_xml, name, 'sequence-slowmo', extra_tracks={'video': raw})
    first = res['tracks_video'] + 1
    res.update(slowed=len(clips), media=made, slowmo_tracks=[f'V{first + k}' for k in range(len(raw))], clips=rep,
               render_s=round(spent, 1))
    return res
