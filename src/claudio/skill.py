"""claudio.skill — the motion-designer skill (overlays, captions, narration) installed from its author's repository.

The skill is Gabriel Greco's (github.com/gabrielgreco1/skills), not part of Cláudio: it is downloaded from there, at
the commit Cláudio was tested with (motion._core_html patches its timeline.html), never copied into this repository.

  claudio-doctor --fix-skill            installs it when missing, or updates a copy installed this way to COMMIT
  claudio-doctor --fix-skill --force    also replaces a copy installed by hand (the old one goes to ../skills-backup)
"""
import datetime, io, os, shutil, subprocess, tarfile, tempfile, urllib.request

from claudio import core

REPO = 'gabrielgreco1/skills'
COMMIT = '4f9d413'                                  # 2026-10-28; tests/test_overlay, test_mogrt, test_srt_captions pass
MARKER = '.claudio-skill.json'


def dest():
    return os.path.expanduser(core.env('MOTION_SKILL') or '~/.claude/skills/motion-designer')


def installed():
    """{'path', 'commit' (None = installed by hand or another way)} or None when absent."""
    d = dest()
    if not os.path.isfile(os.path.join(d, 'templates', 'timeline.html')):
        return None
    try:
        import json
        return {'path': d, 'commit': json.load(open(os.path.join(d, MARKER))).get('commit')}
    except (OSError, ValueError):
        return {'path': d, 'commit': None}


def _download(url):
    """urllib, else curl: a uv/python.org Python on macOS may not find the system certificates (SSL verify fails)."""
    try:
        return urllib.request.urlopen(url, timeout=120).read()
    except Exception as e:
        curl = shutil.which('curl')
        if not curl:
            raise
        r = subprocess.run([curl, '-fsSL', '--max-time', '180', url], capture_output=True)
        if r.returncode:
            raise RuntimeError(f'{url}: {e}; curl: {r.stderr.decode(errors="replace")[:200]}')
        return r.stdout


def _safe_members(tf, prefix):
    for m in tf.getmembers():
        name = m.name.split('/', 1)[1] if '/' in m.name else ''
        if not name.startswith(prefix + '/') or m.issym() or m.islnk():
            continue
        rel = os.path.normpath(name[len(prefix) + 1:])
        if rel.startswith('..') or os.path.isabs(rel):
            continue
        m.name = rel
        yield m


def install(force=False, commit=COMMIT, progress=print):
    """Download the skill at `commit` and put it in place. A copy installed by hand is left alone unless force."""
    cur = installed()
    if cur and cur['commit'] is None and not force:
        return {'skipped': True, 'path': cur['path'], 'why': 'instalada à mão (sem marca do Cláudio); use --force para trocar'}
    if cur and cur['commit'] == commit:
        return {'skipped': True, 'path': cur['path'], 'why': f'já está no commit {commit}'}
    url = f'https://codeload.github.com/{REPO}/tar.gz/{commit}'
    progress(f'baixando a skill motion-designer ({REPO}@{commit})…')
    data = _download(url)
    d = dest()
    tmp = tempfile.mkdtemp(prefix='motion-designer-', dir=os.path.dirname(d) if os.path.isdir(os.path.dirname(d)) else None)
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as tf:
            members = list(_safe_members(tf, 'motion-designer'))
            if not any(m.name == os.path.join('templates', 'timeline.html') for m in members):
                raise RuntimeError(f'{url}: motion-designer/templates/timeline.html não está no pacote')
            tf.extractall(tmp, members=members)
        import json
        json.dump({'repo': REPO, 'commit': commit, 'installed': datetime.date.today().isoformat()},
                  open(os.path.join(tmp, MARKER), 'w'))
        backup = None
        if os.path.exists(d):      # kept OUTSIDE the skills folder: a second SKILL.md there would load a second skill
            bdir = os.path.join(os.path.dirname(os.path.dirname(d)), 'skills-backup')
            os.makedirs(bdir, exist_ok=True)
            backup = os.path.join(bdir, f'{os.path.basename(d)}-{datetime.datetime.now().strftime("%Y%m%d-%H%M%S")}')
            shutil.move(d, backup)
        os.makedirs(os.path.dirname(d), exist_ok=True)
        shutil.move(tmp, d)
        return {'installed': d, 'commit': commit, 'backup': backup}
    finally:
        if os.path.isdir(tmp):
            shutil.rmtree(tmp, ignore_errors=True)
