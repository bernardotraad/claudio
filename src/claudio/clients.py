"""claudio.clients — one profile per client, so the next job starts where the last one ended.

  <clients>/<slug>.md   human-readable (and agent-readable) profile; the machine part lives in a JSON block inside the
                        same file, so there is one file to sync/edit. Folder: $CLAUDIO_CLIENTS, else
                        <vault>/trabalho/clientes when the Obsidian vault is found ($CLAUDIO_VAULT, D:\\bernardo\\vault,
                        ~/vault), else ~/Documents/Claudio/clientes. In the vault the page follows its rules: frontmatter
                        (tipo: cliente), kebab-case file name, linked from trabalho/clientes/README.md.

  profile fields: vocabulary · formats (aspect, duration, platform) · end_card (file, in/out) · music (tracks/folder
  used, style) · captions · structure (hook/body/CTA…) · house_style (measured from finished timelines) · notes ·
  jobs (history).
"""
import datetime, json, os, re, statistics

from claudio import core

BEGIN, END = '<!-- claudio:profile', 'claudio:profile -->'


def vault_dir():
    cands = [core.env('VAULT'), '/mnt/d/bernardo/vault', 'D:\\bernardo\\vault', os.path.expanduser('~/vault')]
    for c in cands:
        if c and os.path.isfile(os.path.join(core.local_path(c), 'CLAUDE.md')) and os.path.isdir(os.path.join(core.local_path(c), 'trabalho')):
            return core.local_path(c)
    return None


def clients_dir():
    d = core.env('CLIENTS')
    if not d:
        v = vault_dir()
        d = os.path.join(v, 'trabalho', 'clientes') if v else os.path.expanduser(os.path.join('~', 'Documents', 'Claudio', 'clientes'))
    d = core.local_path(d)
    os.makedirs(d, exist_ok=True)
    return d


def _in_vault(d):
    v = vault_dir()
    return bool(v) and os.path.abspath(d).startswith(os.path.abspath(v))


def _link_in_readme(d, slug_, name):
    """Vault rule: a new page is linked from its folder README."""
    rp = os.path.join(d, 'README.md')
    if not os.path.exists(rp):
        open(rp, 'w', encoding='utf-8').write(
            f"---\ntipo: indice\nstatus: ativo\natualizado: {datetime.date.today().isoformat()}\n---\n# Clientes\n\n"
            "Um perfil por cliente, mantido pelo [[projetos/claudio|Cláudio, o Editor]]: vocabulário, formatos, trilhas, "
            "legendas e o estilo da casa medido nas timelines finalizadas. O bloco JSON no fim de cada página é o que o "
            "Cláudio lê; o resto é texto livre.\n\n## Clientes\n\nVoltar a [[trabalho/README|Trabalho]].\n")
        tp = os.path.join(os.path.dirname(d), 'README.md')        # and the new folder from trabalho/README.md
        if os.path.exists(tp):
            t = open(tp, encoding='utf-8').read()
            if '[[trabalho/clientes/README' not in t:
                t = t.replace('\n\nVoltar ao [[index]]', '\n- [[trabalho/clientes/README|Clientes]] — perfis por cliente (Cláudio)\n\nVoltar ao [[index]]', 1)
                open(tp, 'w', encoding='utf-8').write(t)
    txt = open(rp, encoding='utf-8').read()
    link = f'[[trabalho/clientes/{slug_}'
    if link in txt:
        return
    line = f'- [[trabalho/clientes/{slug_}|{name}]]\n'
    txt = txt.replace('## Clientes\n\n', '## Clientes\n\n' + line, 1) if '## Clientes\n\n' in txt else txt + line
    txt = re.sub(r'^atualizado: .*$', f'atualizado: {datetime.date.today().isoformat()}', txt, count=1, flags=re.M)
    open(rp, 'w', encoding='utf-8').write(txt)


def slug(name):
    import unicodedata
    s = unicodedata.normalize('NFKD', name.lower()).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]+', '-', s).strip('-') or 'cliente'



def path_of(name):
    return os.path.join(clients_dir(), slug(name) + '.md')


def load(name):
    p = path_of(name)
    if not os.path.exists(p):
        return None
    txt = open(p, encoding='utf-8').read()
    if BEGIN in txt and END in txt:
        j = txt[txt.index(BEGIN) + len(BEGIN): txt.index(END)]
        prof = json.loads(j)
    else:
        prof = {'name': name}
    prof['_path'] = core.nle_path(p)
    prof['_notes_md'] = txt.split(END)[-1].strip() if END in txt else txt
    return prof


def _render(prof, free_text):
    hs = prof.get('house_style') or {}
    L = [f"---\ntipo: cliente\nstatus: ativo\natualizado: {datetime.date.today().isoformat()}\n---\n# {prof['name']}\n",
         f"> Perfil de cliente do Cláudio, o Editor. O bloco JSON no fim é a parte que o Cláudio lê; o resto é pra gente.\n"]
    rows = [('Vocabulário', prof.get('vocabulary') or '—'),
            ('Formatos', ', '.join(prof.get('formats', [])) or '—'),
            ('Assinatura / end card', (prof.get('end_card') or {}).get('path', '—')),
            ('Trilhas', ', '.join(prof.get('music', {}).get('tracks', [])[:6]) or prof.get('music', {}).get('style', '—')),
            ('Legendas', prof.get('captions') or '—'),
            ('Estrutura', prof.get('structure') or '—')]
    L.append('| | |\n|---|---|\n' + ''.join(f'| {a} | {b} |\n' for a, b in rows))
    if hs:
        L.append('\n## Estilo da casa (medido nas timelines finalizadas)\n')
        L.append(f"- duração típica: **{hs.get('duration_s')} s** · planos de fala (V1): **{hs.get('spine_median_s')} s** (mediana)\n"
                 f"- b-roll por cima da fala: **{hs.get('broll_median_s')} s** por corte, cobrindo **{hs.get('broll_coverage_pct')}%** do vídeo\n"
                 f"- legendas em blocos de **{hs.get('caption_median_s')} s** · termina com: **{hs.get('ends_with')}**\n"
                 f"- medido em {hs.get('sequences')} sequências de: {', '.join(hs.get('learned_from', []))}\n")
    if prof.get('jobs'):
        L.append('\n## Trabalhos\n' + ''.join(f"- {j['date']} · {j['name']}" + (f" — `{j['folder']}`" if j.get('folder') else '') + '\n' for j in prof['jobs'][-30:]))
    data = {k: v for k, v in prof.items() if not k.startswith('_')}
    L.append(f"\n## Notas\n{free_text or '_(livre — escreva aqui; o Cláudio preserva)_'}\n")
    L.append(f"\n{BEGIN}\n{json.dumps(data, ensure_ascii=False, indent=1)}\n{END}\n")
    return '\n'.join(L)


def save(name, updates):
    prof = load(name) or {'name': name}
    free = ''
    p = path_of(name)
    if os.path.exists(p):
        txt = open(p, encoding='utf-8').read()
        m = re.search(r'## Notas\n(.*?)\n' + re.escape(BEGIN), txt, re.S)
        free = m.group(1).strip() if m else ''
    for k, v in (updates or {}).items():
        if k == 'jobs' and isinstance(v, list):
            prof.setdefault('jobs', []).extend(v)
        elif isinstance(v, dict) and isinstance(prof.get(k), dict):
            prof[k] = {**prof[k], **v}
        else:
            prof[k] = v
    open(p, 'w', encoding='utf-8').write(_render({k: v for k, v in prof.items() if not k.startswith('_')}, free))
    if _in_vault(os.path.dirname(p)):
        _link_in_readme(os.path.dirname(p), slug(name), prof['name'])
    return load(name)


def learn_style(name, prproj, sequences=None):
    """Measure the house style from finished sequences of a Premiere project and store it in the profile."""
    from claudio import nle_read
    seqs = [s['name'] for s in nle_read.premiere_sequences(prproj)]
    if sequences:
        seqs = [s for s in seqs if s in sequences]
    pats = []
    for s in seqs:
        try:
            p = nle_read.edit_patterns(prproj, s)
        except Exception:
            continue
        if p['spine_V1']['shots'] >= 2 and p['duration_s'] < 600:      # finished pieces, not logging/sync sequences
            pats.append(p)
    if not pats:
        raise ValueError('no finished sequences found to learn from')
    med = lambda xs: round(statistics.median([x for x in xs if x is not None]), 2) if any(x is not None for x in xs) else None
    ends = [p['ends_with'] for p in pats if p['ends_with']]
    hs = {'sequences': len(pats), 'learned_from': [p['sequence'] for p in pats],
          'duration_s': med([p['duration_s'] for p in pats]),
          'spine_median_s': med([p['spine_V1']['median_len_s'] for p in pats]),
          'broll_median_s': med([p['layers_above']['median_len_s'] for p in pats]),
          'broll_coverage_pct': med([p['layers_above']['coverage_pct_of_duration'] for p in pats]),
          'caption_median_s': med([p['captions']['median_len_s'] for p in pats]),
          'ends_with': max(set(ends), key=ends.count) if ends else None,
          'source': core.nle_path(core.local_path(prproj))}
    return save(name, {'house_style': hs})
