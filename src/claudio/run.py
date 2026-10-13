"""claudio-run — the deterministic jobs, straight from the Premiere panel, with NO AI model in the loop (no credits).

  claudio-run <action> '<json args>'      prints one JSON object

  status      {"folder"}                           indexing progress line(s)
  index       {"folder", "vocabulary"?}            start indexing in the background
  stereo      {"nle_xml", "normalize"?}            one-sided audio → both sides (+ speech levelled)
  flatten     {"nle_xml"}                          nested sequences → clips straight on the tracks
  qc          {"nle_xml", "platform"?}             timeline + audio QC (loudness, ducking)
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
    return {'xml': multicam.export_xml(res, out, labels=a.get('labels')), **multicam.summary(res)}


ACTIONS = {'status': _status, 'index': _index, 'stereo': _stereo, 'flatten': _flatten, 'qc': _qc, 'sync': _sync}


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
