"""claudio.share — one analysis for every machine that sees the footage: the desktop indexes, the Mac reads it over SMB
instead of indexing again (transcript, thumbnails, picture, faces, voices, descriptions, caption fixes).

  publish(path)        copy the clip's cache next to the footage, <folder>/_claudio/analise/<clip>-<key>/ (only files newer
                       than the copy there). On when settings.json says {"share_analysis": "1"} (it writes into the footage
                       folders, so it is opt-in)
  adopt(path, local)   a clip with no local cache: take the shared copy (called by core.cache_dir), paths rewritten for
                       this machine
  refresh(path)        a clip already adopted: bring in what the other machine redid since (newer files win)
  fetch_missing(folder, files)   clips with no analysis here nor next to the footage (an old job, a folder copied
                       without _claudio): wake the machine that has it (Home Assistant, as for the local model), find the
                       same job there by its folder name and bring only the analysis over SSH (Tailscale) into this copy's
                       _claudio/analise — as if it had been copied — then adopt it. Settings: "share_from"
                       ("bernardo@100.72.145.75", a Windows OpenSSH server) and "share_roots" ("E:\\Fotos;D:\\…")
  push_back(changed)   the way back: what the Mac redid (descriptions by the desktop's vision model, Scribe…) goes into
                       the desktop's own job folder over the same SSH, so its next refresh takes it. Only when the
                       desktop already answers (never wakes it); what could not go waits in <cache>/share_pending.json

The local cache is keyed by the clip's path, which differs between machines (/mnt/e/… on the desktop, /Volumes/… on the
Mac); the shared copy is keyed by name + size and checked against the modification time (±2 s: SMB and NTFS round it
differently), so a different take with the same name is never mistaken for this one.
"""
import base64, hashlib, json, os, re, shutil, subprocess, tarfile, tempfile, time, unicodedata, uuid

from claudio import core

SHARED = 'analise'
IDENT = 'ident.json'
NO_COPY = {'proxy.json', IDENT}                      # the proxy is found again on each machine (its path is local)


def enabled():
    """Opt-in on the machine that indexes ({"share_analysis": "1"}); on the one that brings analysis in ("share_from") it
    is on unless "share_analysis" says otherwise — publishing there only writes into its own copy of the job."""
    v = core.env('SHARE_ANALYSIS')
    if v is None:
        return bool(core.env('SHARE_FROM'))
    return str(v).lower() in ('1', 'true', 'sim', 'yes')


def shared_dir(path, create=False):
    st = os.stat(path)
    name = os.path.basename(path)
    key = hashlib.sha1(f'{name}|{st.st_size}'.encode()).hexdigest()[:16]
    base = core.data_dir(os.path.dirname(path), create=create)
    return os.path.join(base, SHARED, f'{re.sub(r"[^A-Za-z0-9_.-]", "_", name)[:40]}-{key}')


def _same_file(path, d):
    try:
        ident = json.load(open(os.path.join(d, IDENT)))
        st = os.stat(path)
        return ident['size'] == st.st_size and abs(ident['mtime'] - st.st_mtime) <= 2
    except (OSError, ValueError, KeyError):
        return False


def _sync(src, dst):
    """Copy files (and folders: faces_*) of src that are missing or newer in dst; returns the names copied."""
    done = []
    for e in os.scandir(src):
        if e.name in NO_COPY or e.name.endswith(('.v6', '.tmp')):
            continue
        t = os.path.join(dst, e.name)
        if e.is_dir():
            os.makedirs(t, exist_ok=True)
            if _sync(e.path, t):
                done.append(e.name)
        elif not os.path.exists(t) or e.stat().st_mtime > os.stat(t).st_mtime + 1:
            shutil.copy2(e.path, t + '.tmp')
            os.replace(t + '.tmp', t)
            done.append(e.name)
    return done


def _localise(path, d):
    """The copied index / probe still name the other machine's path: point them at this one."""
    for f in ('index.json', 'probe.json'):
        p = os.path.join(d, f)
        if not os.path.exists(p):
            continue
        j = json.load(open(p))
        info = j['info'] if f == 'index.json' else j
        if info.get('path') == path:
            continue
        info['path'], info['nle_path'] = path, core.nle_path(path)
        if f == 'index.json':
            j['cache'] = d
        mt = os.stat(p).st_mtime
        json.dump(j, open(p, 'w'), ensure_ascii=False, indent=1 if f == 'index.json' else None)
        os.utime(p, (mt, mt))                 # keep "newer wins" honest: rewriting paths is not a newer analysis


def _publish(path):
    """→ (shared dir, names copied into it) or None."""
    if not enabled():
        return None
    path = core.local_path(path)
    local = core.cache_dir(path)
    if not os.path.exists(os.path.join(local, 'index.json')):
        return None
    d = shared_dir(path, create=True)
    os.makedirs(d, exist_ok=True)
    st = os.stat(path)
    json.dump({'name': os.path.basename(path), 'size': st.st_size, 'mtime': st.st_mtime}, open(os.path.join(d, IDENT), 'w'))
    return d, _sync(local, d)


def publish(path):
    r = _publish(path)
    return len(r[1]) if r else 0


def adopt(path, local):
    """Called with a brand-new (empty) local cache dir. True when the shared analysis was taken."""
    try:
        d = shared_dir(path)
    except OSError:
        return False
    if not os.path.exists(os.path.join(d, 'index.json')) or not _same_file(path, d):
        return False
    _sync(d, local)
    _localise(path, local)
    return True


def refresh(path):
    """Newer files from the shared copy into the local cache (what the other machine redid: Scribe, descriptions…)."""
    path = core.local_path(path)
    try:
        d = shared_dir(path)
    except OSError:
        return 0
    if not os.path.exists(os.path.join(d, 'index.json')) or not _same_file(path, d):
        return 0
    local = core.cache_dir(path)
    got = _sync(d, local)
    if got:
        _localise(path, local)
    return len(got)


def publish_all(paths, progress=lambda *_: None):
    n = 0
    changed = []
    for k, p in enumerate(paths):
        progress(k / max(len(paths), 1), os.path.basename(p))
        try:
            r = _publish(p)
        except OSError:
            continue
        if r and r[1]:
            n += 1
            changed.append((core.local_path(p), *r))
    if core.env('SHARE_FROM'):           # the Mac: its new work goes back to the desktop's copy of the job too
        try:
            push_back(changed, lambda line: progress(1.0, line))
        except Exception:
            pass
    return n


# ── bringing a missing analysis from the other machine (SSH to a Windows OpenSSH server over Tailscale) ─────────────
SSH_OPTS = ['-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', '-o', 'StrictHostKeyChecking=accept-new']


def _ssh(args, timeout=120, data=None):
    return subprocess.run(['ssh', *SSH_OPTS, core.env('SHARE_FROM'), *args], capture_output=True, timeout=timeout, input=data)


def _ssh_up():
    try:
        return _ssh(['exit'], timeout=15).returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _reachable(progress, wait=300):
    """The other machine answers SSH — waking it through Home Assistant when it is off (claudio.llm.wake)."""
    if _ssh_up():
        return True, None
    from claudio import llm
    try:
        how = llm.wake()
    except Exception as e:
        return False, f'não consegui ligar o desktop: {e}'
    if not how:
        return False, f'{core.env("SHARE_FROM")} não respondeu por SSH (desligado? configure wake_ha_*)'
    t0 = time.time()
    while time.time() - t0 < wait:
        progress(f'ligando o desktop por {how}… {int(time.time() - t0)} s')
        if _ssh_up():
            return True, f'desktop ligado por {how}'
        time.sleep(10)
    return False, f'liguei o desktop por {how}, mas o SSH não respondeu em {wait // 60} min'


def _job_root(folder):
    """The job folder whose NAME identifies it on both machines: "…/2026-10-19 INC 9/BRUTOS" → "…/2026-10-19 INC 9"."""
    from claudio import profile
    raw = {n.lower() for n in profile.raw_names()} | {'videos', 'vídeos', 'brutos'}
    d = os.path.abspath(folder)
    while os.path.basename(d).lower() in raw and os.path.dirname(d) != d:
        d = os.path.dirname(d)
    return d


FIND = r"""
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$roots = @(%(roots)s); $job = %(job)s
$dir = $null
foreach ($r in $roots) {
  if (-not (Test-Path -LiteralPath $r)) { continue }
  $dir = Get-ChildItem -LiteralPath $r -Directory -Recurse -Depth 3 -ErrorAction SilentlyContinue | Where-Object Name -eq $job | Select-Object -First 1
  if ($dir) { break }
}
if (-not $dir) { 'NAO_ACHEI'; exit }
"""

REMOTE = FIND + r"""
$want = @(%(want)s)
$have = @($want | Where-Object { Test-Path -LiteralPath (Join-Path $dir.FullName $_) })
if (-not $have) { 'NADA ' + $dir.FullName; exit }
$tmp = Join-Path $env:TEMP ('claudio-' + [guid]::NewGuid().ToString('N') + '.tar')
Push-Location -LiteralPath $dir.FullName; & tar.exe -cf $tmp @have; Pop-Location
'TAR ' + ($tmp -replace '\\', '/') + ' ' + $have.Count
"""


def _ps_quote(x):
    return "'" + x.replace("'", "''") + "'"


def remote_script(job, rels, roots):
    """The PowerShell the other machine runs: find the job folder by name under the roots, tar the wanted analysis
    folders (relative to the job) into a temp file, print where. Sent -EncodedCommand: no quoting, accents intact."""
    return REMOTE % {'roots': ', '.join(map(_ps_quote, roots)), 'job': _ps_quote(job),
                     'want': ', '.join(_ps_quote(r.replace('/', '\\')) for r in rels)}


PUSH = FIND + r"""
$tar = Join-Path $HOME %(tar)s
& tar.exe -xf $tar -C $dir.FullName --keep-newer-files
$ok = $LASTEXITCODE
Remove-Item -LiteralPath $tar -ErrorAction SilentlyContinue
if ($ok) { 'TAR_FALHOU'; exit }
$pairs = @()
%(pairs)s
foreach ($p in $pairs) {
  $ident = Join-Path (Join-Path $dir.FullName $p[0]) 'ident.json'
  $clip = Join-Path $dir.FullName $p[1]
  if ((Test-Path -LiteralPath $ident) -or -not (Test-Path -LiteralPath $clip)) { continue }
  $f = Get-Item -LiteralPath $clip
  $j = @{name = $f.Name; size = $f.Length; mtime = ([DateTimeOffset]$f.LastWriteTimeUtc).ToUnixTimeMilliseconds() / 1000} | ConvertTo-Json -Compress
  [IO.File]::WriteAllText($ident, $j)
}
'OK ' + $dir.FullName
"""


def push_script(job, tar, pairs, roots):
    """Unpack the Mac's tar into the desktop's job (files newer there are kept), then give a clip analysed only on the
    Mac its ident.json from the desktop's own file (the Mac's copy of the footage may carry another mtime)."""
    return PUSH % {'roots': ', '.join(map(_ps_quote, roots)), 'job': _ps_quote(job), 'tar': _ps_quote(tar),
                   'pairs': '\n'.join(f'$pairs += ,@({_ps_quote(a.replace("/", chr(92)))}, {_ps_quote(b.replace("/", chr(92)))})'
                                       for a, b in pairs)}


def _nfc(x):
    return unicodedata.normalize('NFC', x)                # the Mac's drive may spell "Ação" decomposed; the desktop never does


def _pending_path():
    return os.path.join(core.CACHE, 'share_pending.json')


def push_back(changed, progress=lambda *_: None):
    """changed: [(clip, shared dir, names copied)] → {'pushed': files, 'note': …}. Files that could not go (desktop off,
    job not there) are kept for the next call."""
    if not core.env('SHARE_FROM'):
        return {'pushed': 0}
    try:
        pending = json.load(open(_pending_path(), encoding='utf-8'))
    except (OSError, ValueError):
        pending = {}
    for clip, d, names in changed:
        root = _job_root(os.path.dirname(os.path.dirname(os.path.dirname(d))))
        job = pending.setdefault(root, {'files': [], 'clips': {}})
        rd = os.path.relpath(d, root).replace(os.sep, '/')
        job['clips'][rd] = os.path.relpath(clip, root).replace(os.sep, '/')
        job['files'] = sorted(set(job['files']) | {f'{rd}/{n}' for n in names})
    if not pending:
        return {'pushed': 0}

    def save():
        os.makedirs(core.CACHE, exist_ok=True)
        json.dump(pending, open(_pending_path(), 'w', encoding='utf-8'), ensure_ascii=False)

    if not _ssh_up():
        save()
        return {'pushed': 0, 'note': 'desktop desligado: a análise nova vai para ele na próxima vez'}
    roots = [r for r in re.split(r'[;\n]', core.env('SHARE_ROOTS', 'E:\\Fotos')) if r.strip()]
    pushed, notes = 0, []
    for root in list(pending):
        job = pending[root]
        files = [f for f in job['files'] if os.path.exists(os.path.join(root, f))]
        if not files:
            del pending[root]
            continue
        progress(f'levando {len(files)} arquivo(s) de análise para o desktop')
        name = f'claudio-push-{uuid.uuid4().hex}.tar'
        with tempfile.TemporaryDirectory() as tmp:
            t = os.path.join(tmp, name)
            with tarfile.open(t, 'w', format=tarfile.PAX_FORMAT) as tf:
                for f in files:
                    tf.add(os.path.join(root, f), arcname=_nfc(f))
            cp = subprocess.run(['scp', '-q', *SSH_OPTS, t, f'{core.env("SHARE_FROM")}:{name}'], capture_output=True, timeout=1800)
        if cp.returncode:
            notes.append(f'scp falhou: {cp.stderr.decode("utf-8", "replace")[:200]}')
            continue
        script = push_script(_nfc(os.path.basename(root)), name, [(_nfc(a), _nfc(b)) for a, b in job['clips'].items()], roots)
        r = _ssh(['powershell', '-NoProfile', '-NonInteractive', '-EncodedCommand', _encoded(script)], timeout=600)
        out = r.stdout.decode('utf-8', 'replace')
        if 'OK ' in out:
            pushed += len(files)
            del pending[root]
        elif 'NAO_ACHEI' in out:
            notes.append(f'o trabalho "{os.path.basename(root)}" não está no desktop (procurei em {", ".join(roots)})')
            del pending[root]            # not there: nothing to keep trying
        else:
            notes.append(f'o desktop respondeu outra coisa: {(r.stderr or r.stdout).decode("utf-8", "replace")[:200]}')
    save()
    return {'pushed': pushed, **({'note': '; '.join(notes)} if notes else {})}


def _encoded(script):
    return base64.b64encode(script.encode('utf-16-le')).decode()


def fetch_missing(folder, files, progress=lambda *_: None):
    """→ {'fetched': n, 'note': …}. Only clips with neither a local analysis nor one next to the footage."""
    if not core.env('SHARE_FROM'):
        return {'fetched': 0}
    missing = []
    for f in files:
        try:
            if not os.path.exists(os.path.join(core.cache_dir(f), 'index.json')):
                missing.append(f)
        except OSError:
            pass
    if not missing:
        return {'fetched': 0}
    ok, note = _reachable(progress)
    if not ok:
        return {'fetched': 0, 'note': note}
    root = _job_root(folder)
    rels = {}
    for f in missing:
        d = shared_dir(f)
        rels[os.path.relpath(d, root).replace(os.sep, '/')] = f
    roots = [r for r in re.split(r'[;\n]', core.env('SHARE_ROOTS', 'E:\\Fotos')) if r.strip()]
    progress(f'procurando "{os.path.basename(root)}" no desktop ({len(missing)} clipe(s) sem análise)')
    r = _ssh(['powershell', '-NoProfile', '-NonInteractive', '-EncodedCommand', _encoded(remote_script(os.path.basename(root), list(rels), roots))], timeout=600)
    out = r.stdout.decode('utf-8', 'replace').strip().splitlines()
    line = next((x for x in out if x.startswith(('TAR ', 'NADA', 'NAO_ACHEI'))), '')
    if line.startswith('NAO_ACHEI'):
        return {'fetched': 0, 'note': f'o trabalho "{os.path.basename(root)}" não está no desktop (procurei em {", ".join(roots)})'}
    if not line.startswith('TAR '):
        return {'fetched': 0, 'note': 'o desktop ' + ('não tem análise desses clipes' if line.startswith('NADA') else
                                                     f'respondeu outra coisa: {(r.stderr or r.stdout).decode("utf-8", "replace")[:200]}')}
    remote = line.split(' ')[1]
    progress('trazendo a análise do desktop')
    with tempfile.TemporaryDirectory() as tmp:
        local_tar = os.path.join(tmp, 'a.tar')
        cp = subprocess.run(['scp', '-q', *SSH_OPTS, f'{core.env("SHARE_FROM")}:{remote}', local_tar], capture_output=True, timeout=1800)
        _ssh(['cmd', '/c', 'del', remote.replace('/', '\\')], timeout=30)
        if cp.returncode:
            return {'fetched': 0, 'note': f'scp falhou: {cp.stderr.decode("utf-8", "replace")[:200]}'}
        subprocess.run(['tar', '-xf', local_tar, '-C', root], check=True)
    n = 0
    for rel, f in rels.items():
        if adopt(f, core.cache_dir(f)):
            n += 1
    return {'fetched': n, 'note': note}
