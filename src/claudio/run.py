"""claudio-run — the deterministic jobs, straight from the Premiere panel, with NO AI model in the loop (no credits).

  claudio-run <action> '<json args>'      prints one JSON object

  status      {"folder"}                           indexing progress line(s)
  index       {"folder", "vocabulary"?}            start indexing in the background
  stereo      {"nle_xml", "normalize"?}            one-sided audio → both sides (+ speech levelled)
  flatten     {"nle_xml"}                          nested sequences → clips straight on the tracks
  qc          {"nle_xml", "platform"?}             timeline + audio QC (loudness, ducking)
  describe    {"folder" | "paths"}                what is seen in each stretch → clip logs (no AI)
  multicam_plan {"folder"}                        cameras/recorders from the folders + colours (no AI)
  sync        {"cameras", "external_audio"?, "out_xml"?, "labels"?}   multicam sync (PluralEyes-style)
  clean       {"nle_xml", "max_pause"?, "fillers"?}  pauses + hesitations out of an edited sequence (new sequence)
  sfx         {"nle_xml", "whoosh"?, "pops"?, "sfx_dir"?}  whoosh into b-roll, pop on caption keywords (new audio track)
  mogrt       {"nle_xml", "style", "srt"?, "job"?}    caption MOGRTs: template (After Effects) + placement plan
  mogrt_styles {"job"?}                            the styles on offer (+ the client's brand one) and the .srt found
  stt         {"set"?: "elevenlabs" | "whisper"}   transcription engine (Scribe on/off), saved in settings.json
  stt_check   {"paths", "prproj"?}                 per clip: indexed, speech, engine, duration (+ sequences with captions)
  retranscribe {"path"}                            redo ONE clip's transcript with the chosen engine (picture kept)
  profile     {}                                   the editor's folders (perfil.json over the defaults)
  recipes     {"job"?}                             the recipes (+ the job's client's usual one) for the panel's buttons
  bench       {"receita"?}                         the local benchmark (~/.config/claudio/benchmark.json), checked without AI

Paths can be Windows paths (D:\\...) — they are translated on WSL — and come back as the editor's paths.
"""
import json, os, re, sys, traceback


def _status(a):
    from claudio import indexer
    jobs = [indexer.read_job(a['folder'])] if a.get('folder') else indexer.all_jobs()
    return {'jobs': [{'folder': j['folder'], 'line': indexer.progress_line(j), 'state': j['state']} for j in jobs if j]}


def client_for(folder):
    """The client profile whose name appears in the job's path ("…\\2026-10-19 ACME 9\\brutos" → ACME)."""
    from claudio import clients, core
    import re as _re
    path = core.local_path(folder).lower()
    try:
        names = [os.path.splitext(f)[0] for f in os.listdir(clients.clients_dir()) if f.endswith('.md') and f != 'README.md']
    except OSError:
        return None
    for n in sorted(names, key=len, reverse=True):
        if _re.search(r'(^|[^a-z0-9])' + _re.escape(n.lower()) + r'([^a-z0-9]|$)', path):
            return clients.load(n)
    return None


def _index(a):
    from claudio import core, indexer, motion
    voc = a.get('vocabulary')
    prof = client_for(a['folder'])
    applied = {}
    if prof:                                       # the client's profile: vocabulary, and footage shot sideways
        applied['client'] = prof.get('name')
        if prof.get('vocabulary'):
            voc = ', '.join(x for x in [voc, prof['vocabulary']] if x)
            applied['vocabulary'] = True
        if prof.get('rotation'):
            vids = indexer.list_videos(a['folder'])
            for f in vids:
                if not core.get_rotation(f):
                    core.set_rotation(f, int(prof['rotation']))
            applied['rotation'] = int(prof['rotation'])
    learned = motion.unapplied_vocabulary(a['folder'])
    if learned:
        voc = ', '.join(([voc] if voc else []) + motion.folder_vocabulary(a['folder']))
        motion.mark_vocabulary_applied(a['folder'])
    j = indexer.read_job(a['folder'])
    if j and j.get('state') == 'running':
        return {'started': False, 'line': indexer.progress_line(j), 'profile': applied}
    indexer.launch_detached(a['folder'], voc, a.get('language', 'pt'))
    return {'started': True, 'files': len(indexer.list_media(a['folder'])), 'profile': applied}


def _stereo(a):
    from claudio import stereo
    return stereo.stereo_fix(a['nle_xml'], a.get('out_xml'), normalize=bool(a.get('normalize')))


def _flatten(a):
    from claudio import core, nle_read
    src = core.local_path(a['nle_xml'])
    return nle_read.flatten_fcp7(src, a.get('out_xml') and core.local_path(a['out_xml']) or os.path.splitext(src)[0] + '_sem_aninhadas.xml')


def _qc(a):
    from claudio import qc
    r = qc.qc_timeline(a['nle_xml'], a.get('platform'))
    r['audio'] = qc.audio_qc(a['nle_xml'], a.get('platform'))
    return r


def _sync(a):
    import os as _os
    from claudio import core, multicam
    res = multicam.sync_cameras(a['cameras'], a.get('external_audio'), a.get('fps'), a.get('correct_drift', True),
                                music=bool(a.get('music')))
    base = _os.path.dirname(_os.path.commonpath([n['path'] for n in res['nodes']]))
    out = core.local_path(a['out_xml']) if a.get('out_xml') else _os.path.join(base, 'claudio_sync.xml')
    _os.makedirs(_os.path.dirname(out), exist_ok=True)
    x = multicam.export_xml(res, out, labels=a.get('labels'))
    summ = multicam.summary(res)
    sj = _os.path.splitext(out)[0] + '.json'                    # for multicam_monitor (the visual check)
    json.dump({'fps': res['fps'], 'drift': res['drift'], 'clips': [{**r, 'path': n['path']} for r, n in
               zip(summ['clips'], sorted(res['nodes'], key=lambda n: (n['cam'], n.get('t0') if n.get('t0') is not None else 1e12)))]},
              open(sj, 'w'), ensure_ascii=False, indent=1)
    return {'xml': x, 'sync_json': core.nle_path(sj), **summ}


def _multicam_plan(a):
    """Cameras and recorders of a job from its folders, without AI: every folder with videos is a camera (named after
    the folder + the camera model), folders with only audio files are external recorders. Colours: the multicam palette."""
    from claudio import core, multicam, profile
    root = core.local_path(a['folder'])
    work = profile.work_dirs()
    skip = lambda n: n.startswith('_') or profile.skip_dir(n, work) or n.lower() in core.PROXY_DIRS
    cams, ext = {}, []
    for dp, dn, fn in os.walk(root):
        dn[:] = sorted(x for x in dn if not skip(x))
        if os.path.relpath(dp, root).count(os.sep) > 2:
            dn[:] = []
            continue
        vids = [f for f in fn if os.path.splitext(f)[1].lower() in core.VIDEO_EXT and not f.startswith('._')]
        auds = [f for f in fn if os.path.splitext(f)[1].lower() in multicam.AUDIO_EXT and not f.startswith('._')]
        if vids:
            model = ''
            try:
                model = core.camera_of(os.path.join(dp, vids[0])) or ''
            except Exception:
                pass
            name = os.path.basename(dp) if dp != root else 'CAM'
            cams[name + (f' — {model}' if model and model.lower() not in name.lower() else '')] = {
                'folder': core.nle_path(dp), 'clips': len(vids), 'model': model}
        elif auds:
            ext.append({'folder': core.nle_path(dp), 'files': len(auds)})
    # one short external audio file and takes no longer than it: a music video with its guide track
    files = [os.path.join(core.local_path(e['folder']), f) for e in ext for f in os.listdir(core.local_path(e['folder']))
             if os.path.splitext(f)[1].lower() in multicam.AUDIO_EXT]
    music = False
    if len(files) == 1:
        try:
            g = core.probe(files[0])['duration']
            music = 60 <= g <= 15 * 60
        except Exception:
            pass
    labels = multicam.camera_labels(list(cams) + (['AUDIO'] if ext else []))
    for k in cams:
        cams[k]['label'] = labels[k]
    return {'cameras': cams, 'external_audio': ext, 'audio_label': labels.get('AUDIO'), 'music': music,
            'guide': core.nle_path(files[0]) if music else None,
            'ready': len(cams) + (1 if ext else 0) >= 2,
            'note': None if len(cams) + (1 if ext else 0) >= 2 else 'precisa de pelo menos 2 fontes (câmeras e/ou gravador)'}


def _describe(a):
    from claudio import core, describe, indexer
    paths = [core.local_path(p) for p in a.get('paths') or []] or indexer.list_videos(a['folder'])
    res = describe.describe(paths)
    return {'clips': len(res), 'stretches': sum(r.get('stretches', 0) for r in res),
            'errors': [f"{r['clip']}: {r['error']}" for r in res if r.get('error')], 'results': res[:40]}


def _out_xml(a, suffix):
    """Where a new sequence from the panel goes: next to the job's project, never inside the backups folder
    (the panel keeps only the last 30 files there), named after the sequence."""
    from claudio import core, nle_read, profile
    if a.get('out_xml'):
        return core.local_path(a['out_xml'])
    src = core.local_path(a['nle_xml'])
    d = os.path.dirname(src)
    if os.path.basename(d).lower() == profile.get('pastas.backups').lower():
        d = os.path.dirname(d)
    name = nle_read.read_fcp7(src)['name'] or os.path.splitext(os.path.basename(src))[0]
    import re as _re
    return os.path.join(d, _re.sub(r'[\\/:*?"<>|]', '-', f'{name} ({suffix})') + '.xml')


def _clean(a):
    from claudio import cleanup
    mp = a.get('max_pause')
    return cleanup.clean_speech(a['nle_xml'], _out_xml(a, 'limpo'), float(mp) if mp else None, bool(a.get('fillers', True)),
                                a.get('extra_fillers') or (), pace=a.get('pace', 'apertado'), notes=bool(a.get('notes', True)),
                                crossfade=bool(a.get('crossfade', True)), zoom=bool(a.get('zoom', False)), punch=a.get('punch'))


def _sfx(a):
    from claudio import sfx
    prof = client_for(a.get('job') or a['nle_xml']) or {}
    return sfx.add_sfx(a['nle_xml'], _out_xml(a, 'com SFX'), bool(a.get('whoosh', True)), bool(a.get('pops', True)),
                       a.get('sfx_dir') or prof.get('sfx_dir'), extra_keywords=(prof.get('caption_look') or {}).get('emphasis_words') or ())


def _from_premiere(a):
    """The caption as the editor left it in Premiere (track C1 of the active sequence, read from the saved .prproj),
    written as <sequence>_revisada.srt in the job's captions folder. None when the sequence has no caption track."""
    from claudio import core, motion, nle_read, profile
    if not (a.get('prproj') and a.get('sequence')):
        return None, []
    try:
        cues = nle_read.premiere_captions(a['prproj'], a['sequence'], a.get('track', 'C1'))
    except Exception:
        return None, []
    if not cues:
        return None, []
    job = a.get('job') or profile.job_of(a['prproj'])
    name = re.sub(r'[\\/:*?"<>|]', '-', a['sequence'])
    p = nle_read.write_srt(cues, os.path.join(profile.folder('legendas', job), f'{name}_revisada.srt'))
    learned = []
    if a.get('nle_xml'):
        try:
            learned = motion.learn_caption_edits(a['nle_xml'], cues)
        except Exception:
            pass
    return p, learned


def _srt_in(job, a=None):
    """The caption to use: the caption track the editor fixed in Premiere, else the newest *revisada*.srt in the
    job (captions folder first), else the newest .srt there."""
    from claudio import core, profile
    p, _ = _from_premiere(a or {})
    if p:
        return p
    if not job:
        return None
    best = []
    skip = {n.lower() for n in profile.raw_names()} | {'proxy', 'proxies'}
    for dp, dn, fn in os.walk(core.local_path(job)):
        dn[:] = [d for d in dn if not d.startswith(('_', '.')) and d.lower() not in skip]
        if os.path.relpath(dp, core.local_path(job)).count(os.sep) > 2:
            dn[:] = []
        for f in fn:
            if f.lower().endswith('.srt'):
                p = os.path.join(dp, f)
                best.append(('revisad' in f.lower(), os.path.getmtime(p), p))
    return max(best)[2] if best else None


def _mogrt_styles(a):
    from claudio import core, motion
    prof = client_for(a.get('job') or '') or {}
    styles = list(motion.STYLES) if prof.get('caption_look') else [s for s in motion.STYLES if s != 'marca']
    srt = _srt_in(a.get('job'), a)
    return {'styles': styles, 'client': prof.get('name'), 'recommended': 'marca' if prof.get('caption_look') else None,
            'srt': core.nle_path(srt) if srt else None}


def _mogrt(a):
    from claudio import core, mogrt, profile
    prof = client_for(a.get('job') or a['nle_xml']) or {}
    srt, learned = a.get('srt'), []
    if srt == 'auto':
        srt, learned = _from_premiere(a)
        srt = srt or _srt_in(a.get('job'))
    out = a.get('out_dir') or (profile.folder('legendas', a['job']) if a.get('job') else None)
    if out:
        os.makedirs(core.local_path(out), exist_ok=True)
    r = mogrt.caption_mogrt(a['nle_xml'], a.get('style', 'social'), a.get('aspect'), a.get('accent', '#4ea1ff'), srt,
                            a.get('corrected'), a.get('fix'), prof.get('caption_look'), a.get('position', 'bottom'), out)
    return {**r, 'learned': learned} if learned else r


def _stt(a):
    from claudio import core
    if a.get('set') in ('elevenlabs', 'whisper'):
        core.set_setting('stt', a['set'])
    return {'stt': core.stt_engine(), 'chosen': core.env('STT', 'whisper'), 'has_key': bool(core.elevenlabs_key())}


def _stt_check(a):
    """What switching engines would redo: each clip's engine and length; songs and unindexed clips are left out."""
    import json as _json
    from claudio import core, nle_read
    clips = {}
    for p in a.get('paths') or []:
        try:
            lp = core.local_path(p)
            ip = os.path.join(core.cache_dir(lp), 'index.json')
            idx = _json.load(open(ip)) if os.path.exists(ip) else None
        except Exception:
            idx = None
        sp = (idx or {}).get('speech')
        clips[p] = {'indexed': bool(idx), 'speech': bool(sp), 'lyrics': bool(sp and sp.get('lyrics')),
                    'engine': (sp or {}).get('engine', 'whisper') if sp else None,
                    'duration': (idx or {}).get('info', {}).get('duration', 0)}
    caps = {}
    if a.get('prproj'):
        try:
            caps = nle_read.caption_sequences(a['prproj'])
        except Exception:
            pass
    return {'stt': core.stt_engine(), 'clips': clips, 'captioned': caps}


def _retranscribe(a):
    from claudio import core
    idx = core.build_index(a['path'], a.get('language', 'pt'), stt=core.stt_engine())
    sp = idx.get('speech') or {}
    return {'engine': sp.get('engine'), 'stt_error': sp.get('stt_error'), 'words': sp.get('n_words', 0)}


def _profile(a):
    from claudio import profile
    return profile.summary()


def _recipes(a):
    from claudio import recipes
    prof = client_for(a['job']) if a.get('job') else None
    return {'receitas': recipes.listing(), 'cliente': (prof or {}).get('receita')}


def _bench(a):
    from claudio import bench
    return bench.run(a.get('receita'))


ACTIONS = {'bench': _bench, 'recipes': _recipes, 'profile': _profile, 'stt': _stt, 'stt_check': _stt_check, 'retranscribe': _retranscribe, 'clean': _clean, 'sfx': _sfx, 'mogrt': _mogrt, 'mogrt_styles': _mogrt_styles, 'describe': _describe, 'multicam_plan': _multicam_plan, 'status': _status, 'index': _index, 'stereo': _stereo, 'flatten': _flatten, 'qc': _qc, 'sync': _sync}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ACTIONS:
        print(json.dumps({'error': 'usage: claudio-run <' + '|'.join(ACTIONS) + '> \'{json}\''}))
        sys.exit(2)
    try:
        raw = sys.argv[2] if len(sys.argv) > 2 else '{}'
        if raw.startswith('b64:'):                     # from the panel: no quoting rules between Windows and WSL
            import base64
            raw = base64.b64decode(raw[4:]).decode('utf-8')
        args = json.loads(raw)
        out = ACTIONS[sys.argv[1]](args)
        print(json.dumps({'ok': True, **out}, ensure_ascii=False, default=str))
    except Exception as e:
        print(json.dumps({'ok': False, 'error': f'{type(e).__name__}: {e}', 'trace': traceback.format_exc()[-1500:]}, ensure_ascii=False))
        sys.exit(1)


if __name__ == '__main__':
    main()
