"""claudio.motion — the motion-designer skill, used ON TOP of an edit that lives in Premiere/Resolve.

Everything here produces ASSETS for the editor's timeline (never the final video):
  render_overlay(code, ...)     HTML timeline written with the skill's core (layout, safe zones, Captions, EndCard,
                                easing) on a TRANSPARENT background → PNG frames with alpha → .mov with alpha (PNG codec, or ProRes 4444)
                                (Premiere and Resolve keep the alpha). Lower thirds, titles, animated callouts, end cards.
  timeline_words(nle_xml)       the words spoken in the edit, on the EDIT's clock (from the clips on the timeline)
  caption_overlay(nle_xml)      styled social captions (the skill's Captions(): chunked per sentence, word pops on the
                                spoken word, plate behind) synced to the edit → one alpha .mov over the whole timeline
  captions_srt(nle_xml)         the same captions as an .srt — editable in Premiere's caption track / Resolve subtitles
  narration(text, ...)          ElevenLabs (skill's el_tts.py: one take, word timings, credit budget) or edge-tts
                                → narration.wav + words.json to place on an audio track
  reference(topic)              the skill's craft rules (readability, safe zones, captions, narration…) to follow

The skill is looked up in $CLAUDIO_MOTION_SKILL or ~/.claude/skills/motion-designer.
"""
import json, math, os, pathlib, re, shutil, subprocess, sys, tempfile, time
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction

from claudio import core

SIZES = {'16x9': (1920, 1080), '1x1': (1080, 1080), '4x5': (1080, 1350), '9x16': (1080, 1920)}


def skill_dir():
    d = core.env('MOTION_SKILL') or os.path.expanduser('~/.claude/skills/motion-designer')
    if not os.path.isfile(os.path.join(d, 'templates', 'timeline.html')):
        raise FileNotFoundError(f'motion-designer skill not found at {d} (set CLAUDIO_MOTION_SKILL)')
    return d


def _core_html():
    """The template's core (everything before the demo TIMELINE section), made transparent."""
    t = open(os.path.join(skill_dir(), 'templates', 'timeline.html'), encoding='utf-8').read()
    cut = t.index('// ================================================================= TIMELINE')
    head = t[:cut]
    head = head.replace(':root{--bg:#F7F7F5;', ':root{--bg:transparent;')
    head = head.replace('html,body{margin:0;padding:0;background:var(--bg);overflow:hidden}',
                        'html,body{margin:0;padding:0;background:transparent!important;overflow:hidden}')
    return head


BOOT = """
window.seek=seek;
// fonts/images get 10 s at most: a font that never loads must not block the render
Promise.race([new Promise(r=>setTimeout(r,10000)), Promise.all([document.fonts.ready,
  ...['400','500','600','700','800'].map(w=>document.fonts.load(`${w} 48px Inter`)),
  ...[...document.images].map(i=>i.complete?1:new Promise(r=>i.onload=i.onerror=r))])]).then(()=>{
  seek(+Q.get('t')||0); window.ready=true;
  if(Q.get('play')){const t0=performance.now();const loop=()=>{seek(((performance.now()-t0)/1000)%DUR);requestAnimationFrame(loop)};loop();}
});
</script></body></html>
"""

CODE_DOC = """Write ONLY the program part (the core is provided): set `const DUR=<seconds>;` (and optional `const MARK={...}`),
build elements with div()/HL()/scene()/Captions()/EndCard(), and define `function seek(t){...}` as a PURE function of t.
Available from the core: W,H,AR, SB (safe box {x,y,w,h,cx,cy}), ZONES {HEAD,VIS,CAP}, LY (per-format layout), E (easings),
seg(t,a,b), clamp, lerp, spr (spring), hlA(headline,t,in,out), fade(el,t,in,out), T(el,{x,y,s,r,o}), Captions(parent,WORDS,
{max,maxChars,fs}), EndCard(stage,{...}). Background is transparent: draw only what overlays the footage. Brand tokens: set
document.documentElement.style.setProperty('--accent','#...') etc. Text must stay inside SB (platform safe zones)."""


def build_html(code, out_dir, fonts_css=None):
    os.makedirs(out_dir, exist_ok=True)
    html = _core_html()
    if fonts_css:
        html = html.replace('</style>', fonts_css + '\n</style>', 1)
    html += code.strip() + '\nwindow.MARK=(typeof MARK!=="undefined")?MARK:{};window.DUR=DUR;\n' + BOOT
    p = os.path.join(out_dir, 'index.html')
    open(p, 'w', encoding='utf-8').write(html)
    lib = os.path.join(skill_dir(), 'lib')
    if os.path.isdir(lib) and not os.path.exists(os.path.join(out_dir, 'lib')):
        try:
            os.symlink(lib, os.path.join(out_dir, 'lib'))
        except OSError:
            pass
    return p


def _size(ar):
    if ar in SIZES:
        return SIZES[ar]
    m = re.match(r'^(\d+)x(\d+)$', ar or '')
    if m:
        return int(m.group(1)), int(m.group(2))
    raise ValueError(f'aspect must be one of {list(SIZES)} or WxH')


def render_frames(html, ar, fps, start, end, frames_dir, workers=3, progress=lambda *_: None):
    """PNG frames with alpha (omit_background) of [start, end) at fps. Returns the frame count."""
    from playwright.sync_api import sync_playwright
    W, H = _size(ar)
    fps = float(Fraction(str(fps)))
    n = int(round((end - start) * fps))
    os.makedirs(frames_dir, exist_ok=True)
    # contiguous chunks per worker, so each one can tell when a frame is identical to its previous one
    step = -(-n // workers) if n else 1
    parts = [list(range(k, min(n, k + step))) for k in range(0, n, step)]
    done = [0]

    def work(idxs):
        with sync_playwright() as p:
            b = p.chromium.launch(args=['--allow-file-access-from-files'])
            pg = b.new_page(viewport={'width': W, 'height': H}, device_scale_factor=1)
            errs = []
            pg.on('pageerror', lambda e: errs.append(str(e)))
            pg.on('console', lambda m: errs.append(m.text) if m.type == 'error' else None)
            # as_uri() percent-encodes the path: a raw '#' (the editor's #projeto folder) would start a URL fragment,
            # the page would never load and the render waited until the timeout
            pg.goto(pathlib.Path(os.path.abspath(html)).as_uri() + f'?ar={ar}')
            try:
                pg.wait_for_function('window.ready===true', timeout=45000)
            except Exception:
                b.close()
                raise RuntimeError('overlay page never became ready' + (': ' + ' | '.join(errs[:5]) if errs else
                                   ' (no JS error; check fonts/network)') + f' — {html}')
            stage = pg.locator('#stage')
            prev_sig = prev_png = None
            for i in idxs:
                t = start + i / fps
                # seek() only sets inline styles/text, so the stage markup is the frame: an unchanged markup (a caption
                # holding still, an empty stretch) is the same picture — link the previous PNG instead of shooting again
                sig = pg.evaluate(f'(window.seek({t:.6f}), document.getElementById("stage").innerHTML)')
                png = os.path.join(frames_dir, f'{i:06d}.png')
                if sig == prev_sig and prev_png:
                    try:
                        os.link(prev_png, png)
                    except OSError:
                        shutil.copyfile(prev_png, png)
                else:
                    stage.screenshot(path=png, omit_background=True, animations='disabled')
                    prev_sig, prev_png = sig, png
                done[0] += 1
                if done[0] % 15 == 0:
                    progress(done[0] / max(n, 1))
            b.close()
    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(work, [p for p in parts if p]))
    return n


def encode_prores4444(frames_dir, fps, out):
    f = Fraction(str(fps)).limit_denominator(1001)
    core.run(['ffmpeg', '-v', 'error', '-y', '-framerate', f'{f.numerator}/{f.denominator}', '-i', os.path.join(frames_dir, '%06d.png'),
              '-c:v', 'prores_ks', '-profile:v', '4444', '-pix_fmt', 'yuva444p10le', '-alpha_bits', '16', '-vendor', 'apl0', out])
    return out


def encode_png_mov(frames_dir, fps, out):
    """The PNG frames muxed as-is into a QuickTime .mov (PNG codec, RGBA): no re-encode, so seconds instead of the
    many minutes ProRes 4444 takes at 4K, and a far smaller file (overlays are mostly transparent). Premiere and Resolve
    read PNG-in-MOV with its alpha."""
    f = Fraction(str(fps)).limit_denominator(1001)
    tmp = os.path.join(frames_dir, '_overlay.mov')               # mux on the Linux disk, then one sequential copy
    core.run(['ffmpeg', '-v', 'error', '-y', '-framerate', f'{f.numerator}/{f.denominator}', '-i', os.path.join(frames_dir, '%06d.png'),
              '-c:v', 'copy', '-f', 'mov', tmp])
    shutil.move(tmp, out)
    return out


CODECS = {'png': (encode_png_mov, 'PNG in MOV (RGBA, alpha)'), 'prores': (encode_prores4444, 'ProRes 4444 (yuva444p10le)')}


def _work_dir(name):
    d = os.path.join(core.CACHE, 'overlays', re.sub(r'[^A-Za-z0-9_.-]', '_', name))
    os.makedirs(d, exist_ok=True)
    return d


def render_overlay(code, name, out_dir, ar='9x16', fps='30000/1001', start=0.0, end=None, progress=lambda *_: None, codec='png'):
    """Build the HTML from the skill core + `code`, render it with alpha, write a .mov with alpha into out_dir.
    codec='png' (default: fast, small) or 'prores' (ProRes 4444, slow at 4K; for pipelines that require ProRes)."""
    out_dir = core.local_path(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    src = os.path.join(out_dir, '_src', name)
    html = build_html(code, src)
    dur = end
    if dur is None:
        m = re.search(r'const\s+DUR\s*=\s*([0-9.]+)', code)
        if not m:
            raise ValueError('code must set const DUR=<seconds>')
        dur = float(m.group(1))
    # on the Linux disk, not /tmp (tmpfs); one folder per run, so two renders of the same overlay never clobber each other
    frames = os.path.join(_work_dir(name), f'frames-{os.getpid()}-{int(time.time() * 1000)}')
    n = render_frames(html, ar, fps, start, dur, frames, progress=progress)
    out = os.path.join(out_dir, f'{name}.mov')
    enc, label = CODECS.get(codec, CODECS['png'])
    progress(0.99)
    enc(frames, fps, out)
    shutil.rmtree(frames, ignore_errors=True)
    W, H = _size(ar)
    return {'overlay': core.nle_path(out), 'html': core.nle_path(html), 'frames': n, 'duration_s': round(dur - start, 3),
            'size': f'{W}x{H}', 'fps': str(fps), 'alpha': label,
            'place_at': 'a NEW empty video track above every track that has clips (create it); never overwrite or insert onto a track that already has anything (adjustment layers, titles, B-roll) — list the tracks with the Premiere MCP first, at the timeline time this overlay starts'}


# ───────────────────────────── captions synced to the EDIT ─────────────────────────────
def timeline_words(nle_xml, tracks=('A1',)):
    """Words spoken in the edit, on the edit's clock: every audio clip on the given tracks contributes the words of its
    source range, shifted to where the clip sits on the timeline."""
    from claudio import nle_read
    tl = nle_read.read_fcp7(nle_xml)
    words = []
    for it in sorted([i for i in tl['items'] if i['track'] in tracks and i['path'] and i['enabled']], key=lambda i: i['tl_in']):
        try:
            idx = core.build_index(it['path'])
        except Exception:
            continue
        for w in core.load_words(idx):
            if w['s'] >= it['src_in'] - 0.02 and w['e'] <= it['src_out'] + 0.02:
                words.append({'w': core.clean_text(w['w']), 's': round(it['tl_in'] + w['s'] - it['src_in'], 3),
                              'e': round(it['tl_in'] + w['e'] - it['src_in'], 3)})
    words.sort(key=lambda w: w['s'])
    return {'timeline': tl['name'], 'duration': tl['duration'], 'fps': tl['fps'], 'words': words}


def captions_srt(nle_xml, out_path, max_words=4, max_chars=24, fix=None, corrected=None):
    """SRT of the edit's speech with the approved grammar corrections (chunks ≤ max_words / max_chars, never across a
    sentence end) — import as captions."""
    tw = corrected_timeline_words(nle_xml, corrected, fix)
    W = tw['words']
    chunks, cur = [], []
    for w in W:
        txt = ' '.join(x['w'] for x in cur + [w])
        if cur and (len(cur) >= max_words or len(txt) > max_chars or w['s'] - cur[-1]['e'] > 0.6):
            chunks.append(cur); cur = []
        cur.append(w)
        if re.search(r'[.?!]$', w['w']):
            chunks.append(cur); cur = []
    if cur:
        chunks.append(cur)
    def ts(t):
        t = max(t, 0); h, r = divmod(t, 3600); m, s = divmod(r, 60)
        return f'{int(h):02d}:{int(m):02d}:{int(s):02d},{int(round((s % 1) * 1000)) % 1000:03d}'
    lines = []
    for k, c in enumerate(chunks, 1):
        a = c[0]['s']; b = min(c[-1]['e'] + 0.3, chunks[k][0]['s'] - 0.04 if k < len(chunks) else c[-1]['e'] + 0.3)
        lines.append(f"{k}\n{ts(a)} --> {ts(max(b, a + 0.3))}\n{' '.join(w['w'] for w in c)}\n")
    out_path = core.local_path(out_path)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    open(out_path, 'w', encoding='utf-8-sig').write('\n'.join(lines))
    return {'srt': core.nle_path(out_path), 'blocks': len(chunks), 'words': len(W), 'timeline': tw['timeline']}


def caption_overlay(nle_xml, out_dir, ar='9x16', accent='#4ea1ff', uppercase=None, max_words=None, fix=None, progress=lambda *_: None,
                    style='social', corrected=None, position='bottom', codec='png'):
    """Captions over the whole edit in one of STYLES, with the approved grammar corrections, transparent background →
    alpha .mov to lay at 0 on a NEW empty video track above every track that has clips (create it); never overwrite or insert onto a track that already has anything (adjustment layers, titles, B-roll) — list the tracks with the Premiere MCP first."""
    tw = corrected_timeline_words(nle_xml, corrected, fix)
    W = tw['words']
    code = caption_code(W, style, accent, uppercase, max_words, position, max(tw['duration'], (W[-1]['e'] + 1) if W else 1))
    name = f'legendas_{style}_' + re.sub(r'[^A-Za-z0-9]+', '_', tw['timeline'] or 'timeline')[:40]
    fps = tw['fps']
    r = render_overlay(code, name, out_dir, ar, fps, 0.0, None, progress, codec)
    r.update(words=len(W), timeline=tw['timeline'], style=style, corrections_applied=tw.get('corrections_applied', 0),
             learned_vocabulary=tw.get('learned_vocabulary', []))
    return r


# ───────────────────────────── narration ─────────────────────────────
def narration(text, out_dir, engine='elevenlabs', voice=None, budget=None, lang='pt'):
    """Narration for the edit. elevenlabs: the skill's el_tts.py (one take, word timings, cache, hard credit budget —
    budget defaults to the text length + 10 %). edge: free edge-tts voices (pt-BR Thalita/Antonio)."""
    out_dir = core.local_path(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    sd = skill_dir()
    script = os.path.join(out_dir, 'script.txt')
    open(script, 'w', encoding='utf-8').write(text.strip() + '\n')
    if engine == 'elevenlabs':
        if not voice:
            raise ValueError('voice (ElevenLabs voice_id) is required — list voices with the skill (el_voices.py)')
        budget = budget or int(len(text) * 1.1) + 20
        r = subprocess.run([sys.executable, os.path.join(sd, 'scripts', 'el_tts.py'), script, '--voice', voice, '--budget', str(budget)],
                           capture_output=True, text=True, cwd=out_dir)
        if r.returncode:
            raise RuntimeError('el_tts failed: ' + (r.stderr or r.stdout)[-600:])
        wav = os.path.join(out_dir, 'narration.wav')
        info = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ''
    else:
        v = voice or ('pt-BR-ThalitaMultilingualNeural' if lang.startswith('pt') else 'en-US-AndrewMultilingualNeural')
        wav = os.path.join(out_dir, 'narration.wav')
        mp3 = os.path.join(out_dir, 'narration.mp3')
        core.run([sys.executable, '-m', 'edge_tts', '--voice', v, '--text', text, '--write-media', mp3])   # works inside a uv/pipx env too
        core.run(['ffmpeg', '-v', 'error', '-y', '-i', mp3, '-ar', '48000', '-ac', '2', wav])
        info = f'edge-tts {v}'
    dur = core.probe(wav)['duration']
    return {'audio': core.nle_path(wav), 'words': core.nle_path(os.path.join(out_dir, 'words.json')) if os.path.exists(os.path.join(out_dir, 'words.json')) else None,
            'duration_s': round(dur, 2), 'engine': engine, 'info': info}


def reference(topic=None):
    """The skill's craft rules (references/craft.md), whole or one section by keyword."""
    txt = open(os.path.join(skill_dir(), 'references', 'craft.md'), encoding='utf-8').read()
    if not topic:
        return txt
    secs = re.split(r'\n(?=## )', txt)
    hit = [s for s in secs if topic.lower() in s.lower()[:200] or topic.lower() in s.lower()]
    return '\n\n'.join(hit[:3]) or txt[:6000]


# ───────────────────────────── grammar review: sentences → corrections (timings preserved) ─────────────────────────────
def _fix_store(path):
    return os.path.join(core.cache_dir(core.local_path(path)), 'caption_fixes.json')


def _load_fixes(path):
    p = _fix_store(path)
    return json.load(open(p, encoding='utf-8')) if os.path.exists(p) else {}


def caption_sentences(nle_xml):
    """The edit's speech as sentences (ids s1, s2…), each with the source clip it comes from and any correction already
    approved for that sentence before (remembered per clip)."""
    from claudio import nle_read
    tl = nle_read.read_fcp7(nle_xml)
    out, k = [], 0
    for it in sorted([i for i in tl['items'] if i['track'] == 'A1' and i['path'] and i['enabled']], key=lambda i: i['tl_in']):
        try:
            ws = [w for w in core.load_words(core.build_index(it['path'])) if w['s'] >= it['src_in'] - 0.02 and w['e'] <= it['src_out'] + 0.02]
        except Exception:
            continue
        fixes = _load_fixes(it['path'])
        cur = []
        for j, w in enumerate(ws):
            cur.append(w)
            end = re.search(r'[.?!]$', w['w']) or j == len(ws) - 1 or ws[j + 1]['s'] - w['e'] > 0.8
            if end:
                k += 1
                text = core.clean_text(' '.join(x['w'] for x in cur))
                out.append({'id': f's{k}', 'text': text, 'tl_s': round(it['tl_in'] + cur[0]['s'] - it['src_in'], 3),
                            'tl_e': round(it['tl_in'] + cur[-1]['e'] - it['src_in'], 3), 'clip': os.path.basename(it['path']),
                            'remembered_correction': fixes.get(core.norm(text.replace(' ', '')))})
                cur = []
    return {'timeline': tl['name'], 'sentences': out,
            'how': 'correct ONLY grammar (agreement, regência, crase, punctuation) — keep the speaker\'s words, meaning and brand '
                   'names; pass {id: corrected_text} as `corrected` to captions_srt / caption_overlay / caption_style_preview'}


def _retime(orig, new_text):
    """Lay the corrected words onto the spoken words' timings (difflib alignment on normalised tokens)."""
    import difflib
    nw = new_text.split()
    a = [core.norm(w['w']) for w in orig]; b = [core.norm(w) for w in nw]
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == 'equal':
            out += [{'w': nw[j1 + q], 's': orig[i1 + q]['s'], 'e': orig[i1 + q]['e']} for q in range(i2 - i1)]
        elif tag in ('replace', 'insert'):
            if i2 > i1:
                s0, e0 = orig[i1]['s'], orig[i2 - 1]['e']
            else:                                   # pure insertion: borrow a sliver after the previous word
                s0 = out[-1]['e'] if out else (orig[0]['s'] if orig else 0)
                e0 = s0 + 0.12 * (j2 - j1)
            step = (e0 - s0) / max(j2 - j1, 1)
            out += [{'w': nw[j1 + q], 's': round(s0 + q * step, 3), 'e': round(s0 + (q + 1) * step, 3)} for q in range(j2 - j1)]
    return out


_TOK = re.compile(r"[\w'’-]+", re.U)


def learned_terms(old, new):
    """Spellings the editor fixed in a caption that the transcript should get right from now on: names, brands and
    misheard words ("Frusciante", "Red Hot Chili Peppers", "blog" → "vlog"). Grammar fixes are not vocabulary:
    an inflection of the same word (quantos → quanto, fiz → fizemos) or a case/punctuation change is skipped."""
    import difflib
    a, b = _TOK.findall(old), _TOK.findall(new)
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, [x.lower() for x in a], [x.lower() for x in b]).get_opcodes():
        if op not in ('replace', 'insert') or j2 - j1 > 4:
            continue
        old_s, new_s = ' '.join(a[i1:i2]), ' '.join(b[j1:j2])
        if core.norm(old_s) == core.norm(new_s) or len(new_s) < 3:
            continue
        o, n = old_s.lower(), new_s.lower()
        if ' ' not in o and ' ' not in n:
            k = len(os.path.commonprefix([o, n]))
            if k >= 2 and k >= min(len(o), len(n)) - 3:
                continue                                    # same stem, another ending: an inflection, i.e. grammar
        proper = any(w[:1].isupper() for w in b[j1:j2]) and j1 > 0
        misheard = op == 'replace' and difflib.SequenceMatcher(None, o, n).ratio() >= 0.5
        if not (proper or misheard):
            continue
        if proper:                                          # the whole name: the run of capitalised words around it
            while j1 > 1 and b[j1 - 1][:1].isupper():
                j1 -= 1
            while j2 < len(b) and b[j2][:1].isupper():
                j2 += 1
            new_s = ' '.join(b[j1:j2])
        out.append(new_s)
    return list(dict.fromkeys(out))


def _learn(clip_path, terms):
    """Add terms to the folder's learned vocabulary (_claudio/vocabulary.json): index_folder applies it to every clip."""
    if not terms:
        return
    p = os.path.join(core.data_dir(os.path.dirname(clip_path)), 'vocabulary.json')
    v = json.load(open(p, encoding='utf-8')) if os.path.exists(p) else {'terms': []}
    for t in terms:
        if t.lower() not in {x.lower() for x in v['terms']}:
            v['terms'].append(t)
    json.dump(v, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


def _vocab_file(folder):
    return os.path.join(core.data_dir(core.local_path(folder), create=False), 'vocabulary.json')


def unapplied_vocabulary(folder):
    p = _vocab_file(folder)
    if not os.path.exists(p):
        return []
    v = json.load(open(p, encoding='utf-8'))
    done = {x.lower() for x in v.get('applied', [])}
    return [t for t in v.get('terms', []) if t.lower() not in done]


def mark_vocabulary_applied(folder):
    p = _vocab_file(folder)
    v = json.load(open(p, encoding='utf-8'))
    v['applied'] = list(v.get('terms', []))
    json.dump(v, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


def folder_vocabulary(folder):
    p = os.path.join(core.data_dir(core.local_path(folder), create=False), 'vocabulary.json')
    return json.load(open(p, encoding='utf-8')).get('terms', []) if os.path.exists(p) else []


def corrected_timeline_words(nle_xml, corrected=None, fix=None, remember=True):
    """Timeline words with the approved sentence corrections applied (and remembered per source clip)."""
    sent = caption_sentences(nle_xml)['sentences']
    tw = timeline_words(nle_xml)
    words = tw['words']
    corrected = dict(corrected or {})
    for s in sent:                                  # corrections approved earlier for the same take come back by themselves
        if s['id'] not in corrected and s.get('remembered_correction'):
            corrected[s['id']] = s['remembered_correction']
    out, learned = [], []
    for s in sent:
        span = [w for w in words if s['tl_s'] - 0.005 <= w['s'] <= s['tl_e'] + 0.005]
        new = corrected.get(s['id'])
        out += _retime(span, new) if new and new.strip() != s['text'] else span
        if new and remember and new.strip() != s['text']:
            from claudio import nle_read
            tl = nle_read.read_fcp7(nle_xml)
            for it in tl['items']:
                if it['track'] == 'A1' and it['path'] and it['tl_in'] - 0.01 <= s['tl_s'] <= it['tl_out']:
                    st = _load_fixes(it['path']); st[core.norm(s['text'].replace(' ', ''))] = new.strip()
                    json.dump(st, open(_fix_store(it['path']), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
                    terms = learned_terms(s['text'], new)
                    try:
                        _learn(it['path'], terms)
                    except OSError:
                        pass
                    learned += terms
                    break
    for w in out:
        for a, b in (fix or {}).items():
            w['w'] = re.sub(re.escape(a), b, w['w'], flags=re.I)
    out.sort(key=lambda w: w['s'])
    return {**tw, 'words': out, 'corrections_applied': sum(1 for s in sent if corrected.get(s['id']) and corrected[s['id']].strip() != s['text']),
            'learned_vocabulary': sorted(set(learned))}


# ───────────────────────────── caption styles ─────────────────────────────
STYLES = {
    'social':  'Plate escura arredondada atrás das palavras; cada palavra aparece quando é falada; a palavra atual na cor de destaque (padrão da skill motion-designer)',
    'bold':    'Caixa alta, letra grossa com contorno preto, 2–3 palavras por vez, a palavra atual em destaque e com "pop" (estilo reels de alto impacto)',
    'clean':   'Texto branco com sombra suave, sem caixa, palavras surgindo com fade — discreto, deixa a imagem respirar',
    'karaoke': 'A frase inteira aparece apagada e cada palavra acende quando é falada, a atual em destaque',
    'box':     'O bloco inteiro dentro de uma caixa na cor de destaque com texto escuro, entra com "pop"',
    'minimal': 'Legenda de TV/cinema: frase inteira, sentence case, tarja preta translúcida, sem animação — bom pra 16:9 e entrevista',
}


def recommend_style(aspect, profile_captions=None):
    pref = (profile_captions or '').lower()
    if 'caixa alta' in pref or 'uppercase' in pref:
        return 'bold'
    if aspect in ('16x9',) or re.match(r'^\d+x\d+$', aspect or '') and _size(aspect)[0] > _size(aspect)[1]:
        return 'minimal'
    return 'social'


def caption_code(WORDS, style='social', accent='#4ea1ff', uppercase=None, max_words=None, position='bottom', dur=None):
    style = style if style in STYLES else 'social'
    up = (style == 'bold') if uppercase is None else uppercase
    W = [{**w, 'w': w['w'].upper() if up else w['w']} for w in WORDS]
    mx = max_words or {'bold': 3, 'minimal': 7, 'karaoke': 5}.get(style, 4)
    D = dur or ((W[-1]['e'] + 1) if W else 1)
    head = (f"document.documentElement.style.setProperty('--accent','{accent}');\nconst DUR={D:.3f};\n"
            f"const WORDS={json.dumps(W, ensure_ascii=False)};\n")
    if style == 'social':
        return head + f"const CAP=Captions(stage,WORDS,{{max:{mx}}});\nfunction seek(t){{CAP.seek(t);}}\n"
    return head + f"const STYLE='{style}', POS='{position}', MAX={mx};\n" + r"""
const _C=Captions(stage,WORDS,{max:MAX}); _C.box.style.display='none';         // reuse the skill's chunking only
const CH=_C.chunks, nextS=i=>WORDS[i+1]?WORDS[i+1].s:1e9;
const WIN=CH.map((c,k)=>{const l=c[c.length-1],nx=CH[k+1]?WORDS[CH[k+1][0]].s:1e9;
  return [WORDS[c[0]].s-.02,Math.min(nx-.03,Math.max(WORDS[l].s+.45,Math.min(WORDS[l].e+.6,nextS(l)-.05)))];});
const tall=H/W>1.2, FS0=(tall?60:48)*({bold:1.35,minimal:.72,clean:.95,karaoke:1,box:1}[STYLE]||1);
const Y=POS==='top'?ZONES.HEAD.y+ZONES.HEAD.h*.35:POS==='center'?SB.cy+SB.h*.12:ZONES.CAP.y+ZONES.CAP.h*.5;
const box=div(stage,'el','');box.dataset.role='caption';
Object.assign(box.style,{left:SB.cx+'px',top:Y+'px',transform:'translate(-50%,-50%)',display:'flex',gap:'0 .26em',flexWrap:'nowrap',
  justifyContent:'center',alignItems:'center',whiteSpace:'nowrap',fontWeight:{bold:900,minimal:600,clean:700}[STYLE]||800,
  fontSize:FS0+'px',lineHeight:1.12,color:'#fff',maxWidth:SB.w+'px'});
if(STYLE==='minimal')Object.assign(box.style,{background:'rgba(0,0,0,.58)',padding:'.18em .5em .24em',borderRadius:'6px'});
if(STYLE==='box')Object.assign(box.style,{background:'var(--accent)',color:'#111',padding:'.2em .55em .28em',borderRadius:'16px',boxShadow:'0 10px 30px rgba(0,0,0,.3)'});
const els=CH.map(c=>c.map(i=>{const s=document.createElement('span');s.textContent=WORDS[i].w;s.style.display='inline-block';
  if(STYLE==='bold'){s.style.webkitTextStroke=(FS0*.075)+'px #000';s.style.paintOrder='stroke fill';s.style.textShadow='0 6px 18px rgba(0,0,0,.45)';}
  if(STYLE==='clean'||STYLE==='karaoke')s.style.textShadow='0 2px 12px rgba(0,0,0,.65),0 0 3px rgba(0,0,0,.7)';
  return s;}));
let shown=-1;
function seek(t){const k=WIN.findIndex(([a,b])=>t>=a&&t<b);
  if(k!==shown){box.replaceChildren(...(k<0?[]:els[k]));shown=k;box.style.fontSize=FS0+'px';
    for(let f=FS0;k>=0&&box.scrollWidth>SB.w&&f>FS0*.55;f-=2)box.style.fontSize=f+'px';}
  if(k<0){box.style.opacity=0;return;}
  const [a,b]=WIN[k],fo=1-seg(t,b-.06,b);box.style.opacity=fo;
  if(STYLE==='box'||STYLE==='minimal'){const p=STYLE==='box'?E.oB(seg(t,a,a+.22)):E.o3(seg(t,a,a+.08));
    box.style.transform=`translate(-50%,-50%) scale(${STYLE==='box'?.85+.15*p:1})`;box.style.opacity=fo*clamp(p*1.5);}
  CH[k].forEach((i,j)=>{const w=WORDS[i],sp=els[k][j],on=t>=w.s-.02,nx=CH[k][j+1],cur=on&&(nx==null||t<WORDS[nx].s-.02);
    const p=E.oB(seg(t,w.s-.02,w.s+.16));
    if(STYLE==='bold'){sp.style.opacity=on?1:0;sp.style.color=cur?'var(--accent)':'#fff';sp.style.transform=`scale(${on?(cur?1+.12*(1-seg(t,w.s,w.s+.2)):1):.8})`;}
    else if(STYLE==='clean'){sp.style.opacity=on?clamp(seg(t,w.s-.02,w.s+.14)):0;}
    else if(STYLE==='karaoke'){sp.style.opacity=on?1:.45;sp.style.color=cur?'var(--accent)':'#fff';}
    else {sp.style.opacity=1;}});}
"""


def caption_style_preview(nle_xml, aspect='9x16', accent='#4ea1ff', corrected=None, fix=None, profile_captions=None):
    """One frame of the edit with each caption style on it (same moment, real footage), recommended one starred."""
    from PIL import Image, ImageDraw
    from claudio import nle_read, montage
    tw = corrected_timeline_words(nle_xml, corrected, fix, remember=False)
    W = tw['words']
    if not W:
        raise ValueError('no speech on A1 in this timeline')
    sent, cur = [], []
    for w in W:                                     # pick a moment mid-way through a sentence of ≥ 4 words
        cur.append(w)
        if re.search(r'[.?!]$', w['w']):
            sent.append(cur); cur = []
    target = next((s for s in sent if len(s) >= 4), W[:4])
    t = target[min(2, len(target) - 1)]['s'] + 0.06
    tl = nle_read.read_fcp7(nle_xml)
    vis = sorted([i for i in tl['items'] if i['track'].startswith('V') and i['path'] and i['tl_in'] <= t < i['tl_out']], key=lambda i: -int(i['track'][1:]))
    Wd, Ht = _size(aspect)
    base = Image.new('RGB', (Wd, Ht), (40, 40, 40))
    if vis:
        it = vis[0]
        info = core.light_index(it['path'])['info']
        fr = core.grab(it['path'], it['src_in'] + (t - it['tl_in']), 1600)
        ow, oh = core.oriented(info)
        sc = fr.width / ow
        a = aspect if aspect in montage.ASPECTS else '9:16'
        cx, cy, cw, ch = montage.crop_box(ow, oh, a.replace('x', ':') if a in SIZES else a)
        base = fr.crop((int(cx * sc), int(cy * sc), int((cx + cw) * sc), int((cy + ch) * sc))).resize((Wd, Ht))
    rec = recommend_style(aspect, profile_captions)
    tiles = []
    tmp = _work_dir('style_preview')
    for st in STYLES:
        html = build_html(caption_code(W, st, accent), os.path.join(tmp, st))
        fdir = os.path.join(tmp, st, 'f'); shutil.rmtree(fdir, ignore_errors=True)
        render_frames(html, aspect, 30, t, t + 1 / 30, fdir, workers=1)
        ov = Image.open(os.path.join(fdir, '000000.png')).convert('RGBA')
        c = base.convert('RGBA'); c.alpha_composite(ov)
        c = c.convert('RGB').resize((Wd * 360 // Wd, Ht * 360 // Wd))
        d = ImageDraw.Draw(c)
        lab = ('★ ' if st == rec else '') + st + (' (recomendado)' if st == rec else '')
        d.rectangle([0, 0, 360, 34], fill=(0, 0, 0)); d.text((8, 8), lab, fill=(255, 220, 0) if st == rec else (255, 255, 255), font=core._font(18))
        tiles.append(c)
    cols = 3 if Ht > Wd else 2
    th = tiles[0].height
    g = Image.new('RGB', (360 * cols, th * math.ceil(len(tiles) / cols)), (0, 0, 0))
    for k, im in enumerate(tiles):
        g.paste(im, ((k % cols) * 360, (k // cols) * th))
    return g, {'recommended': rec, 'styles': STYLES, 'moment_s': round(t, 2)}
