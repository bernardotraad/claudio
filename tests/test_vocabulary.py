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
