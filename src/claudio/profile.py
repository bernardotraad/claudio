"""claudio.profile — how THIS editor works: the job's folder names and where the client profiles live.

  ~/.config/claudio/perfil.json   ($CLAUDIO_PROFILE points somewhere else). Only what differs from DEFAULTS:

  {"pastas": {"brutos": "BRUTOS", "audio": "BRUTOS/00 AUDIO", "projeto": "#projeto", "legendas": "#legendas", ...},
   "nle": "premiere",                        the editor's NLE: "premiere" · "resolve" (claudio.targets)
   "vault": "D:\\\\vault",                    an Obsidian vault: client pages follow its rules (frontmatter, README link)
   "clientes": "D:\\\\vault\\\\clientes"}      client profiles (default: <vault>/trabalho/clientes, else ~/Documents/Claudio/clientes)

  pastas are relative to the job folder ("/" separates levels), except "backups", which sits next to the NLE project
  file. The engine never spells a folder name: it asks folder('legendas', job).
"""
import json, os

from claudio import core

def path():
    return os.path.expanduser(core.env('PROFILE') or '~/.config/claudio/perfil.json')

DEFAULTS = {
    'pastas': {
        'brutos': 'brutos',                      # camera footage: brutos/<camera>/
        'audio': 'brutos/audio',                 # external recorders
        'fotos': 'brutos/fotos',
        'projeto': 'projeto',                    # the NLE project, XMLs, selects, sync
        'trilhas': 'trilhas',                    # music
        'legendas': 'legendas',                  # .srt, caption overlays and MOGRT plans
        'overlays': 'projeto/claudio_overlays',  # lower thirds, titles, end cards
        'backups': 'claudio_backups',            # the panel's sequence backups, next to the NLE project
    },
    'nle': None,                                 # "premiere" · "resolve": which rules the server sends (claudio.targets)
    'vault': None,
    'clientes': None,
}

# folders that hold raw footage in jobs that don't follow the profile
RAW_NAMES = ('BRUTOS', 'VIDEOS', 'VÍDEOS', 'RAW', 'FOOTAGE', 'MATERIAL', 'CARTOES', 'CARTÕES')


def _merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def load():
    """DEFAULTS with perfil.json on top (read every time: it is tiny and the panel may run for days)."""
    try:
        mine = json.load(open(path(), encoding='utf-8'))
    except (OSError, ValueError):
        mine = {}
    return _merge(DEFAULTS, mine)


def get(key, default=None):
    """get('pastas.legendas'), get('vault')."""
    v = load()
    for k in key.split('.'):
        if not isinstance(v, dict) or k not in v:
            return default
        v = v[k]
    return default if v is None else v


def folder(name, job, create=False):
    """<job>/<pastas[name]> as this process can open it."""
    d = os.path.join(core.local_path(job), *get('pastas.' + name).split('/'))
    if create:
        os.makedirs(d, exist_ok=True)
    return d


def job_of(project_file):
    """The job folder of an NLE project: <job>/<pastas.projeto>/x.prproj → <job>; a project elsewhere → its folder."""
    d = os.path.dirname(core.local_path(project_file))
    if os.path.basename(d).lower() == get('pastas.projeto').split('/')[-1].lower():
        d = os.path.dirname(d)
    return d


def work_dirs():
    """Folder names the tools write into (project, music, captions, overlays, backups), lowercase: a walk for footage
    skips them. Any folder starting with '#' counts too (the editor's own convention for non-footage folders)."""
    p = get('pastas')
    return {p[k].split('/')[0].lower() for k in ('projeto', 'trilhas', 'legendas', 'overlays', 'backups')}


def skip_dir(name, work=None):
    """True for a folder a footage walk must not enter. work = work_dirs(), computed once per walk."""
    return name.startswith(('#', '.')) or name.lower() in (work if work is not None else work_dirs())


def raw_names():
    """Names of the raw-footage folder, the profile's first."""
    own = get('pastas.brutos').split('/')[0]
    return [own] + [n for n in RAW_NAMES if n.lower() != own.lower()]


def summary():
    """What the panel and claudio-doctor need: the resolved folders, raw names and where the profile came from."""
    p = load()
    return {'file': path() if os.path.isfile(path()) else None, 'pastas': p['pastas'], 'raw_names': raw_names(),
            'nle': p['nle'], 'vault': p['vault'], 'clientes': p['clientes']}
