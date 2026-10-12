"""decupa.clients — one profile per client, so the next job starts where the last one ended.

  <clients>/<slug>.md   human-readable (and agent-readable) profile; the machine part lives in a JSON block inside the
                        same file, so there is one file to sync/edit. Folder: $DECUPA_CLIENTS, default
                        D:\\Documentos\\decupa\\clientes (WSL: /mnt/d/...; elsewhere ~/decupa/clientes).

  profile fields: vocabulary · formats (aspect, duration, platform) · end_card (file, in/out) · music (tracks/folder
  used, style) · captions · structure (hook/body/CTA…) · house_style (measured from finished timelines) · notes ·
  jobs (history).
"""
import datetime, json, os, re, statistics

from decupa import core

BEGIN, END = '<!-- decupa:profile', 'decupa:profile -->'


def clients_dir():
    d = os.environ.get('DECUPA_CLIENTS')
    if not d:
        d = '/mnt/d/Documentos/decupa/clientes' if os.path.isdir('/mnt/d') else (
            'D:\\Documentos\\decupa\\clientes' if core.IS_WIN and os.path.isdir('D:\\') else os.path.expanduser('~/decupa/clientes'))
    d = core.local_path(d)
    os.makedirs(d, exist_ok=True)
    return d


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
    L = [f"# {prof['name']}\n",
         f"> Perfil de cliente do decupa · atualizado {datetime.date.today().isoformat()}. O bloco JSON no fim é a parte "
         "que o decupa lê; o resto é pra gente.\n"]
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
    L.append(f"\n## Notas\n{free_text or '_(livre — escreva aqui; o decupa preserva)_'}\n")
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
    return load(name)


def learn_style(name, prproj, sequences=None):
    """Measure the house style from finished sequences of a Premiere project and store it in the profile."""
    from decupa import nle_read
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
