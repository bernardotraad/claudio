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


def test_chorus_fix_keeps_the_next_lines_first_word():
    # lines sung back to back: the misheard line must not swallow the next line's first word
    segs = [{'s': 0.0, 'e': 2.0, 'text': 'Pois nada mais que eu tentei funcionou'},
            {'s': 2.0, 'e': 4.0, 'text': 'Pois nada mais que eu tentei funcionou'},
            {'s': 4.0, 'e': 6.0, 'text': 'Coisa da mais que eu tentei funcionar'},
            {'s': 6.0, 'e': 8.0, 'text': 'Eu preciso'}]
    words = []
    for s in segs:
        ws = s['text'].split()
        words += [{'w': w, 's': s['s'] + k * 2 / len(ws), 'e': s['s'] + (k + 1) * 2 / len(ws), 'p': .9} for k, w in enumerate(ws)]
    segs, words = core.fix_repeated_lines(segs, words)
    assert ' '.join(w['w'] for w in words).endswith('tentei funcionou Eu preciso')


def test_sung_lines_and_blocks():
    from claudio import motion
    t = iter(range(1000))
    w = lambda x, gap=0: {'w': x, 's': next(t) * 0.5 + gap, 'e': 0}
    ws = [w(x) for x in 'Ela mudou aquilo que eu pensava Mudou meu jeito, mudou minha visão'.split()]
    ws += [w(x, 3) for x in 'Juro que não consigo entender quando você mudou na TV'.split()]
    for a in ws:
        a['e'] = a['s'] + 0.4
    lines = core.lyric_lines(core.mark_lines(ws))
    assert [' '.join(x['w'] for x in ln) for ln in lines] == [
        'Ela mudou aquilo que eu pensava', 'Mudou meu jeito, mudou minha visão',
        'Juro que não consigo entender quando você mudou na TV']        # "TV" is not a line start
    blocks = motion.lyric_chunks([{**x, 'lyrics': True} for x in core.mark_lines(ws)])
    assert all(len(' '.join(x['w'] for x in b)) <= 38 for b in blocks) and len(blocks) == 4


def test_snap_moves_a_line_start_to_the_voice():
    import numpy as np
    from claudio import vocals
    env = np.full(1000, -60.0, np.float32)
    env[100:300] = -12        # "pensava" held to 3.0 s
    env[520:600] = -12        # "Mudou" really sung at 5.2 s
    words = [{'w': 'pensava', 's': 1.0, 'e': 3.0}, {'w': 'Mudou', 's': 3.0, 'e': 6.0}]
    out, moved = vocals.snap_words(words, env)
    assert moved == 1 and out[0]['s'] == 1.0 and abs(out[1]['s'] - 5.15) < 0.02 and out[1]['e'] == 6.0
