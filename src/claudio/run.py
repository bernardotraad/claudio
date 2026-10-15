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

Paths can be Windows paths (D:\\...) — they are translated on WSL — and come back as the editor's paths.
"""
import json, os, sys, traceback


def _status(a):
    from claudio import indexer
    jobs = [indexer.read_job(a['folder'])] if a.get('folder') else indexer.all_jobs()
    return {'jobs': [{'folder': j['folder'], 'line': indexer.progress_line(j), 'state': j['state']} for j in jobs if j]}


def _index(a):
    from claudio import indexer, motion
    voc = a.get('vocabulary')
    learned = motion.unapplied_vocabulary(a['folder'])
    if learned:
        voc = ', '.join(([voc] if voc else []) + motion.folder_vocabulary(a['folder']))
        motion.mark_vocabulary_applied(a['folder'])
    j = indexer.read_job(a['folder'])
    if j and j.get('state') == 'running':
        return {'started': False, 'line': indexer.progress_line(j)}
    indexer.launch_detached(a['folder'], voc, a.get('language', 'pt'))
    return {'started': True, 'files': len(indexer.list_videos(a['folder']))}


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
    res = multicam.sync_cameras(a['cameras'], a.get('external_audio'), a.get('fps'), a.get('correct_drift', True))
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
    from claudio import core, multicam
    root = core.local_path(a['folder'])
    skip = lambda n: n.startswith(('#', '_', '.')) or n.lower() in core.PROXY_DIRS
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
    labels = multicam.camera_labels(list(cams) + (['AUDIO'] if ext else []))
    for k in cams:
        cams[k]['label'] = labels[k]
    return {'cameras': cams, 'external_audio': ext, 'audio_label': labels.get('AUDIO'),
            'ready': len(cams) + (1 if ext else 0) >= 2,
            'note': None if len(cams) + (1 if ext else 0) >= 2 else 'precisa de pelo menos 2 fontes (câmeras e/ou gravador)'}


def _describe(a):
    from claudio import core, describe, indexer
    paths = [core.local_path(p) for p in a.get('paths') or []] or indexer.list_videos(a['folder'])
    res = describe.describe(paths)
    return {'clips': len(res), 'stretches': sum(r.get('stretches', 0) for r in res),
            'errors': [f"{r['clip']}: {r['error']}" for r in res if r.get('error')], 'results': res[:40]}


ACTIONS = {'describe': _describe, 'multicam_plan': _multicam_plan, 'status': _status, 'index': _index, 'stereo': _stereo, 'flatten': _flatten, 'qc': _qc, 'sync': _sync}


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
