from claudio import motion

W = [{'w': 'que', 's': 0.0, 'e': 0.2}, {'w': 'paga', 's': 0.25, 'e': 0.5}, {'w': 'aluguel', 's': 0.55, 'e': 1.0},
     {'w': 'R$599', 's': 1.1, 'e': 1.6}, {'w': 'mil.', 's': 1.7, 'e': 1.9}]


def test_mark_emphasis():
    em = {w['w']: w['em'] for w in motion.mark_emphasis(W, extra=['paga'])}
    assert em == {'que': False, 'paga': True, 'aluguel': True, 'R$599': True, 'mil.': True}


def test_brand_static_and_dynamic():
    look = {'font': 'Gilroy-Medium', 'bg': '#ee3c07', 'size': 80}
    static = motion.caption_code(W, 'marca', look=look)
    dyn = motion.caption_code(W, 'marca', look={**look, 'dynamic': True})
    assert '#ee3c07' in static and 'Gilroy-Medium' in static
    assert 'spring' in dyn and 'spring' not in static
    assert '"em": true' in dyn or '"em":true' in dyn
