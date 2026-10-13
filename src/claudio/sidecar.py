"""claudio.sidecar — the index saved next to the footage, readable by any person or agent.

  <clip>.md          next to every video: file, camera, colour space, audio, what is SEEN (scene descriptions written by
                     Claude after looking — annotate), shots and usable windows (measured), takes, slate, transcript with
                     timecode, multicam sync. Another agent can plan an edit from these files without re-analysing.
  _claudio/<clip>.jpg contact sheet (12 frames with timecode) linked from the .md
  _CLAUDIO.md         one per folder: every clip in one table + indexing progress while it runs

Sections between <!-- claudio:auto --> markers are regenerated; anything written outside them by a person is kept.
"""
import datetime, json, os, re

from claudio import core

AUTO_BEGIN, AUTO_END = '<!-- claudio:auto:begin -->', '<!-- claudio:auto:end -->'


def _old_markers(txt):
    """Logs written before the rename (v0.7) used decupa markers."""
    return txt.replace('<!-- decupa:auto:', '<!-- claudio:auto:')
VERSION = '0.8'


def md_path(video):
    d, f = os.path.split(video)
    stem = os.path.splitext(f)[0]
    clash = [x for x in os.listdir(d) if os.path.splitext(x)[0] == stem and x != f and os.path.splitext(x)[1].lower() in core.VIDEO_EXT]
    return os.path.join(d, (f if clash else stem) + '.md')


def _tc(clk, s):
    return clk.tc(clk.frame(s, 'floor'))


def _fmt_dur(s):
    m, s = divmod(round(s, 1), 60)
    return f'{int(m)}:{s:04.1f}' if m else f'{s:.1f} s'


def _table(head, rows):
    if not rows:
        return ''
    out = '| ' + ' | '.join(head) + ' |\n|' + '---|' * len(head) + '\n'
    for r in rows:
        out += '| ' + ' | '.join(str(x).replace('|', '/').replace('\n', ' ') for x in r) + ' |\n'
    return out


def render_md(idx, sheet_rel=None, sync=None):
    info = idx['info']; clk = core.clock_of(info)
    L = []
    kind = 'fala' if idx.get('speech') and idx['speech']['n_words'] > max(8, info['duration'] / 6) else 'sem fala / ambiente'
    L.append(f"# {info['name']}\n")
    L.append(f"> Índice gerado pelo **Cláudio, o Editor {VERSION}** em {datetime.date.today().isoformat()}. Tempos em segundos do arquivo e "
             f"timecode de origem. Seções marcadas como 👁 foram escritas depois de OLHAR as imagens; o resto é medido.\n")
    # file
    col = {}
    try:
        col = core.color_profile(idx) if info.get('has_video') else {}
    except Exception:
        pass
    rot = core.get_rotation(info['path'])
    a = idx.get('audio') or {}
    rows = [('Arquivo', f"`{info['nle_path']}`"),
            ('Câmera', col.get('camera') or '—'),
            ('Duração', f"{_fmt_dur(info['duration'])} · {info.get('frames', '?')} frames @ {info.get('fps_float', '?')} fps" + (' (VFR)' if info.get('vfr') else '')),
            ('Timecode', f"{_tc(clk, 0)} → {_tc(clk, info['duration'])}" if info.get('start_tc') else 'sem TC (começa em 00:00:00:00)'),
            ('Imagem', f"{info.get('width')}×{info.get('height')} {info.get('codec', '')} {col.get('pix_fmt', '')}" + (f' · **rodar {rot}°** (gravado de lado)' if rot else '')),
            ('Cor', f"**{col.get('profile', '—')}** (confiança {col.get('confidence', '—')}) — {col.get('note', '')}" if col else '—'),
            ('Áudio', f"{info.get('audio_channels', 0)} canais · fala/ruído {int(a.get('speech_ratio', 0) * 100)}% do tempo · pico {a.get('peak_db', '—')} dBFS"
                      + (f" · **clipou {a['clipped_ms']} ms**" if a.get('clipped_ms') else '') if a else 'sem áudio'),
            ('Tipo', kind)]
    L.append(_table(['', ''], rows))
    if sheet_rel:
        L.append(f"\n![contact sheet]({sheet_rel.replace(' ', '%20')})\n")
    # what is seen (Claude's annotations)
    log = core.read_log(idx)
    L.append('\n## 👁 Cenas (o que se vê)\n')
    if log:
        L.append(_table(['TC in', 's', 'até', 'Descrição', 'Tags', 'Nota'],
                        [(l.get('tc_in', _tc(clk, l['s'])), f"{l['s']:.1f}", f"{l['e']:.1f}", l['text'], ', '.join(l.get('tags', [])),
                          '★' * int(l['rating']) if l.get('rating') else '') for l in log]))
    else:
        L.append('_Ainda não descrito. Para descrever: `contact_sheet` no clipe e `annotate` com o que aparece em cada trecho._\n')
    # slate / takes
    if idx.get('speech'):
        sl = core.slate(idx)
        if sl['has_slate']:
            L.append(f"\n## Claquete\ncena **{sl['scene']}** · take **{sl['take']}**" + (f" · parte {sl['part']}" if sl['part'] else '')
                     + (f" · {sl['shot']}" if sl['shot'] else '') + f" · ação {sl['action'][0]:.1f}s → {'corta ' if sl['has_cut_call'] else 'fim '}{sl['action'][1]:.1f}s\n")
        tk = core.takes_report(idx)
        multi = [g for g in tk['groups'] if g['attempts'] > 1]
        if multi:
            L.append('\n## Takes (falas repetidas)\n')
            for g in multi:
                L.append(f"**“{g['line'][:140]}”** — {g['attempts']} tentativas, melhor: take {g['best_take']}\n")
                L.append(_table(['take', 'TC', 's', 'cobertura', 'veredito', 'texto'],
                                [(c['take'], _tc(clk, c['s']), f"{c['s']:.2f}–{c['e']:.2f}", f"{int(c['coverage'] * 100)}%",
                                  ('✅ melhor' if c['take'] == g['best_take'] else '') + (' ✗ falante criticou' if c['self_critique_after'] else '')
                                  + (' incompleto' if not c['complete'] else ''), c['text'][:90]) for c in g['candidates']]))
    # picture (measured)
    if info.get('has_video') and not idx.get('light'):
        b = core.broll_windows(idx)
        L.append('\n## Imagem (medido)\n')
        cuts = idx.get('picture', {}).get('cuts_sec', [])
        L.append(f"Cortes internos: {', '.join(f'{c:.2f}s' for c in cuts) if cuts else 'nenhum (plano contínuo)'}\n")
        if kind == 'fala':
            L.append('_Clipe de fala: escolha pelos takes e pela transcrição; a análise de foco/movimento serve para b-roll._\n')
        elif b['windows']:
            L.append('\n**Trechos utilizáveis** (foco relativo ao próprio clipe, 1.0 = mais nítido; câmera = movimento medido)\n')
            L.append(_table(['TC', 's', 'dur', 'câmera', 'nitidez', 'tremor', 'fala'],
                            [(_tc(clk, w['s']), f"{w['s']:.1f}–{w['e']:.1f}", f"{w['len']:.1f}", w['camera'], w['picture']['sharpness'],
                              w['picture']['shake'], w['speech_words']) for w in sorted(b['windows'], key=lambda w: w['s'])[:20]]))
        if b['rejected'] and kind != 'fala':
            L.append('\n**Trechos a evitar**: ' + '; '.join(f"{r['s']:.1f}–{r['e']:.1f}s {r['reason']}" for r in b['rejected'][:15]) + '\n')
    # multicam
    if sync:
        L.append(f"\n## Sync multicâmera\n{sync}\n")
    # transcript
    if idx.get('speech') and idx['speech']['segments']:
        L.append('\n## Transcrição\n')
        L.append(_table(['TC', 's', 'fala'], [(_tc(clk, s['s']), f"{s['s']:.1f}–{s['e']:.1f}", core.clean_text(s['text'])) for s in idx['speech']['segments']]))
    return '\n'.join(L)


def write_clip_md(idx, sheet=True, sync=None):
    """Write/refresh <clip>.md next to the video (and its contact sheet). Text outside the auto markers is preserved."""
    info = idx['info']
    video = info['path']
    folder = os.path.dirname(video)
    sheet_rel = None
    try:
        if sheet and info.get('has_video'):
            sd = core.data_dir(folder)
            sp = os.path.join(sd, os.path.splitext(info['name'])[0] + '.jpg')
            if not os.path.exists(sp):
                img, _ = core.contact_sheet(idx, None, None, 12)
                open(sp, 'wb').write(core.to_jpeg(img, q=78, max_side=1800))
            sheet_rel = os.path.basename(sd) + '/' + os.path.basename(sp)
        p = md_path(video)
        body = render_md(idx, sheet_rel, sync)
        old = _old_markers(open(p, encoding='utf-8').read()) if os.path.exists(p) else ''
        if AUTO_BEGIN in old and AUTO_END in old:
            new = old[:old.index(AUTO_BEGIN)] + AUTO_BEGIN + '\n' + body + '\n' + old[old.index(AUTO_END):]
        else:
            new = AUTO_BEGIN + '\n' + body + '\n' + AUTO_END + '\n\n## Notas\n_(espaço livre — o Cláudio não apaga o que estiver fora dos marcadores)_\n' + (('\n' + old) if old else '')
        open(p, 'w', encoding='utf-8').write(new)
        return p
    except OSError as e:              # read-only card / no permission: keep it in the cache instead
        p = os.path.join(idx['cache'], os.path.splitext(info['name'])[0] + '.md')
        open(p, 'w', encoding='utf-8').write(render_md(idx, None, sync))
        return p


def write_folder_md(folder, progress=None):
    """_CLAUDIO.md: every video in the folder (indexed or not) in one table, with indexing progress on top."""
    folder = core.local_path(folder)
    vids = sorted(f for f in os.listdir(folder) if os.path.splitext(f)[1].lower() in core.VIDEO_EXT and not f.startswith('._'))
    rows, done, total_s = [], 0, 0.0
    for f in vids:
        p = os.path.join(folder, f)
        try:
            ip = os.path.join(core.cache_dir(p), 'index.json')
        except OSError:
            continue
        if os.path.exists(ip):
            idx = json.load(open(ip))
            done += 1
            info = idx['info']; total_s += info['duration']
            col = {}
            cp = os.path.join(idx['cache'], 'color.json')
            if os.path.exists(cp):
                col = json.load(open(cp))
            log = core.read_log(idx) if os.path.exists(core.log_path(idx)) else []
            seen = log[0]['text'] if log else ''
            said = (idx.get('speech') or {}).get('segments') or []
            first = said[0]['text'][:70] if said else ''
            mdn = os.path.basename(md_path(p))
            rows.append((f"[{f}]({mdn.replace(' ', '%20')})", _fmt_dur(info['duration']), info.get('start_tc') or '—',
                         (col.get('profile') or '').replace('provável ', '~'), 'fala' if said and len(' '.join(s['text'] for s in said)) > 60 else '—',
                         seen[:70] or '_(não descrito)_', first))
        else:
            rows.append((f, '—', '—', '—', '—', '_(não indexado)_', ''))
    head = f"# Decupagem — {os.path.basename(folder)}\n\n"
    if progress:
        head += f"> ⏳ **Indexando**: {progress}\n\n"
    head += f"{done}/{len(vids)} clipes indexados · {_fmt_dur(total_s)} de material · Cláudio {VERSION} · {datetime.datetime.now():%Y-%m-%d %H:%M}\n\n"
    head += "Cada clipe tem um `.md` ao lado (técnico, cor, cenas, takes, transcrição) e um contact sheet em `_claudio/`.\n\n"
    out = head + _table(['clipe', 'dur', 'TC', 'cor', 'fala', 'o que se vê', 'primeira fala'], rows)
    try:
        p = os.path.join(folder, '_CLAUDIO.md')
        open(p, 'w', encoding='utf-8').write(out)
        if os.path.exists(os.path.join(folder, '_DECUPA.md')):
            os.remove(os.path.join(folder, '_DECUPA.md'))      # pre-rename name of this same file
        return p
    except OSError:
        return None


# ───────────────────────────── project overview (after everything is indexed) ─────────────────────────────
STOP = set('a o e de da do das dos que um uma é em no na nos nas pra para por com se não mas mais como eu você ele ela a gente '
           'isso esse essa aqui lá tá né então já foi vai ser tem ter meu minha seu sua the and to of in is it you that on for'.split())


def project_digest(folder, max_chars=18000):
    """Everything the logs know about a job, condensed for reading at once: totals, cameras, colour, when it was shot,
    what is said (most frequent words, first lines per clip), what was seen (annotations), takes, b-roll."""
    import collections
    root = core.local_path(folder)
    vids = []
    for dp, dn, fn in os.walk(root):
        dn[:] = [x for x in dn if x.lower() not in core.PROXY_DIRS and not x.startswith(('_claudio', '_decupa', '.', '#'))]
        vids += [os.path.join(dp, f) for f in sorted(fn) if os.path.splitext(f)[1].lower() in core.VIDEO_EXT and not core.is_proxy(os.path.join(dp, f))]
    clips, words, cams, colors, total, speech_s, not_indexed = [], collections.Counter(), collections.Counter(), collections.Counter(), 0.0, 0.0, []
    for p in vids:
        ip = os.path.join(core.cache_dir(p), 'index.json')
        if not os.path.exists(ip):
            not_indexed.append(os.path.basename(p)); continue
        idx = json.load(open(ip))
        info = idx['info']; total += info['duration']
        col = {}
        cp = os.path.join(idx['cache'], 'color.json')
        if os.path.exists(cp):
            col = json.load(open(cp))
        if not col.get('camera'):
            try:
                col['camera'] = core.camera_of(p)          # bytes scan only: milliseconds
            except Exception:
                pass
        cams[col.get('camera') or '?'] += 1
        colors[(col.get('profile') or 'não medido').replace('provável ', '')] += 1
        segs = (idx.get('speech') or {}).get('segments') or []
        txt = ' '.join(s['text'] for s in segs)
        if segs:
            speech_s += sum(s['e'] - s['s'] for s in segs)
        for w in re.findall(r'\w+', txt.lower()):
            if len(w) > 3 and w not in STOP and core.norm(w) not in core.CREW:
                words[w] += 1
        log = core.read_log(idx) if os.path.exists(core.log_path(idx)) else []
        clips.append({'clip': os.path.relpath(p, root), 'dur': round(info['duration'], 1), 'tc': info.get('start_tc'),
                      'fps': info.get('fps_float'), 'speech': bool(segs) and len(txt) > 60,
                      'said': core.clean_text(txt)[:220], 'seen': [l['text'] for l in log][:3]})
    head = {'folder': core.nle_path(root), 'clips': len(clips), 'not_indexed': not_indexed[:20], 'total_min': round(total / 60, 1),
            'speech_min': round(speech_s / 60, 1), 'cameras': dict(cams), 'color': dict(colors),
            'frequent_words': [w for w, _ in words.most_common(40)]}
    body, used = [], len(json.dumps(head, ensure_ascii=False))
    for c in clips:
        s = json.dumps(c, ensure_ascii=False)
        if used + len(s) > max_chars:
            body.append({'more_clips': len(clips) - len(body)}); break
        body.append(c); used += len(s)
    return {**head, 'per_clip': body}


def write_overview(job_folder, markdown):
    """_VISAO_GERAL.md at the job root: what the project is, the material, the open questions and the directions."""
    root = core.local_path(job_folder)
    p = os.path.join(root, '_VISAO_GERAL.md')
    old = _old_markers(open(p, encoding='utf-8').read()) if os.path.exists(p) else ''
    body = markdown.strip() + '\n'
    if AUTO_BEGIN in old and AUTO_END in old:
        new = old[:old.index(AUTO_BEGIN)] + AUTO_BEGIN + '\n' + body + old[old.index(AUTO_END):]
    else:
        new = AUTO_BEGIN + '\n' + body + AUTO_END + '\n\n## Notas\n_(livre — o Cláudio não apaga o que estiver fora dos marcadores)_\n'
    open(p, 'w', encoding='utf-8').write(new)
    return p
