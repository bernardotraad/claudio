"""Caption blocks cut by phrase and timed like a subtitler's (claudio.captions)."""
from claudio import captions


def words(text, start=0.0, step=0.3, pauses=()):
    """Synthetic words, `step` s each; pauses = {word index: extra silence before it}."""
    out, t = [], start
    for i, w in enumerate(text.split()):
        t += dict(pauses).get(i, 0)
        out.append({'w': w, 's': round(t, 3), 'e': round(t + step - 0.04, 3)})
        t += step
    return out


def texts(W, **kw):
    return [c[4] for c in captions.cues(W, **kw)]


def test_never_ends_on_a_word_that_leans_on_the_next():
    W = words('A Cidade Alta é a região que mais cresce na cidade de Juiz de Fora, e só a INK tem um empreendimento '
              'com localização privilegiada pra você.')
    for t in texts(W, max_chars=26):
        assert t.split()[-1].lower().strip(',.') not in ('de', 'a', 'o', 'que', 'na', 'e', 'é', 'um', 'mais'), t
        assert len(t) <= 30


def test_never_across_a_sentence_end():
    W = words('Você não tem entrada? Não tem problema. Eu tenho a solução para você.')
    for t in texts(W, max_chars=26):
        assert not any(w[-1] in '.?!' for w in t.split()[:-1]), t


def test_a_number_and_a_name_stay_whole():
    W = words('Com parcelas a partir de R$ 750 e segurança vinte e quatro horas na Cidade Alta de Juiz de Fora.')
    ts = texts(W, max_chars=20)
    assert any('R$ 750' in t for t in ts)
    assert any('vinte e quatro' in t for t in ts)
    assert not any(t.endswith('Cidade') for t in ts)


def test_a_breath_is_where_a_block_ends():
    W = words('você que paga aluguel e sente que o dinheiro vai embora', pauses={4: 0.5})
    assert texts(W, max_chars=26)[0] == 'você que paga aluguel'


def test_timing_on_frames_chained_and_on_the_cut():
    W = words('primeira frase aqui. segunda frase logo depois.', start=1.0, step=0.4)
    cs = captions.cues(W, max_chars=26, fps='30000/1001')
    fd = 1001 / 30000
    (_, _, a1, b1, _), (_, _, a2, b2, _) = cs
    assert abs(a1 - W[0]['s']) <= fd                      # in on the voice
    assert abs((a2 - b1) - 2 * fd) < 0.002                  # chained, 2 frames apart
    for t in (a1, b1, a2, b2):
        assert abs(t / fd - round(t / fd)) < 0.02           # on the frame grid
    # speech 3 frames after a picture cut comes in on the cut
    cut = W[3]['s'] - 3 * fd
    cs = captions.cues(W, max_chars=26, fps='30000/1001', cuts=[cut])
    assert abs(cs[1][2] - cut) < fd


def test_two_lines_balanced():
    W = words('Com parcelas a partir de R$ 750 e entrada facilitada você pode conquistar o seu primeiro imóvel.')
    for t in texts(W, max_chars=42, lines=2):
        assert t.count('\n') <= 1
        assert all(len(l) <= 46 for l in t.split('\n'))
