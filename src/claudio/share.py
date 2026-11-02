"""claudio.share — one analysis for every machine that sees the footage: the desktop indexes, the Mac reads it over SMB
instead of indexing again (transcript, thumbnails, picture, faces, voices, descriptions, caption fixes).

  publish(path)        copy the clip's cache next to the footage, <folder>/_claudio/analise/<clip>-<key>/ (only files newer
                       than the copy there). On when settings.json says {"share_analysis": "1"} (it writes into the footage
                       folders, so it is opt-in)
  adopt(path, local)   a clip with no local cache: take the shared copy (called by core.cache_dir), paths rewritten for
                       this machine
  refresh(path)        a clip already adopted: bring in what the other machine redid since (newer files win)

The local cache is keyed by the clip's path, which differs between machines (/mnt/e/… on the desktop, /Volumes/… on the
Mac); the shared copy is keyed by name + size and checked against the modification time (±2 s: SMB and NTFS round it
differently), so a different take with the same name is never mistaken for this one.
"""
import hashlib, json, os, re, shutil

from claudio import core

SHARED = 'analise'
IDENT = 'ident.json'
NO_COPY = {'proxy.json', IDENT}                      # the proxy is found again on each machine (its path is local)


def enabled():
    return str(core.env('SHARE_ANALYSIS', '0')).lower() in ('1', 'true', 'sim', 'yes')


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


def publish(path):
    if not enabled():
        return 0
    path = core.local_path(path)
    local = core.cache_dir(path)
    if not os.path.exists(os.path.join(local, 'index.json')):
        return 0
    d = shared_dir(path, create=True)
    os.makedirs(d, exist_ok=True)
    st = os.stat(path)
    json.dump({'name': os.path.basename(path), 'size': st.st_size, 'mtime': st.st_mtime}, open(os.path.join(d, IDENT), 'w'))
    return len(_sync(local, d))


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
    for k, p in enumerate(paths):
        progress(k / max(len(paths), 1), os.path.basename(p))
        try:
            n += 1 if publish(p) else 0
        except OSError:
            pass
    return n
