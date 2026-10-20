"""Captions as Motion Graphics Templates: the same styles as caption_overlay (social, bold, clean, karaoke, box, minimal,
marca and its dynamic cut), but as one EDITABLE .mogrt clip per caption block in Premiere — the editor fixes a word
or a timing right there, no re-render.

How: once per style × format, After Effects builds the template from a script (claudio-ae, run with AfterFX -r):
a hidden "TEXTO" layer (Essential Graphics control "Texto" = the block's text), a hidden "TEMPOS" layer (control
"Tempos" = when each word is spoken, relative to the block, + keyword marks + block length), and the visible caption
whose animators, box and keyword chips are expressions reading those two. The whole comp is a protected region, so
Premiere never time-stretches it. Then the panel (no AI) drops one clip per block on a NEW top video track and writes
Texto/Tempos — from the reviewed .srt when there is one, so blocks and timings are the editor's."""
import hashlib, json, os, re, subprocess, time

from claudio import core, motion

TPL_VERSION = 2
SAFE = {'16x9': (80, 110, 90, 110), '1x1': (90, 90, 90, 90), '4x5': (110, 90, 120, 90), '9x16': (260, 150, 460, 150)}   # t r b l
GENERIC_FONT = {900: 'Montserrat-Black', 800: 'Montserrat-ExtraBold', 700: 'Montserrat-Bold', 600: 'Montserrat-SemiBold'}


def layout(aspect):
    """Safe box and caption band of the motion-designer template (same numbers as the overlay)."""
    a = aspect.replace(':', 'x')
    W, H = motion.SIZES.get(a, motion.SIZES['9x16'])
    t, r, b, l = SAFE.get(a, SAFE['9x16'])
    sb = {'x': l, 'y': t, 'w': W - l - r, 'h': H - t - b}
    sb['cx'], sb['cy'] = sb['x'] + sb['w'] / 2, sb['y'] + sb['h'] / 2
    tall = H / W > 1.2
    cap, head = (190 if tall else 150), round(sb['h'] * (.25 if tall else .28))
    zones = {'HEAD': {'x': sb['x'], 'y': sb['y'], 'w': sb['w'], 'h': head},
             'CAP': {'x': sb['x'], 'y': sb['y'] + sb['h'] - cap, 'w': sb['w'], 'h': cap}}
    return W, H, sb, zones, tall


def _rgb(hexs):
    h = hexs.lstrip('#')
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    return [round(int(h[i:i + 2], 16) / 255, 4) for i in (0, 2, 4)]


def style_spec(style, aspect='9x16', accent='#4ea1ff', look=None, position='bottom'):
    """Everything the AE script needs to build one style, in pixels of the template frame."""
    W, H, sb, Z, tall = layout(aspect)
    if style == 'marca':
        L = {**motion.BRAND_DEFAULT, **(look or {}), 'pop': {**motion.BRAND_DEFAULT['pop'], **((look or {}).get('pop') or {})}}
        k = W / 1080
        dyn = bool(L.get('dynamic'))
        return {'style': 'marca_dinamica' if dyn else 'marca', 'W': W, 'H': H, 'font': L['font'], 'size': L['size'] * k,
                'color': _rgb(L['color']), 'caps': False, 'stroke': (L.get('stroke') or 0) * k, 'stroke_color': _rgb(L['color']),
                'x': W / 2, 'y': H * L['y'], 'maxw': W * .92, 'mode': 'grow' if dyn else 'fixed',
                'box': {'color': _rgb(L['bg']), 'alpha': 1, 'pad_x': L['pad_x'] * k, 'pad_y': L['pad_y'] * k, 'radius': L['radius'] * k, 'hug': 'all'},
                'appear': 'brand_dynamic' if dyn else 'none', 'current': None, 'dim': None,
                'entry': 'spring' if dyn else 'pop', 'pop': L['pop'], 'exit': 'brand' if dyn else 'cut',
                'chips': ({'bg': _rgb(L.get('emphasis_bg') or '#ffffff'), 'text': _rgb(L.get('emphasis_color') or L['bg']),
                           'pad_x': L['pad_x'] * k * .55, 'pad_y': L['pad_y'] * k * .55, 'n': 3} if dyn else None),
                'shadow': None, 'line_height': L.get('line_height', .9)}
    acc = _rgb(accent)
    if style == 'social':
        fs = 60 if tall else 48
        return {'style': 'social', 'W': W, 'H': H, 'font': GENERIC_FONT[800], 'size': fs, 'color': [1, 1, 1], 'caps': False,
                'stroke': 0, 'x': sb['cx'], 'y': Z['CAP']['y'] + Z['CAP']['h'] / 2, 'maxw': Z['CAP']['w'], 'mode': 'fixed',
                'box': {'color': [5 / 255, 8 / 255, 23 / 255], 'alpha': .62, 'pad_x': fs * .45, 'pad_y': fs * .45 * .45, 'radius': 22,
                        'hug': 'visible'},
                'appear': 'rise', 'current': acc, 'dim': None, 'entry': 'none', 'exit': 'fade', 'chips': None,
                'shadow': {'opacity': 35, 'distance': 2, 'softness': 10}, 'line_height': 1.1}
    mult = {'bold': 1.35, 'minimal': .72, 'clean': .95, 'karaoke': 1, 'box': 1}[style]
    fs = (60 if tall else 48) * mult
    weight = {'bold': 900, 'minimal': 600, 'clean': 700}.get(style, 800)
    y = (Z['HEAD']['y'] + Z['HEAD']['h'] * .35 if position == 'top' else sb['cy'] + sb['h'] * .12 if position == 'center'
         else Z['CAP']['y'] + Z['CAP']['h'] * .5)
    spec = {'style': style, 'W': W, 'H': H, 'font': GENERIC_FONT[weight], 'size': fs, 'color': [1, 1, 1], 'caps': style == 'bold',
            'stroke': fs * .075 if style == 'bold' else 0, 'stroke_color': [0, 0, 0], 'x': sb['cx'], 'y': y, 'maxw': sb['w'],
            'mode': 'fixed', 'box': None, 'appear': 'none', 'current': None, 'dim': None, 'entry': 'none', 'exit': 'fade',
            'chips': None, 'shadow': None, 'line_height': 1.12}
    if style == 'bold':
        spec.update(appear='instant', current=acc, current_pop=.12, shadow={'opacity': 45, 'distance': 6, 'softness': 18})
    elif style == 'clean':
        spec.update(appear='fade', shadow={'opacity': 65, 'distance': 2, 'softness': 12})
    elif style == 'karaoke':
        spec.update(dim=.45, current=acc, shadow={'opacity': 65, 'distance': 2, 'softness': 12})
    elif style == 'box':
        spec.update(color=_rgb('#111111'), box={'color': acc, 'alpha': 1, 'pad_x': fs * .55, 'pad_y': fs * .24, 'radius': 16, 'hug': 'all'},
                    entry='back', exit='fade')
    elif style == 'minimal':
        spec.update(box={'color': [0, 0, 0], 'alpha': .58, 'pad_x': fs * .5, 'pad_y': fs * .21, 'radius': 6, 'hug': 'all'}, entry='o3')
    return spec


def template_name(spec, aspect):
    h = hashlib.sha1(json.dumps({**spec, 'v': TPL_VERSION}, sort_keys=True).encode()).hexdigest()[:6]
    return f'Claudio legenda {spec["style"]} {aspect.replace(":", "x")} {h}'


# ───────────────────────────── the After Effects side ─────────────────────────────
_PRE = r'''var _r=String(thisComp.layer("TEMPOS").text.sourceText).split("|");
var T=_r[0].split(",").map(function(x){return parseFloat(x);}).filter(function(x){return !isNaN(x);});
var EM=(_r[1]||"").split(",").filter(function(x){return x!=="";}).map(Number);
var D=parseFloat(_r[2]||"")||30;
var WS=String(thisComp.layer("TEXTO").text.sourceText).replace(/\s+/g," ").replace(/^ | $/g,"").split(" ");
function seg(t,a,b){return Math.min(1,Math.max(0,(t-a)/(b-a)));}
function cl(x){return Math.min(1,Math.max(0,x));}
function oB(x){var c=1.70158,c3=c+1;return 1+c3*Math.pow(x-1,3)+c*Math.pow(x-1,2);}
function o3(x){return 1-Math.pow(1-x,3);}
function spr(x){return x>=1?1:1-Math.cos(x*Math.PI*1.25)*Math.exp(-x*4.2);}
function ts(i){return T[i]!==undefined?T[i]:(T.length?T[T.length-1]:0);}
function nvis(t){var n=0;for(var i=0;i<WS.length;i++){if(i===0||t>=ts(i)-0.04)n=i+1;}return n;}
'''


def _expr(body):
    return _PRE + body


def expressions(spec):
    """The expressions of one style (strings), keyed by where they go."""
    e = {}
    grow = spec['mode'] == 'grow'
    e['legenda_text'] = _expr('WS.slice(0, nvis(time)).join(" ")' if grow else 'WS.join(" ")')
    e['visivel_text'] = _expr('WS.slice(0, nvis(time)).join(" ")')
    e['medida_text'] = _expr('WS.join(" ")')
    # anchor: horizontally the centre of what is shown, vertically the full line (no jumping)
    e['anchor'] = _expr('var m=thisComp.layer("MEDIDA").sourceRectAtTime(time,false), r=thisLayer.sourceRectAtTime(time,false);'
                        '[r.left+r.width/2, m.top+m.height/2]')
    padx = (spec['box'] or {}).get('pad_x', 0)
    fit = f'var m=thisComp.layer("MEDIDA").sourceRectAtTime(time,false); var f=Math.min(1,{spec["maxw"]:.1f}/Math.max(1,m.width+{2 * padx:.1f}));'
    entry = spec['entry']
    if entry == 'pop':
        p = spec['pop']
        e['scale'] = _expr(fit + f'var p=o3(seg(time,0,{p["dur"]})); var s=f*({p["scale"]}+(1-{p["scale"]})*p)*100; [s,s]')
        e['position'] = _expr(f'var p=o3(seg(time,0,{p["dur"]})); [{spec["x"]:.1f}, {spec["y"]:.1f}+(1-p)*{p["rise"] * spec["W"] / 1080:.2f}]')
        e['opacity'] = _expr(f'var p=o3(seg(time,0,{p["dur"]})); ({p["opacity"]}+(1-{p["opacity"]})*p)*100')
    elif entry == 'spring':
        k = spec['W'] / 1080
        e['scale'] = _expr(fit + 'var pin=spr(seg(time,0,0.3)), o=seg(time,D-0.07,D); var s=f*(0.88+0.12*pin)*(1-0.08*o)*100; [s,s]')
        e['position'] = _expr(f'var pin=spr(seg(time,0,0.3)); [{spec["x"]:.1f}, {spec["y"]:.1f}+(1-pin)*{20 * k:.2f}]')
        e['opacity'] = _expr('cl(seg(time,0,0.06))*(1-seg(time,D-0.07,D))*100')
    elif entry == 'back':
        e['scale'] = _expr(fit + 'var p=oB(seg(time,0,0.22)); var s=f*(0.85+0.15*p)*100; [s,s]')
        e['position'] = f'[{spec["x"]:.1f}, {spec["y"]:.1f}]'
        e['opacity'] = _expr('var p=oB(seg(time,0,0.22)); cl(p*1.5)*(1-seg(time,D-0.06,D))*100')
    elif entry == 'o3':
        e['scale'] = _expr(fit + '[f*100,f*100]')
        e['position'] = f'[{spec["x"]:.1f}, {spec["y"]:.1f}]'
        e['opacity'] = _expr('cl(o3(seg(time,0,0.08))*1.5)*(1-seg(time,D-0.06,D))*100')
    else:
        e['scale'] = _expr(fit + '[f*100,f*100]')
        e['position'] = f'[{spec["x"]:.1f}, {spec["y"]:.1f}]'
        e['opacity'] = _expr('(1-seg(time,D-0.06,D))*100' if spec['exit'] == 'fade' else '100')
    # per-word animators (Expression Selector, based on words): amount 100 = the animator's values fully applied
    W1 = 'var i=textIndex-1, t=ts(i), on=(i===0&&' + ('true' if grow else 'false') + ')||time>=t-0.02, nx=T[i+1];'
    if spec['appear'] == 'rise':          # social: fade + rise + scale in, from the word's time
        e['sel_appear'] = _expr(W1 + 'var p=oB(seg(time,t-0.02,t+0.18)); var v=100*(1-cl(p*2.4)); [v,v,v]')
    elif spec['appear'] == 'instant':     # bold
        e['sel_appear'] = _expr(W1 + 'var v=on?0:100; [v,v,v]')
    elif spec['appear'] == 'fade':        # clean
        e['sel_appear'] = _expr(W1 + 'var v=100*(1-cl(seg(time,t-0.02,t+0.14))); [v,v,v]')
    elif spec['appear'] == 'brand_dynamic':
        e['sel_appear'] = _expr('var i=textIndex-1, t=ts(i); if(EM.indexOf(i+1)>=0){[0,0,0]}else{var p=spr(seg(time,t-0.04,t+0.26)); var v=100*(1-p); [v,v,v]}')
    if spec['current']:
        pop = spec.get('current_pop')
        e['sel_current'] = _expr(W1 + 'var cur=on&&(nx===undefined||time<nx-0.02); var v=cur?100:0; [v,v,v]')
        if pop:
            e['sel_current_pop'] = _expr(W1 + f'var cur=on&&(nx===undefined||time<nx-0.02); var v=cur?100*(1-seg(time,t,t+0.2)):0; [v,v,v]')
    if spec['dim']:
        e['sel_dim'] = _expr(W1 + 'var v=on?0:100; [v,v,v]')
    if spec['chips']:
        e['sel_em_color'] = _expr('var i=textIndex-1, t=ts(i); var v=(EM.indexOf(i+1)>=0&&time>=t-0.04)?100:0; [v,v,v]')
        e['sel_em_pop'] = _expr('var i=textIndex-1, t=ts(i); var v=0; if(EM.indexOf(i+1)>=0&&time>=t-0.04){var p=spr(seg(time,t-0.04,t+0.26)); v=100*(1-p);} [v,v,v]')
    return e


def chip_expressions(spec, k):
    """Chip k (1-based): the k-th keyword of the block, measured with two hidden left-aligned layers."""
    c = spec['chips']
    word = _expr(f'var e=EM[{k - 1}]; (e&&e<=nvis(time))?WS[e-1]:""')
    inc = _expr(f'var e=EM[{k - 1}]; (e&&e<=nvis(time))?WS.slice(0,e).join(" "):""')
    geo = (f'var e=EM[{k - 1}], L=thisComp.layer("LEGENDA").sourceRectAtTime(time,false), m=thisComp.layer("MEDIDA").sourceRectAtTime(time,false);'
           f'var a=thisComp.layer("INC{k}").sourceRectAtTime(time,false), w=thisComp.layer("PAL{k}").sourceRectAtTime(time,false);'
           f'var x1=L.left+a.left+a.width, x0=x1-w.width;')
    size = _expr(geo + f'(e&&e<=nvis(time))?[w.width+{2 * c["pad_x"]:.1f}, m.height+{2 * c["pad_y"]:.1f}]:[0,0]')
    pos = _expr(geo + '[(x0+x1)/2, m.top+m.height/2]')
    sc = _expr(f'var e=EM[{k - 1}], t=e?ts(e-1):0; var p=spr(seg(time,t-0.04,t+0.26)); var s=(0.7+0.3*p+0.06*Math.max(0,1-seg(time,t,t+0.5)))*100; [s,s]')
    rot = _expr(f'var e=EM[{k - 1}], t=e?ts(e-1):0; -3*(1-seg(time,t,t+0.45))')
    return {'word': word, 'inc': inc, 'size': size, 'pos': pos, 'scale': sc, 'rot': rot}


def box_expressions(spec):
    """The box lives in LEGENDA's own layer space (parented to it): around the whole line, or — social — only around
    the words already spoken (measured by the hidden left-aligned VISIVEL layer), the line itself never moving."""
    b = spec['box']
    head = ('var L=thisComp.layer("LEGENDA").sourceRectAtTime(time,false), m=thisComp.layer("MEDIDA").sourceRectAtTime(time,false);'
            + ('var v=thisComp.layer("VISIVEL").sourceRectAtTime(time,false), w=v.width;' if b['hug'] == 'visible' else 'var w=L.width;'))
    size = _expr(head + f'[w+{2 * b["pad_x"]:.1f}, m.height+{2 * b["pad_y"]:.1f}]')
    pos = _expr(head + '[L.left+w/2, m.top+m.height/2]')
    return {'size': size, 'pos': pos}


def jsx(specs, out_dir, result_path):
    """ExtendScript (ES3) that builds every template in `specs` and exports it to out_dir as <name>.mogrt."""
    data = []
    for name, sp in specs:
        ex = expressions(sp)
        data.append({'name': name, 'spec': sp, 'ex': ex, 'box': box_expressions(sp) if sp['box'] else None,
                     'chips': [chip_expressions(sp, k) for k in range(1, (sp['chips'] or {}).get('n', 0) + 1)]})
    return ('var CLAUDIO_JOBS = ' + json.dumps(data, ensure_ascii=False) + ';\n'
            f'var CLAUDIO_OUT = {json.dumps(core.nle_path(out_dir))};\n'
            f'var CLAUDIO_RESULT = {json.dumps(core.nle_path(result_path))};\n'
            + open(os.path.join(os.path.dirname(__file__), 'ae', 'captions.jsx'), encoding='utf-8').read())


def _afterfx():
    for base in ('/mnt/c/Program Files/Adobe',):
        try:
            for d in sorted(os.listdir(base), reverse=True):
                exe = os.path.join(base, d, 'Support Files', 'AfterFX.exe')
                if d.startswith('Adobe After Effects') and os.path.exists(exe):
                    return exe
        except OSError:
            pass
    return core.env('AFTERFX')


def ensure_templates(styles, aspect='9x16', accent='#4ea1ff', look=None, position='bottom', out_dir=None, timeout=420):
    """{style: .mogrt path}, building in After Effects only the ones not built yet (cached by their spec)."""
    out_dir = core.local_path(out_dir or os.path.join(core.env('MOGRT_DIR') or 'D:\\Documentos\\claudio-ae', 'templates'))
    os.makedirs(out_dir, exist_ok=True)
    todo, have = [], {}
    for st in styles:
        sp = style_spec(st, aspect, accent, look, position)
        name = template_name(sp, aspect)
        p = os.path.join(out_dir, name + '.mogrt')
        have[st] = p
        if not os.path.isfile(p):
            todo.append((name, sp))
    if todo:
        exe = _afterfx()
        if not exe:
            raise RuntimeError('After Effects não encontrado (instale pelo Creative Cloud ou defina CLAUDIO_AFTERFX)')
        res = os.path.join(out_dir, f'_resultado_{int(time.time())}.json')
        script = os.path.join(out_dir, '_claudio_captions.jsx')
        open(script, 'w', encoding='utf-8').write(jsx(todo, out_dir, res))
        subprocess.Popen([exe, '-r', core.nle_path(script)], cwd='/mnt/c', stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        t0 = time.time()
        while time.time() - t0 < timeout and not os.path.exists(res):
            time.sleep(1.5)
        if not os.path.exists(res):
            raise TimeoutError('o After Effects não terminou em ' + str(timeout) + ' s — veja se há alguma janela esperando clique no AE')
        r = json.load(open(res, encoding='utf-8'))
        errs = [x for x in r.get('templates', []) if not x.get('ok')]
        if errs:
            raise RuntimeError('After Effects: ' + '; '.join(f'{x.get("name")}: {x.get("error")}' for x in errs))
    missing = [s for s, p in have.items() if not os.path.isfile(p)]
    if missing:
        raise RuntimeError('MOGRT não gerado: ' + ', '.join(missing))
    return have


# ───────────────────────────── the blocks ─────────────────────────────
def blocks(words, style='social', look=None, cues=None, max_words=None, dur=None):
    """[{start, end, text, tempos}] — exactly the blocks and windows the overlay would show (same chunker), each
    word's time relative to its block, keyword marks for the dynamic brand style."""
    from playwright.sync_api import sync_playwright
    st = 'marca' if style.startswith('marca') else style
    W = motion.mark_emphasis(words, (look or {}).get('emphasis_words') or ()) if st == 'marca' else words
    code = motion.caption_code(W, st, '#ffffff', False, max_words, 'bottom', dur or ((W[-1]['e'] + 1) if W else 1), look, cues=cues)
    tmp = motion._work_dir('mogrt_blocks')
    html = motion.build_html(code, tmp)
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={'width': 540, 'height': 960})
        pg.goto('file://' + html + '?t=0')
        pg.wait_for_function('window.ready===true', timeout=30000)
        got = pg.evaluate('''() => ({ch: (typeof CH!=='undefined') ? CH : CAP.chunks, win: (typeof WIN!=='undefined') ? WIN : null})''')
        b.close()
    ch, win = got['ch'], got['win']
    if win is None:                                    # social: the skill's Captions() keeps its windows inside
        nxt = lambda i: W[i + 1]['s'] if i + 1 < len(W) else 1e9
        win = []
        for k, c in enumerate(ch):
            l = c[-1]
            nxc = W[ch[k + 1][0]]['s'] if k + 1 < len(ch) else 1e9
            win.append([W[c[0]]['s'] - .02, min(nxc - .03, max(W[l]['s'] + .45, min(W[l]['e'] + .6, nxt(l) - .05)))])
        if cues:
            win = [[c[2] - .001, c[3]] for c in cues]
    out = []
    for c, (a, b) in zip(ch, win):
        ws = [W[i] for i in c]
        rel = [max(0.0, round(w['s'] - a, 3)) for w in ws]
        em = [str(j + 1) for j, w in enumerate(ws) if w.get('em')]
        out.append({'start': round(a, 3), 'end': round(b, 3), 'text': ' '.join(w['w'] for w in ws),
                    'tempos': ','.join(f'{x:g}' for x in rel) + '|' + ','.join(em) + '|' + f'{b - a:.3f}'})
    return out


def caption_mogrt(nle_xml, style='social', aspect=None, accent='#4ea1ff', srt=None, corrected=None, fix=None, look=None,
                  position='bottom', out_dir=None, max_words=None):
    """Template(s) built if needed + the placement plan the Premiere panel applies (one .mogrt clip per block)."""
    from claudio import nle_read
    tl = nle_read.read_fcp7(nle_xml)
    if not aspect:
        root, top, *_ = nle_read.flat_timeline(nle_xml)
        w = int(top.findtext('media/video/format/samplecharacteristics/width') or 1080)
        h = int(top.findtext('media/video/format/samplecharacteristics/height') or 1920)
        r = w / h
        aspect = min(motion.SIZES, key=lambda a: abs(motion.SIZES[a][0] / motion.SIZES[a][1] - r))
    tw = motion.srt_timeline_words(nle_xml, srt, fix) if srt else motion.corrected_timeline_words(nle_xml, corrected, fix)
    if style == 'marca' and not look:
        return {'error': 'estilo marca precisa do caption_look do cliente'}
    tpl = ensure_templates([style], aspect, accent, look, position)[style]      # the shared template library, not the job
    bl = blocks(tw['words'], style, look, tw.get('cues'), max_words, tw['duration'])
    out_dir = core.local_path(out_dir) if out_dir else os.path.dirname(core.local_path(nle_xml))
    plan_path = os.path.join(out_dir, re.sub(r'[\\/:*?"<>|]', '-', f'legendas {style} {tl["name"] or "timeline"}') + '.claudio-mogrt.jsx')
    plan = {'mogrt': core.nle_path(tpl), 'sequence': tl['name'], 'style': style, 'blocks': bl,
            'texto': 'Texto', 'tempos': 'Tempos'}
    open(plan_path, 'w', encoding='utf-8').write('var CLAUDIO_PLAN = ' + json.dumps(plan, ensure_ascii=False) + ';\n')
    return {'plan': core.nle_path(plan_path), 'mogrt': core.nle_path(tpl), 'blocks': len(bl), 'style': style, 'aspect': aspect,
            'from_srt': bool(srt), 'timeline': tl['name'],
            'next': 'no painel do Cláudio, botão "Aplicar legendas (MOGRT)" — põe um clipe por bloco numa trilha de vídeo nova no topo'}
