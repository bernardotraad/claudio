"""Caption blocks cut and timed the way a subtitler does it, not every N words.

Blocks: per sentence, dynamic programming over the break points between words. A break is good after punctuation, at
a pause, before a clause ("que", "mas", "porque", "com"…) and bad after a word that leans on the next one (article,
preposition, conjunction, clitic, auxiliary, "não") or inside a spoken number ("vinte e quatro", "R$ 750"). A block
fills its line(s) without running over, a lone word only stands when it is the whole sentence, and the reading speed
stays under ~17 characters per second (Netflix PT-BR).
Timing, on the sequence's frame grid: a block comes in on its first word's voice and goes out after its last word
(+hold), held at least ~0.83 s and long enough to be read; blocks a short gap apart are chained (2 frames between),
and an in/out a few frames from a picture cut goes onto the cut — no caption flashing across a shot change.
Lyrics are not cut here (motion.lyric_chunks: one sung line per block)."""
import re
from fractions import Fraction

from claudio import core

# words that lean on the next one: a block never ends on them ("cresce na cidade de / Juiz de Fora")
_GLUE = {
    3.0: 'o a os as um uma uns umas de do da dos das em no na nos nas num numa dum duma por pelo pela pelos pelas para '
         'pra pro pras pros com sem sob sobre até ao aos à às entre contra desde após '
         'e ou mas que se porque pois quando como onde nem '
         'me te lhe nos vos não',
    2.0: 'meu minha meus minhas teu tua teus tuas seu sua seus suas nosso nossa nossos nossas '
         'esse essa esses essas este esta estes estas aquele aquela aqueles aquelas eu tu '
         'é são foi era vai vou vamos vão pode posso podem quer quero está estou estão estava tem tenho têm ter '
         'ser estar fazer muito muita muitos muitas mais menos tão',
    1.0: 'você vocês ele ela eles elas nós já ainda só bem também sempre nunca',
}
GLUE = {w: p for p, ws in _GLUE.items() for w in ws.split()}
# words that open a clause: a good place for the next block to start
OPENERS = set('que mas porque pois quando onde enquanto então como se ou e nem para pra com sem'.split())
NUMBER_WORDS = set(('zero um uma dois duas três quatro cinco seis sete oito nove dez onze doze treze catorze quatorze '
                    'quinze dezesseis dezessete dezoito dezenove vinte trinta quarenta cinquenta sessenta setenta oitenta '
                    'noventa cem cento duzentos duzentas trezentos quatrocentos quinhentos seiscentos setecentos '
                    'oitocentos novecentos mil milhão milhões bilhão bilhões reais real centavos vezes '
                    'por cento metros m² mês meses anos dias horas').split())

CPS = 17            # reading speed ceiling (characters per second)
MIN_DUR = 5 / 6     # 20 frames at 24 fps
MAX_DUR = 7.0
HOLD = 0.25         # a block stays this long after its last word…
CHAIN = 0.5         # …and runs on to the next one when the gap is shorter than this
GAP_FRAMES = 2      # frames between chained blocks
SNAP_FRAMES = 8     # an in/out this close to a picture cut goes onto it

# per caption style: (characters per line, lines) — vertical / horizontal
STYLE_LIMITS = {
    'social':  ((26, 1), (32, 1)),
    'bold':    ((18, 1), (22, 1)),
    'clean':   ((30, 1), (40, 1)),
    'box':     ((24, 1), (30, 1)),
    'karaoke': ((28, 1), (36, 1)),
    'minimal': ((30, 2), (42, 2)),
    'srt':     ((26, 1), (42, 2)),
}


def limits(style=None, vertical=True, look=None, max_chars=None, lines=None):
    """(characters per line, lines) for a style on a vertical/horizontal sequence; the client's measured look and
    explicit values win."""
    if style == 'marca' or look:
        c, n = (look or {}).get('max_chars') or 22, (look or {}).get('lines') or 1
    else:
        c, n = STYLE_LIMITS.get(style or 'srt', STYLE_LIMITS['srt'])[0 if vertical else 1]
    return max_chars or c, lines or n


def _bare(w):
    return re.sub(r"[^\w$%²]", '', (w or '').lower())


def _is_num(w):
    b = _bare(w)
    return bool(b) and (any(ch.isdigit() for ch in b) or b in NUMBER_WORDS or b in ('r$', '$'))


def _in_number(W, i):
    """Is the break after word i inside a spoken number/amount ("vinte e | quatro", "R$ | 750", "setenta | e duas")?"""
    a, b = W[i]['w'], W[i + 1]['w']
    if re.search(r'[,.;:?!]$', a):
        return False
    if a.strip().upper() in ('R$', '$'):
        return True
    if _is_num(a) and (_is_num(b) or _bare(b) in ('%', 'por')):
        return True
    if _bare(a) == 'e' and i > 0 and _is_num(W[i - 1]['w']) and _is_num(b):
        return True
    if _bare(b) == 'e' and i + 2 < len(W) and _is_num(a) and _is_num(W[i + 2]['w']):
        return True
    return False


def break_cost(W, i):
    """How bad it is to end a block after word i (negative = a good place)."""
    a, b = W[i], W[i + 1]
    if _in_number(W, i):
        return 8.0
    c = 0.0
    if re.search(r'[,;:—–-]$', a['w']):
        c -= 1.5
    elif re.search(r'[.?!…]$', a['w']):
        c -= 3.0
    else:
        c += GLUE.get(_bare(a['w']), 0.0)
        if a['w'][:1].isupper() and b['w'][:1].isupper() and i > 0 and not re.search(r'[.?!…]$', W[i - 1]['w']):
            c += 3.0                                    # inside a name: "Cidade | Alta", "Reserva das | Bromélias"
    if _bare(b['w']) in OPENERS:
        c -= 0.6
    gap = b['s'] - a['e']
    c -= 4.0 * min(max(gap, 0.0), 0.6)              # a breath is where a block wants to end
    return c


def _text(W, b, e):
    return ' '.join(w['w'] for w in W[b:e])


def wrap(words, max_chars):
    """A block's text in up to 2 lines: the break with the best break_cost among those that fit, lines balanced, the
    lower line the longer one when they differ (a pyramid reads better)."""
    t = ' '.join(w['w'] for w in words)
    if len(t) <= max_chars or len(words) < 2:
        return t
    best, bk = None, 1
    for k in range(1, len(words)):
        l1, l2 = _text(words, 0, k), _text(words, k, len(words))
        over = max(0, len(l1) - max_chars) + max(0, len(l2) - max_chars)
        c = over * 10 + break_cost(words, k - 1) + abs(len(l1) - len(l2)) * 0.08 + (0.3 if len(l1) > len(l2) else 0)
        if best is None or c < best:
            best, bk = c, k
    return _text(words, 0, bk) + '\n' + _text(words, bk, len(words))


def _sentences(W, pause=1.0):
    """[(start, end)] index ranges: a sentence ends at . ? ! or a long pause."""
    out, b = [], 0
    for i, w in enumerate(W):
        if i == len(W) - 1 or re.search(r'[.?!…]$', w['w']) or W[i + 1]['s'] - w['e'] >= pause:
            out.append((b, i + 1))
            b = i + 1
    return out


def blocks(W, max_chars=26, lines=1, max_words=None):
    """[(start, end)] word index ranges, one per caption block."""
    cap = max_chars * lines
    out = []
    for s0, s1 in _sentences(W):
        n = s1 - s0
        whole = len(_text(W, s0, s1))
        best = [0.0] + [float('inf')] * n
        frm = [0] * (n + 1)
        for e in range(1, n + 1):
            for b in range(e - 1, -1, -1):
                L = len(_text(W, s0 + b, s0 + e))
                k = e - b
                last = e == n
                if L > cap * 1.12 + (0.05 * cap if last else 0) and k > 1:
                    break                                       # longer starts only get longer
                if max_words and k > max_words + 3:         # a preference, not a wall: the line width is the wall
                    break
                ws = W[s0 + b:s0 + e]
                c = 1.0                                         # every block costs a reading
                if max_words and k > max_words:
                    c += (k - max_words) * 0.7
                if L > cap:
                    c += (L - cap) * 0.8
                if L < cap * 0.45 and whole > L:                # a scrap of a sentence on its own
                    c += (cap * 0.45 - L) * 0.12 + (1.5 if k == 1 else 0)
                if not last:
                    c += break_cost(W, s0 + e - 1)
                inner = max((ws[j + 1]['s'] - ws[j]['e'] for j in range(k - 1)), default=0)
                if inner > 0.7:                                 # text sitting over a pause, waiting for its words
                    c += (inner - 0.7) * 6
                nxt = W[s0 + e]['s'] if s0 + e < len(W) else ws[-1]['e'] + HOLD
                span = min(nxt, ws[-1]['e'] + CHAIN) - ws[0]['s']      # how long it can stay up
                if L / max(span, 0.3) > CPS + 3:                # gone before it is read
                    c += (L / max(span, 0.3) - CPS - 3) * 0.4
                if span > MAX_DUR:
                    c += (span - MAX_DUR) * 2
                if best[b] + c < best[e]:
                    best[e], frm[e] = best[b] + c, b
        cut, e = [], n
        while e > 0:
            cut.append((s0 + frm[e], s0 + e))
            e = frm[e]
        out += cut[::-1]
    return out


def timed(W, bl, fps=30, cuts=(), duration=None):
    """[(in, out)] per block on the frame grid of `fps`."""
    fd = 1 / float(Fraction(str(fps or 30)))
    q = lambda t: round(round(t / fd) * fd, 3)
    cuts = sorted(c for c in cuts if c > 0)
    near = lambda t, lo, hi: next((c for c in cuts if t - lo <= c <= t + hi), None)
    ins = []
    for b, e in bl:
        a = W[b]['s']
        c = near(a, SNAP_FRAMES * fd, 4 * fd)                   # speech starting just after a cut comes in on it
        if c is not None and c <= W[b]['e']:
            a = c
        ins.append(a)
    out = []
    for k, (b, e) in enumerate(bl):
        a, last = ins[k], W[e - 1]['e']
        chars = len(_text(W, b, e))
        z = max(last + HOLD, a + MIN_DUR, a + min(chars / CPS, MAX_DUR))
        z = min(z, max(a + MAX_DUR, last))
        c = near(z, 12 * fd, 12 * fd)
        if c is not None and c >= last and c - a >= MIN_DUR * 0.8:
            z = c - GAP_FRAMES * fd                             # out with the shot, not 3 frames into the next one
        if k + 1 < len(bl):
            nx = ins[k + 1]
            if nx - z < CHAIN:
                z = nx - GAP_FRAMES * fd
            z = min(z, nx - GAP_FRAMES * fd)
        if duration:
            z = min(z, duration)
        out.append((q(a), q(max(z, a + fd))))
    return out


def cues(W, max_chars=26, lines=1, max_words=None, fps=30, cuts=(), duration=None):
    """[[first word, last word, in, out, text]] — the blocks, timed, text wrapped in up to `lines` lines."""
    if not W:
        return []
    bl = blocks(W, max_chars, lines, max_words)
    tm = timed(W, bl, fps, cuts, duration)
    return [[b, e - 1, a, z, wrap(W[b:e], max_chars) if lines > 1 else _text(W, b, e)] for (b, e), (a, z) in zip(bl, tm)]


def srt_time(t):
    t = max(t, 0); h, r = divmod(t, 3600); m, s = divmod(r, 60)
    return f'{int(h):02d}:{int(m):02d}:{int(s):02d},{int(round((s % 1) * 1000)) % 1000:03d}'
