"""claudio.indexer — index a folder in its OWN process, independent of any chat/MCP session.

  python -m claudio.indexer <folder> [--vocabulary "INC Empreendimentos, ..."] [--language pt]

Started detached by the MCP tool index_folder (so closing the Premiere panel or ending a message does not stop it).
Progress lives in ~/.cache/claudio/jobs/<key>.json and on top of the folder's _CLAUDIO.md; index_status reads it.
One indexer per folder at a time (the status file holds the pid). Clips already indexed are skipped.
"""
import argparse, hashlib, json, os, sys, time, traceback

from claudio import core, sidecar

JOBS = os.path.join(core.CACHE, 'jobs')
STAGE_W = {'probe': (0.0, 0.02), 'audio': (0.02, 0.08), 'picture': (0.10, 0.65), 'speech': (0.75, 0.25)}
STAGE_PT = {'probe': 'lendo metadados', 'audio': 'áudio', 'picture': 'imagem', 'speech': 'transcrição'}


def job_path(folder):
    os.makedirs(JOBS, exist_ok=True)
    return os.path.join(JOBS, hashlib.sha1(core.local_path(folder).encode()).hexdigest()[:12] + '.json')


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, TypeError):
        return False


def read_job(folder):
    p = job_path(folder)
    if not os.path.exists(p):
        return None
    try:
        j = json.load(open(p))
    except Exception:
        return None
    if j.get('state') == 'running' and not alive(j.get('pid')):
        j['state'] = 'interrupted'
    return j


def all_jobs(max_age_h=24):
    out = []
    if os.path.isdir(JOBS):
        for f in os.listdir(JOBS):
            try:
                j = json.load(open(os.path.join(JOBS, f)))
            except Exception:
                continue
            if j.get('state') == 'running' and not alive(j.get('pid')):
                j['state'] = 'interrupted'
            if time.time() - j.get('updated', 0) < max_age_h * 3600:
                out.append(j)
    return sorted(out, key=lambda j: -j.get('updated', 0))


def progress_line(j):
    if not j:
        return 'nenhuma indexação'
    s = f"{int(j.get('fraction', 0) * 100)}% · {j.get('files_done', 0)}/{j.get('files_total', 0)} clipes"
    if j.get('state') == 'running' and j.get('current'):
        s += f" · agora: {j['current']} ({STAGE_PT.get(j.get('stage'), j.get('stage') or '')} {int((j.get('stage_progress') or 0) * 100)}%)"
    if j.get('eta_min') is not None and j.get('state') == 'running':
        s += f" · ~{j['eta_min']:.0f} min restantes ({j.get('speed_x')}× tempo real)"
    if j.get('state') != 'running':
        s += f" · {({'done': 'concluído', 'interrupted': 'interrompido', 'error': 'erro'}).get(j.get('state'), j.get('state'))}"
    return s


def launch_detached(folder, vocabulary=None, lang='pt'):
    """Start the indexer outside any chat. On WSL it is launched through a hidden Windows wsl.exe, so WSL stays up
    until indexing ends even if Claude Desktop / Premiere / the panel are closed (WSL shuts down when no Windows
    process is attached). Elsewhere a plain detached process."""
    import shlex, subprocess as sp
    folder = core.local_path(folder)
    os.makedirs(JOBS, exist_ok=True)
    args = [sys.executable, '-m', 'claudio.indexer', folder, '--language', lang] + (['--vocabulary', vocabulary] if vocabulary else [])
    log = os.path.join(JOBS, 'indexer.log')
    if core.IS_WSL:
        sh = os.path.join(JOBS, hashlib.sha1(folder.encode()).hexdigest()[:12] + '.sh')
        open(sh, 'w').write('#!/bin/bash\ncd /tmp\nexec ' + ' '.join(shlex.quote(a) for a in args) + f' >> {shlex.quote(log)} 2>&1 < /dev/null\n')
        os.chmod(sh, 0o755)
        distro = os.environ.get('WSL_DISTRO_NAME', 'Ubuntu')
        ps = (f"Start-Process -WindowStyle Hidden -FilePath 'C:\\Windows\\System32\\wsl.exe' "
              f"-ArgumentList '-d','{distro}','--','bash','{sh}'")
        r = sp.run(['powershell.exe', '-NoProfile', '-Command', ps], cwd='/mnt/c', capture_output=True, text=True)
        if r.returncode == 0:
            return 'wsl.exe'
    sp.Popen(args, stdout=open(log, 'a'), stderr=sp.STDOUT, stdin=sp.DEVNULL, start_new_session=True, close_fds=True)
    return 'detached'


def list_videos(folder):
    root = core.local_path(folder)
    out = []
    for dp, dn, fn in os.walk(root):
        dn[:] = [x for x in dn if x.lower() not in core.PROXY_DIRS and not x.startswith(('_claudio', '_decupa', '.', '#'))]
        out += [os.path.join(dp, f) for f in sorted(fn) if os.path.splitext(f)[1].lower() in core.VIDEO_EXT
                and not f.startswith('._') and not core.is_proxy(os.path.join(dp, f))]
    return out


def run(folder, vocabulary=None, lang='pt'):
    folder = core.local_path(folder)
    jp = job_path(folder)
    files = list_videos(folder)
    durs = {}
    for f in files:
        try:
            durs[f] = core.light_index(f)['info']['duration']
        except Exception:
            durs[f] = 0.0
    todo = [f for f in files if not os.path.exists(os.path.join(core.cache_dir(f), 'index.json')) or vocabulary]
    j = {'folder': core.nle_path(folder), 'pid': os.getpid(), 'state': 'running', 'started': time.time(), 'updated': time.time(),
         'files_total': len(todo), 'files_done': 0, 'total_s': sum(durs[f] for f in todo), 'done_s': 0.0, 'errors': [],
         'current': None, 'stage': None, 'stage_progress': 0, 'fraction': 0.0, 'eta_min': None, 'speed_x': None}
    last_md = [0.0]

    def save(force=False):
        j['updated'] = time.time()
        el = j['updated'] - j['started']
        cur_s = durs.get(j.get('_cur_path'), 0) * _frac(j)
        done = j['done_s'] + cur_s
        j['fraction'] = round(done / j['total_s'], 4) if j['total_s'] else 1.0
        if el > 10 and done > 0:
            j['speed_x'] = round(done / el, 2)
            j['eta_min'] = round((j['total_s'] - done) / (done / el) / 60, 1)
        tmp = jp + '.tmp'
        pub = {k: v for k, v in j.items() if not k.startswith('_')}
        pub['line'] = progress_line(j)
        json.dump(pub, open(tmp, 'w'), ensure_ascii=False)
        os.replace(tmp, jp)
        try:                      # next to the footage too: the Premiere panel (Windows/macOS) reads it directly
            pd = core.data_dir(folder)
            json.dump(pub, open(os.path.join(pd, 'progress.json.tmp'), 'w'), ensure_ascii=False)
            os.replace(os.path.join(pd, 'progress.json.tmp'), os.path.join(pd, 'progress.json'))
        except OSError:
            pass
        if force or time.time() - last_md[0] > 20:          # the folder index shows the same progress line
            last_md[0] = time.time()
            try:
                sidecar.write_folder_md(folder, progress=progress_line(j) if j['state'] == 'running' else None)
            except Exception:
                pass

    def _frac(jj):
        b, w = STAGE_W.get(jj.get('stage'), (0.0, 0.0))
        return min(1.0, b + w * (jj.get('stage_progress') or 0)) if jj.get('current') else 0.0

    save(True)
    for f in todo:
        j.update(current=os.path.basename(f), stage='probe', stage_progress=0, _cur_path=f)
        save()
        try:
            def prog(stage, fr):
                j.update(stage=stage, stage_progress=round(min(fr, 1), 3))
                if time.time() - j['updated'] > 2:
                    save()
            idx = core.build_index(f, lang, prog, False, vocabulary)
            try:
                sidecar.write_clip_md(idx)
            except Exception as e:
                j['errors'].append(f'{os.path.basename(f)} (.md): {e}')
        except Exception as e:
            j['errors'].append(f'{os.path.basename(f)}: {e}')
            traceback.print_exc()
        j['done_s'] += durs.get(f, 0)
        j['files_done'] += 1
        j.update(current=None, stage=None, stage_progress=0, _cur_path=None)
        save()
    j['state'] = 'done'
    save(True)
    try:
        sidecar.write_folder_md(folder)
    except Exception:
        pass
    return j


def cli():
    ap = argparse.ArgumentParser(description='Index every video in a folder (writes the .md next to each clip).')
    ap.add_argument('folder')
    ap.add_argument('--vocabulary')
    ap.add_argument('--language', default='pt')
    a = ap.parse_args()
    ex = read_job(a.folder)
    if ex and ex.get('state') == 'running':
        print('already running', ex.get('pid'))
        sys.exit(0)
    run(a.folder, a.vocabulary, a.language)


if __name__ == '__main__':
    cli()
