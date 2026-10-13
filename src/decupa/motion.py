"""decupa.motion — the motion-designer skill, used ON TOP of an edit that lives in Premiere/Resolve.

Everything here produces ASSETS for the editor's timeline (never the final video):
  render_overlay(code, ...)     HTML timeline written with the skill's core (layout, safe zones, Captions, EndCard,
                                easing) on a TRANSPARENT background → PNG frames with alpha → ProRes 4444 .mov
                                (Premiere and Resolve keep the alpha). Lower thirds, titles, animated callouts, end cards.
  timeline_words(nle_xml)       the words spoken in the edit, on the EDIT's clock (from the clips on the timeline)
  caption_overlay(nle_xml)      styled social captions (the skill's Captions(): chunked per sentence, word pops on the
                                spoken word, plate behind) synced to the edit → ProRes 4444 over the whole timeline
  captions_srt(nle_xml)         the same captions as an .srt — editable in Premiere's caption track / Resolve subtitles
  narration(text, ...)          ElevenLabs (skill's el_tts.py: one take, word timings, credit budget) or edge-tts
                                → narration.wav + words.json to place on an audio track
  reference(topic)              the skill's craft rules (readability, safe zones, captions, narration…) to follow

The skill is looked up in $DECUPA_MOTION_SKILL or ~/.claude/skills/motion-designer.
"""
import json, math, os, re, shutil, subprocess, sys, tempfile
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction

from decupa import core

SIZES = {'16x9': (1920, 1080), '1x1': (1080, 1080), '4x5': (1080, 1350), '9x16': (1080, 1920)}


def skill_dir():
    d = os.environ.get('DECUPA_MOTION_SKILL') or os.path.expanduser('~/.claude/skills/motion-designer')
    if not os.path.isfile(os.path.join(d, 'templates', 'timeline.html')):
        raise FileNotFoundError(f'motion-designer skill not found at {d} (set DECUPA_MOTION_SKILL)')
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
Promise.all([document.fonts.ready,
  ...['400','500','600','700','800'].map(w=>document.fonts.load(`${w} 48px Inter`)),
  ...[...document.images].map(i=>i.complete?1:new Promise(r=>i.onload=i.onerror=r))]).then(()=>{
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
    parts = [list(range(k, n, workers)) for k in range(workers)]
    done = [0]

    def work(idxs):
        with sync_playwright() as p:
            b = p.chromium.launch(args=['--allow-file-access-from-files'])
            pg = b.new_page(viewport={'width': W, 'height': H}, device_scale_factor=1)
            pg.goto('file://' + os.path.abspath(html) + f'?ar={ar}')
            pg.wait_for_function('window.ready===true', timeout=120000)
            stage = pg.locator('#stage')
            for i in idxs:
                t = start + i / fps
                pg.evaluate(f'window.seek({t:.6f})')
                stage.screenshot(path=os.path.join(frames_dir, f'{i:06d}.png'), omit_background=True, animations='disabled')
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


def _work_dir(name):
    d = os.path.join(core.CACHE, 'overlays', re.sub(r'[^A-Za-z0-9_.-]', '_', name))
    os.makedirs(d, exist_ok=True)
    return d


def render_overlay(code, name, out_dir, ar='9x16', fps='30000/1001', start=0.0, end=None, progress=lambda *_: None):
    """Build the HTML from the skill core + `code`, render it with alpha, encode ProRes 4444 into out_dir."""
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
    frames = os.path.join(_work_dir(name), 'frames')          # on the Linux disk, not /tmp (tmpfs)
    shutil.rmtree(frames, ignore_errors=True)
    n = render_frames(html, ar, fps, start, dur, frames, progress=progress)
    out = os.path.join(out_dir, f'{name}.mov')
    encode_prores4444(frames, fps, out)
    shutil.rmtree(frames, ignore_errors=True)
    W, H = _size(ar)
    return {'overlay': core.nle_path(out), 'html': core.nle_path(html), 'frames': n, 'duration_s': round(dur - start, 3),
            'size': f'{W}x{H}', 'fps': str(fps), 'alpha': 'ProRes 4444 (yuva444p10le)',
            'place_at': 'top video track, at the timeline time this overlay starts'}


# ───────────────────────────── captions synced to the EDIT ─────────────────────────────
def timeline_words(nle_xml, tracks=('A1',)):
    """Words spoken in the edit, on the edit's clock: every audio clip on the given tracks contributes the words of its
    source range, shifted to where the clip sits on the timeline."""
    from decupa import nle_read
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


def captions_srt(nle_xml, out_path, max_words=4, max_chars=24, fix=None):
    """SRT of the edit's speech (chunks ≤ max_words / max_chars, never across a sentence end) — import as captions."""
    tw = timeline_words(nle_xml)
    W = tw['words']
    for w in W:
        for a, b in (fix or {}).items():
            w['w'] = re.sub(re.escape(a), b, w['w'], flags=re.I)
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


def caption_overlay(nle_xml, out_dir, ar='9x16', accent='#4ea1ff', uppercase=False, max_words=4, fix=None, progress=lambda *_: None):
    """The skill's Captions() over the whole edit, transparent background → ProRes 4444 to lay on the top track at 0."""
    tw = timeline_words(nle_xml)
    W = []
    for w in tw['words']:
        t = w['w']
        for a, b in (fix or {}).items():
            t = re.sub(re.escape(a), b, t, flags=re.I)
        W.append({'w': t.upper() if uppercase else t, 's': w['s'], 'e': w['e']})
    code = (f"document.documentElement.style.setProperty('--accent','{accent}');\n"
            f"const DUR={max(tw['duration'], (W[-1]['e'] + 1) if W else 1):.3f};\n"
            f"const WORDS={json.dumps(W, ensure_ascii=False)};\n"
            f"const CAP=Captions(stage,WORDS,{{max:{max_words}}});\n"
            "function seek(t){CAP.seek(t);}\n")
    name = 'legendas_' + re.sub(r'[^A-Za-z0-9]+', '_', tw['timeline'] or 'timeline')[:40]
    fps = tw['fps']
    r = render_overlay(code, name, out_dir, ar, fps, 0.0, None, progress)
    r.update(words=len(W), timeline=tw['timeline'])
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
        core.run(['edge-tts', '--voice', v, '--text', text, '--write-media', mp3])
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
