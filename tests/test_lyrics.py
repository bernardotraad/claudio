"""Lyrics: a line heard once that is nearly a chorus line is that line misheard."""
from claudio import core


def _song(lines):
    segs, words, t = [], [], 0.0
    for line in lines:
        ws = line.split()
        segs.append({'s': t, 'e': t + len(ws) * 0.5, 'text': line})
        words += [{'w': w, 's': round(t + k * 0.5, 3), 'e': round(t + k * 0.5 + 0.4, 3), 'p': 0.9} for k, w in enumerate(ws)]
        t += len(ws) * 0.5 + 1
    return segs, words


def test_misheard_chorus_lines_are_fixed():
    chorus = ['Vai ver que eu só preciso de outra', 'Pois nada mais que eu tentei funcionou', 'Eu preciso te tirar da cabeça']
    segs, words = _song(['Ela mudou aquilo que eu pensava'] + chorus + ['Nada que eu faça muda a situação'] + chorus +
                        ['Aqui que eu só preciso te olhar', 'Coisa da mais que eu tentei funcionar',
                         'Eu preciso te tirar da cabeça antes que eu enlouqueça'])
    segs, words = core.fix_repeated_lines(segs, words)
    text = [s['text'] for s in segs]
    assert text[-3:] == ['Vai ver que eu só preciso de outra', 'Pois nada mais que eu tentei funcionou',
                         'Eu preciso te tirar da cabeça antes que eu enlouqueça']      # two lines run together: kept
    assert text[0] == 'Ela mudou aquilo que eu pensava' and text[4] == 'Nada que eu faça muda a situação'
    said = ' '.join(w['w'] for w in words)
    assert 'Pois nada mais que eu tentei funcionou Eu preciso' in said and 'olhar' not in said
    assert all(a['s'] <= b['s'] for a, b in zip(words, words[1:]))                  # times still in order


def test_no_chorus_no_change():
    segs, words = _song(['uma linha só', 'outra linha'])
    assert core.fix_repeated_lines([dict(s) for s in segs], list(words)) == (segs, words)
