"""Caption fixes teach the transcript new spellings; grammar fixes don't."""
import pytest

from claudio import motion


@pytest.mark.parametrize('old,new,want', [
    ('Você sabe quantos já gastou de aluguel?', 'Você sabe quanto já gastou de aluguel?', []),
    ('a gente fiz isso', 'a gente fizemos isso', []),
    ('foi a melhor show', 'foi o melhor show', []),
    ('esse é um blog que a gente vai fazer', 'esse é um vlog que a gente vai fazer', ['vlog']),
    ('o teclista da banda', 'o tecladista da banda', ['tecladista']),
    ('o John Fruchante do red hot', 'o John Frusciante do Red Hot Chili Peppers', ['John Frusciante', 'Red Hot Chili Peppers']),
])
def test_learned_terms(old, new, want):
    assert motion.learned_terms(old, new) == want


def test_last_complete_uncriticised_take_wins():
    """INC rule: the last complete attempt the speaker didn't criticise (even rephrased) is the take."""
    from claudio import core
    def W(t0, text, p=0.9):
        out, t = [], t0
        for w in text.split():
            out.append({'w': w, 's': round(t, 2), 'e': round(t + 0.3, 2), 'p': p}); t += 0.35
        return out
    words = (W(1, 'Chega de pagar aluguel. Clique no link abaixo. Nossa, perdão, perdão.') +
             W(9, 'Três, dois, um, ação.') + W(12, 'Chega de pagar aluguel. Clique no link abaixo e fale comigo.'))
    import types
    idx = {'cache': '/nonexistent', 'info': {'duration': 20, 'fps': '30'}}
    core_load, core_slate, core_rq = core.load_words, core.slate, core.range_quality
    try:
        core.load_words = lambda i: words
        core.slate = lambda i: {'has_slate': False}
        core.range_quality = lambda i, s, e: {}
        g = core.takes_report(idx)['groups'][0]
        assert abs(g['best']['s'] - 12) < 0.01
    finally:
        core.load_words, core.slate, core.range_quality = core_load, core_slate, core_rq
