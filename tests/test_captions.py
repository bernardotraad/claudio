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


def test_clean_for_reading():
    W = words('Ação. Eu Eu tenho a pa parcela muito muito boa empre- empreendimento ãh com um quarto.', pauses={1: 0.5})
    t = ' '.join(w['w'] for w in captions.clean_for_reading(W))
    assert t == 'Eu tenho a parcela muito muito boa empreendimento com um quarto.'
    # "ação" inside a sentence is a word
    W = words('uma ação do governo')
    assert len(captions.clean_for_reading(W)) == 4


def test_amount_shows_whole_on_first_spoken_word():
    # "setecentos e cinquenta reais" corrected to "R$ 750": no "R$" alone waiting for the number
    from claudio.motion import _retime
    said = 'parcelas a partir de setecentos e cinquenta reais, e entrada'.split()
    orig = [{'w': w, 's': k * 0.4, 'e': k * 0.4 + 0.35} for k, w in enumerate(said)]
    rt = _retime(orig, 'Parcelas a partir de R$ 750, e entrada')
    rs, n = rt[4], rt[5]
    assert (rs['w'], n['w']) == ('R$', '750,')
    assert rs['s'] == n['s'] == orig[4]['s'] and rs['e'] == n['e'] == round(orig[7]['e'], 3)
    assert rt[6]['s'] == orig[8]['s']                      # the comma ends the amount


def test_amount_with_magnitude_and_plain_numbers():
    from claudio import captions
    W = [{'w': w, 's': k * 0.4, 'e': k * 0.4 + 0.3} for k, w in enumerate('ganhe R$ 55 mil em dois meses 100% financiado'.split())]
    captions.amounts_at_once(W)
    assert W[1]['s'] == W[2]['s'] == W[3]['s'] == 0.4 and W[1]['e'] == W[3]['e'] and abs(W[1]['e'] - 1.5) < 1e-9
    assert [round(w['s'], 3) for w in W[4:]] == [1.6, 2.0, 2.4, 2.8, 3.2]   # "dois meses", "100%" untouched
