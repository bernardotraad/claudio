"""claudio.learn — learn a client from videos the editor already delivered: what comes out of the edit, measured
WITHOUT AI, into the client profile and a draft recipe of the client's own.

  learn(client, folder or paths)
      measures each finished video (Whisper, local and free — never the paid STT — for the speech timing):
        formato · duração · corte a cada (median shot) · fala % · primeira fala (gancho) · vinheta final (after the last
        word) · legenda queimada (ESTIMATE: the band of the frame that changes alone when a caption block swaps) ·
        rosto na tela % (thumbs with a big face) · música de fundo (ESTIMATE: the quietest stretches aren't silent) ·
        loudness + true peak
      aggregates them (p10 / median / p90), picks the closest built-in recipe, and writes:
        ~/.config/claudio/receitas/<client>.md   the client's recipe (measured numbers + the base recipe's steps)
        profile house_style.medido_videos        the numbers; profile "receita" → this recipe (when it has none)
  What isn't a number — structure, tone, what each kind of shot shows, what the client expects — is the AI pass's job:
  the `aprender` prompt reads this draft, looks at the videos and rewrites the recipe with save_recipe.
"""
import datetime, glob, os, re, statistics, subprocess

import numpy as np

from claudio import core, recipes

VIDEO_EXT = ('.mp4', '.mov', '.mxf', '.m4v')


# ───────────────────────────── one video ─────────────────────────────
def _loudness(path):
    r = subprocess.run(['ffmpeg', '-nostats', '-i', path, '-vn', '-af', 'ebur128=peak=true', '-f', 'null', '-'],
                       capture_output=True, text=True)
    i = re.findall(r'I:\s+(-?[\d.]+) LUFS', r.stderr)
    p = re.findall(r'Peak:\s+(-?[\d.]+) dBFS', r.stderr)
    return (float(i[-1]) if i else None), (float(p[-1]) if p else None)


def _gray_frames(path, fps=10, w=90, h=160):
    r = subprocess.run(['ffmpeg', '-v', 'error', '-i', path, '-vf', f'fps={fps},scale={w}:{h},format=gray',
                        '-f', 'rawvideo', '-'], capture_output=True)
    a = np.frombuffer(r.stdout, np.uint8)
    return a.reshape(-1, h, w).astype(np.float32) if a.size else np.zeros((0, h, w), np.float32)


def captions(path, duration, fps=10, bands=16):
    """Burned-in captions, estimated without OCR: when a caption block swaps, one horizontal band of the frame changes
    while the rest barely does (a cut changes everything). → band (0 = top), block swaps, median block, first swap,
    coverage. None when no band changes alone often enough to be captions."""
    f = _gray_frames(path, fps)
    if len(f) < 3:
        return None
    d = np.abs(np.diff(f, axis=0))
    bh = f.shape[1] // bands
    per = np.stack([d[:, b * bh:(b + 1) * bh].mean(axis=(1, 2)) for b in range(bands)], 1)
    ratio = per / (np.median(per, axis=1, keepdims=True) + 1.0)
    alone = ratio > 4
    band = int(np.argmax(alone.sum(axis=0)))
    t = (np.where(alone[:, band] & (per[:, band] > 6))[0] + 1) / fps
    if len(t):
        t = t[np.insert(np.diff(t) > 0.15, 0, True)]
    if len(t) < max(4, duration / 10):                     # fewer than one swap per 10 s: not a caption track
        return None
    gaps = np.diff(t)
    blocks = gaps[(gaps > 0.2) & (gaps < 4)]
    covered = float(blocks.sum()) + (float(np.median(blocks)) if len(blocks) else 0)
    return {'faixa_y': round(band / bands + 0.5 / bands, 2), 'trocas': int(len(t)),
            'bloco_s': round(float(np.median(blocks)), 2) if len(blocks) else None,
            'primeira_s': round(float(t[0]), 2), 'cobertura_pct': round(min(100.0, 100 * covered / duration))}


def face_share(path, info):
    """% of the clip's thumbs with a BIG face (≥ 12 % of the frame width): someone talking to the camera, not people
    in the background of a b-roll."""
    import cv2
    from claudio import people
    th = core.load_thumbs(core.cache_dir(path), info)
    if not th or not th[1]:
        return None
    ts, ims = th
    det = people._det(*ims[0].size, score=0.7)
    big = 0
    for im in ims:
        _, fs = det.detect(cv2.cvtColor(np.asarray(im), cv2.COLOR_RGB2BGR))
        if fs is not None and len(fs) and float(fs[:, 2].max()) >= 0.12 * im.size[0]:
            big += 1
    return round(100 * big / len(ims))


def measure(path, progress=lambda *_: None):
    path = core.local_path(path)
    old = os.environ.get('CLAUDIO_STT')
    os.environ['CLAUDIO_STT'] = 'whisper'                  # learning reads many videos: never the paid STT
    try:
        idx = core.build_index(path, 'pt', progress)
    finally:
        if old is None:
            os.environ.pop('CLAUDIO_STT', None)
        else:
            os.environ['CLAUDIO_STT'] = old
    info, pic, aud, sp = idx['info'], idx['picture'], idx['audio'], idx['speech']
    dur = float(info['duration'])
    cuts = [c for c in pic.get('cuts_sec') or [] if 0.1 < c < dur - 0.1]
    shots = np.diff([0.0] + cuts + [dur])
    segs = [s for s in sp.get('segments') or [] if s.get('text', '').strip()]
    words = [w for w in core.load_words(idx) or [] if str(w.get('w', '')).strip()]   # a segment may start at 0 s over music
    lufs, peak = _loudness(path)
    m = {'video': info['name'], 'path': info['nle_path'], 'w': info['width'], 'h': info['height'], 'fps': info['fps'],
         'duracao_s': round(dur, 2), 'planos': len(shots), 'corte_a_cada_s': round(float(np.median(shots)), 2),
         'fala_pct': round(100 * float(aud.get('speech_ratio') or 0)),
         'primeira_fala_s': round(words[0]['s'], 2) if words else (round(segs[0]['s'], 2) if segs else None),
         'vinheta_final_s': round(dur - (words[-1]['e'] if words else segs[-1]['e']), 2) if (words or segs) else None,
         'musica_de_fundo': (aud.get('noise_floor_db') or -99) > -42 and dur > 5,
         'lufs': lufs, 'true_peak_db': peak, 'legenda': captions(path, dur), 'rosto_pct': face_share(path, info)}
    return m


# ───────────────────────────── many videos → profile + recipe ─────────────────────────────
def videos_in(folder, limit=12):
    """The finished videos of a folder (and its subfolders), newest first — the current style — up to limit."""
    folder = core.local_path(folder)
    vs = [p for p in glob.glob(os.path.join(folder, '**', '*'), recursive=True)
          if p.lower().endswith(VIDEO_EXT) and not os.path.basename(p).startswith('.')
          and not any(part.lower() in core.PROXY_DIRS or part.startswith('_') for part in p[len(folder):].split(os.sep))]
    return sorted(vs, key=os.path.getmtime, reverse=True)[:limit]


def _stats(vals):
    v = sorted(x for x in vals if x is not None)
    if not v:
        return None
    q = lambda p: v[min(len(v) - 1, int(round(p * (len(v) - 1))))]
    return {'p10': round(q(.1), 2), 'mediana': round(statistics.median(v), 2), 'p90': round(q(.9), 2)}


def aggregate(ms):
    caps = [m['legenda'] for m in ms if m['legenda']]
    vert = sum(m['h'] > m['w'] for m in ms)
    fmt = max({(m['w'], m['h']) for m in ms}, key=lambda s: sum((m['w'], m['h']) == s for m in ms))
    return {'videos': len(ms), 'formato': f'{fmt[0]}x{fmt[1]} ({round(100 * sum((m["w"], m["h"]) == fmt for m in ms) / len(ms))}%)',
            'vertical': vert * 2 > len(ms), 'aspect': '9:16' if vert * 2 > len(ms) else '16:9',
            **{k: _stats([m[k] for m in ms]) for k in ('duracao_s', 'corte_a_cada_s', 'fala_pct', 'primeira_fala_s',
                                                      'vinheta_final_s', 'rosto_pct', 'lufs', 'true_peak_db')},
            'musica_de_fundo_pct': round(100 * sum(m['musica_de_fundo'] for m in ms) / len(ms)),
            'legenda': {'com_legenda_pct': round(100 * len(caps) / len(ms)),
                        'bloco_s': _stats([c['bloco_s'] for c in caps]), 'primeira_s': _stats([c['primeira_s'] for c in caps]),
                        'cobertura_pct': _stats([c['cobertura_pct'] for c in caps]),
                        'faixa_y': _stats([c['faixa_y'] for c in caps]), 'obs': 'estimativa sem OCR'} if caps else None}


def base_for(agg):
    """The built-in recipe closest to what was measured."""
    talk = (agg['fala_pct'] or {}).get('mediana', 0) >= 35
    long = (agg['duracao_s'] or {}).get('mediana', 0) > 600
    if talk:
        return 'podcast' if long and not agg['vertical'] else ('depoimento' if agg['vertical'] else 'depoimento-horizontal')
    return 'beat-edit'


def _med(agg, k, default=None):
    return (agg.get(k) or {}).get('mediana', default)


def draft(client, agg, base, sources):
    """The client's recipe: the measured numbers on top, the base recipe's brief/steps/rules below to be adjusted."""
    meta, body = recipes.parse(open(recipes.find(base)[0], encoding='utf-8').read())
    slug = recipes._key(client)
    shot = _med(agg, 'corte_a_cada_s', 4)
    pad = {**(meta.get('padroes') or {}), 'aspect': agg['aspect'], 'duracao': round(_med(agg, 'duracao_s', 0)),
           'pace': 'fast' if shot < 3 else 'medium' if shot < 6 else 'calm'}
    if agg['legenda'] and agg['legenda']['com_legenda_pct'] >= 50:
        pad['legendas'] = 'queimada, blocos de ~%ss' % ((agg['legenda']['bloco_s'] or {}).get('mediana', '?'))
    pad['musica'] = 'trilha sob a fala' if agg['musica_de_fundo_pct'] >= 50 else 'sem trilha'
    ver = {'plataforma': 'reels' if agg['vertical'] else 'youtube'}
    first = agg.get('primeira_fala_s')
    if first and _med(agg, 'fala_pct', 0) >= 35:
        ver['gancho_s'] = round(max(0.5, first['p90'] + 0.3), 1)
    js = lambda o: __import__('json').dumps(o, ensure_ascii=False)
    br = lambda x: f'{x:.1f}'.replace('.', ',').replace(',0', '') if isinstance(x, (int, float)) else str(x)
    rng = lambda k, u='': (f"{br(agg[k]['p10'])}–{br(agg[k]['p90'])}{u} (mediana {br(agg[k]['mediana'])}{u})" if agg.get(k) else '—')
    cap = agg['legenda']
    today = datetime.date.today().isoformat()
    head = (f"---\nnome: {slug}\ntitulo: {client} — {meta.get('titulo', base)}\n"
            f"quando: trabalhos do cliente {client} (aprendida de {agg['videos']} vídeos prontos em {today}; base: {base})\n"
            f"padroes: {js(pad)}\nverificar: {js(ver)}\nentregas: {js(meta.get('entregas') or [])}\n"
            f"ferramentas: {js(meta.get('ferramentas') or [])}\n---\n")
    measured = f"""## Medido nos vídeos prontos

{agg['videos']} vídeos de `{sources}`, medidos sem IA em {today}.

| | |
|---|---|
| formato | {agg['formato']} |
| duração | {rng('duracao_s', ' s')} |
| corte a cada | {rng('corte_a_cada_s', ' s')} |
| fala | {rng('fala_pct', ' %')} do tempo |
| primeira fala (gancho) | {rng('primeira_fala_s', ' s')} |
| vinheta / cartela depois da última fala | {rng('vinheta_final_s', ' s')} |
| rosto de quem fala na tela | {rng('rosto_pct', ' %')} do tempo (o resto é b-roll, cartela, gráfico) |
| trilha sob a fala (estimativa) | em {agg['musica_de_fundo_pct']} % dos vídeos |
| legenda queimada (estimativa) | {(f"em {cap['com_legenda_pct']} % dos vídeos; blocos de {br((cap['bloco_s'] or {}).get('mediana', '?'))} s; a {round(100 * (cap['faixa_y'] or {}).get('mediana', 0))} % da altura; desde {br((cap['primeira_s'] or {}).get('mediana', '?'))} s" if cap else 'não detectada')} |
| loudness | {rng('lufs', ' LUFS')}; pico {rng('true_peak_db', ' dB')} |

Números, não gosto: a estrutura (o que vem no gancho, no corpo, no fim), o tom, o que cada tipo de plano mostra e o
que o cliente espera receber vêm do modo com IA (prompt `aprender`) ou do editor — edite as seções abaixo.

"""
    note = f"*(Seções abaixo vindas da receita base `{base}`: ajuste ao cliente.)*\n\n"
    return slug, head + '\n' + measured + note + body


def learn(client, folder=None, paths=None, base=None, write_recipe=True, save_profile=True, overwrite=False, limit=12,
          progress=lambda *_: None):
    vids = [core.local_path(p) for p in paths] if paths else videos_in(folder, limit)
    if not vids:
        return {'error': f'nenhum vídeo em {folder or paths}'}
    ms, errors = [], []
    for k, v in enumerate(vids):
        progress(f'{k + 1}/{len(vids)} {os.path.basename(v)}')
        try:
            ms.append(measure(v))
        except Exception as e:
            errors.append(f'{os.path.basename(v)}: {str(e)[:120]}')
    if not ms:
        return {'error': 'nenhum vídeo medido', 'errors': errors}
    agg = aggregate(ms)
    base = base or base_for(agg)
    src = core.nle_path(folder) if folder else f'{len(vids)} arquivos'
    slug, text = draft(client, agg, base, src)
    out = {'cliente': client, 'base': base, 'medido': agg, 'videos': [m['video'] for m in ms], 'errors': errors}
    if write_recipe:
        d = recipes.user_dir(); os.makedirs(d, exist_ok=True)
        p = os.path.join(d, slug + '.md')
        if os.path.exists(p) and not overwrite:          # the editor's recipe stays; this one is a draft beside it
            stem = f'{slug}-rascunho-{datetime.date.today().isoformat()}'
            p, text = os.path.join(d, stem + '.md'), text.replace(f'nome: {slug}\n', f'nome: {stem}\n', 1)
        open(p, 'w', encoding='utf-8').write(text)
        out['receita'] = core.nle_path(p)
        out['receita_nome'] = os.path.splitext(os.path.basename(p))[0]
    if save_profile:
        from claudio import clients
        prof = clients.load(client) or {}
        upd = {'house_style': {'medido_videos': {**agg, 'fonte': src, 'quando': datetime.date.today().isoformat()}}}
        if write_recipe and not prof.get('receita'):
            upd['receita'] = out['receita_nome']
        clients.save(client, upd)
        out['perfil'] = core.nle_path(clients.path_of(client))
    return out
