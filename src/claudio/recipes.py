"""claudio.recipes — one Markdown file per kind of project: what to ask, the steps, the taste rules, the defaults.

  src/claudio/receitas/<nome>.md      built in (depoimento, recap-evento, beat-edit, show-multicam, curta-narrativo)
  ~/.config/claudio/receitas/<nome>.md   the editor's own ($CLAUDIO_RECIPES): a new kind, or a built-in one replaced
  _comum.md                           rules every edit follows, sent with every recipe

  Frontmatter: `chave: valor`, one per line; a value starting with [ or { is JSON (so the file is still valid YAML for
  Obsidian). Keys: nome, titulo, quando (when it fits), padroes (defaults), entregas, ferramentas. The body is Markdown;
  each `## ` heading is a section that can be read alone (recipe('depoimento', 'legendas')).
  The recipe plans, the tools execute: no recipe calls code, the model follows it.
"""
import json, os, re, unicodedata

from claudio import core

BUILTIN = os.path.join(os.path.dirname(__file__), 'receitas')
COMMON = '_comum'


def user_dir():
    return core.local_path(os.path.expanduser(core.env('RECIPES') or '~/.config/claudio/receitas'))


def _key(s):
    s = unicodedata.normalize('NFKD', str(s)).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+', '-', s).strip('-')


def parse(text):
    """→ (meta, body). Values: JSON when they start with [ or {, else the plain string."""
    meta, body = {}, text
    m = re.match(r'^---\r?\n(.*?)\r?\n---\r?\n?', text, re.S)
    if m:
        body = text[m.end():]
        for line in m.group(1).splitlines():
            k, sep, v = line.partition(':')
            if not sep or not k.strip() or line[:1].isspace():
                continue
            v = v.strip()
            if v[:1] in '[{':
                try:
                    v = json.loads(v)
                except ValueError:
                    pass
            meta[k.strip()] = v
    return meta, body.strip() + '\n'


def _files():
    """{key: (path, source)} — the editor's files replace built-in ones with the same name."""
    out = {}
    for src, d in (('builtin', BUILTIN), ('editor', user_dir())):
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for f in names:
            if f.endswith('.md') and not f.startswith('.') and f.lower() != 'readme.md':
                out[_key(os.path.splitext(f)[0])] = (os.path.join(d, f), src)
    return out


def _read(path):
    return parse(open(path, encoding='utf-8').read())


def listing():
    """Every recipe but the common rules: nome, titulo, quando, padroes, origem."""
    out = []
    for k, (p, src) in sorted(_files().items()):
        if k == _key(COMMON):
            continue
        meta, _ = _read(p)
        out.append({'nome': meta.get('nome') or k, 'titulo': meta.get('titulo', k), 'quando': meta.get('quando', ''),
                    'padroes': meta.get('padroes') or {}, 'origem': src})
    return out


def find(name):
    """The recipe for a name or a title, accent- and case-insensitive ("Depoimento", "recap evento"). None if unknown."""
    files, k = _files(), _key(name)
    if k in files:
        return files[k]
    for key, (p, src) in files.items():
        meta, _ = _read(p)
        if key != _key(COMMON) and (_key(meta.get('nome', '')) == k or _key(meta.get('titulo', '')).startswith(k)):
            return p, src
    return None


def sections(body):
    """{heading key: '## heading\\n…'} of a recipe body."""
    out = {}
    for part in re.split(r'\n(?=## )', '\n' + body):
        m = re.match(r'\s*## (.+)', part)
        if m:
            out[_key(m.group(1))] = part.strip() + '\n'
    return out


def load(name, section=None):
    """The recipe to follow: its metadata and text with the common rules after it. section: one part only, from the
    recipe or, when it has no such part, from the common rules ("legendas", "entregar-no-nle")."""
    hit = find(name)
    if not hit:
        raise KeyError(f'receita desconhecida: {name!r}; há: ' + ', '.join(r['nome'] for r in listing()))
    meta, body = _read(hit[0])
    common = _files().get(_key(COMMON))
    cbody = _read(common[0])[1] if common and common[0] != hit[0] else ''
    if section:
        k = _key(section)
        part = sections(body).get(k) or sections(cbody).get(k)
        if not part:
            raise KeyError(f'a receita {meta.get("nome", name)!r} não tem a seção {section!r}; há: '
                           + ', '.join(sorted(set(sections(body)) | set(sections(cbody)))))
        text = part
    else:
        text = f'# Receita: {meta.get("titulo", name)}\n\n{body}' + (f'\n# Regras comuns\n\n{cbody}' if cbody else '')
    return {'nome': meta.get('nome') or _key(name), 'meta': meta, 'origem': hit[1], 'text': text}
