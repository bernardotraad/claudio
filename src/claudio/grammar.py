"""claudio.grammar — caption proofreading by the local model (claudio.llm, no credits): agreement, regência, crase,
punctuation, capitals of names, money in figures ("R$ 750") — never the speaker's words or meaning.

  suggest(sentences, keep)   {id: corrected} for the sentences of caption_sentences that need a fix; every answer goes
                             through guard() first, so a rewrite the model sneaks in is dropped, not offered
  guard(spoken, corrected)   None when the change is a proofreading one; else why it is refused

The editor (or Claude) still approves: these are suggestions, shown as a diff spoken → caption.
"""
import difflib, re

from claudio import core, llm

SYSTEM = (
    'Você é revisor de legendas de vídeo em português do Brasil. A legenda transcreve a fala de uma pessoa. Corrija SÓ '
    'o que é erro de escrita: concordância (verbal e nominal), regência, crase, pontuação (vírgulas, ponto final, '
    'interrogação), maiúsculas em nomes próprios e marcas, e valores em dinheiro com algarismos ("setecentos e cinquenta '
    'reais" → "R$ 750", "cinquenta e cinco mil reais" → "R$ 55 mil") e porcentagem com algarismos ("cem por cento" → "100%"); '
    'outros números ficam como foram ditos. "Dia a dia" sem hífen. Imperativo dirigido ao espectador fica na forma '
    'padrão ("clica aqui" → "clique aqui", "fala comigo" → "fale comigo"). NÃO troque palavras por sinônimos, NÃO '
    'reescreva, NÃO resuma, NÃO tire gírias nem marcas de fala ("pra", "a gente", "tá", "né" ficam), NÃO acrescente '
    'informação. Se a frase já está certa, devolva igual. Exemplos: "Você sabe quantos já gastou de aluguel?" → "Você '
    'sabe quanto já gastou de aluguel?"; "os apartamento tem dois quarto" → "Os apartamentos têm dois quartos."; '
    '"vou a praia" → "Vou à praia."')

NUM = {'zero', 'um', 'uma', 'dois', 'duas', 'tres', 'quatro', 'cinco', 'seis', 'sete', 'oito', 'nove', 'dez', 'onze', 'doze',
       'treze', 'quatorze', 'catorze', 'quinze', 'dezesseis', 'dezessete', 'dezoito', 'dezenove', 'vinte', 'trinta',
       'quarenta', 'cinquenta', 'sessenta', 'setenta', 'oitenta', 'noventa', 'cem', 'cento', 'duzentos', 'trezentos',
       'quatrocentos', 'quinhentos', 'seiscentos', 'setecentos', 'oitocentos', 'novecentos', 'mil', 'milhao', 'milhoes',
       'reais', 'real', 'r', 'porcento', 'por', 'e'}
SMALL = {'a', 'o', 'as', 'os', 'e', 'de', 'do', 'da', 'dos', 'das', 'que', 'se', 'em', 'no', 'na', 'pra', 'pro',
         'um', 'uma', 'ao', 'com', 'por', 'mas', 'ja', 'so'}


def _toks(s):
    return [core.norm(w) for w in s.split() if core.norm(w)]


def _numberish(ws):
    return all(w in NUM or w.isdigit() or re.fullmatch(r'\d+(mil|%)?', w) for w in ws)


def _inflection(p, q):
    """Same word, other ending: quantos/quanto, clica/clique, fala/fale, está/estão, apartamentos/apartamento."""
    n = 0
    while n < min(len(p), len(q)) and p[n] == q[n]:
        n += 1
    return n >= 3 and n >= 0.6 * min(len(p), len(q))


def _keep_terms(keep):
    return {core.norm(t.replace(' ', '')) for t in keep} | {core.norm(w) for t in keep for w in t.split()}


def guard(spoken, corrected, keep=()):
    """A change the editor would call proofreading: the same words in the same order, give or take inflections
    (quantos → quanto, clica → clique), money/numbers in figures, "dia -a -dia" → "dia a dia", a couple of small words,
    a misheard name put back to the client's spelling (keep: "INK" → "INC"). Other names stay."""
    a, b = _toks(spoken), _toks(corrected)
    if not b:
        return 'legenda vazia'
    names = _keep_terms(keep)
    small_edits = 0
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == 'equal':
            continue
        x, y = a[i1:i2], b[j1:j2]
        if ''.join(x) == ''.join(y):
            continue                                                # hyphens and spaces: dia-a-dia / dia a dia
        if _numberish(x) and _numberish(y):
            continue                                                # "setecentos e cinquenta reais" → "R$ 750"
        if op == 'replace' and len(x) == len(y) and all(p == q or _inflection(p, q) for p, q in zip(x, y)):
            continue
        if op == 'replace' and y and all(w in names for w in y) and len(x) <= len(y) + 1:
            continue                                                # misheard brand → the client's spelling
        if all(w in SMALL for w in x + y) and len(x + y) <= 2:
            small_edits += 1
            continue
        return f'mudou palavras: "{" ".join(x)}" → "{" ".join(y)}"'
    if small_edits > 2:
        return 'mexeu demais em palavras pequenas'
    caps = [w for w in spoken.split()[1:] if w[:1].isupper()]      # names and brands the transcript already capitalised
    lost = [w for w in caps if core.norm(w) and core.norm(w) not in b and not (names & set(b) - set(a))]
    if lost:
        return f'sumiu nome: {", ".join(lost)}'
    return None


def _finish(spoken, new):
    """What the model gets wrong every time, fixed by rule: a sentence that continues the previous one keeps its small
    first letter; "dia-a-dia" (and the transcript's "dia -a -dia") is "dia a dia" since the 2009 spelling reform."""
    if not new:
        return new
    new = re.sub(r'\bdia\s*-\s*a\s*-\s*dia\b', 'dia a dia', new, flags=re.I)
    if spoken[:1].islower() and new[:1].isupper() and new.split()[0].lower() == spoken.split()[0].lower():
        new = new[:1].lower() + new[1:]
    return new


ONE = {'type': 'object', 'required': ['legenda'], 'properties': {'legenda': {'type': 'string'}}}


def suggest(sentences, keep=(), progress=lambda *_: None):
    """sentences: [{id, text, remembered_correction?}] → {'fixes': {id: corrected}, 'refused': {id: why}}.
    One sentence per call, with its neighbours as context: in batches of 12 the model copied most of them back unchanged
    (it missed even its own example). Remembered corrections are the editor's: not asked again. keep: names/brands."""
    hint = f'Grafias exatas destes nomes: {", ".join(keep)}.\n' if keep else ''
    fixes, refused = {}, {}
    for k, s in enumerate(sentences):
        if s.get('remembered_correction') or not s.get('text'):
            continue
        progress(k / max(len(sentences), 1), f'revisando legendas: {k + 1} de {len(sentences)}')
        prev = sentences[k - 1]['text'] if k else ''
        nxt = sentences[k + 1]['text'] if k + 1 < len(sentences) else ''
        prompt = (hint + (f'(frase anterior, só contexto: "{prev}")\n' if prev else '') +
                  (f'(frase seguinte, só contexto: "{nxt}")\n' if nxt else '') + f'Revise esta frase: {s["text"]}')
        new = _finish(s['text'], (llm.ask(prompt, ONE, system=SYSTEM).get('legenda') or '').strip())
        if not new or new == s['text']:
            continue
        why = guard(s['text'], new, keep)
        if why:
            refused[s['id']] = f'{why} (sugeriu: {new})'
        else:
            fixes[s['id']] = new
    return {'fixes': fixes, 'refused': refused}
