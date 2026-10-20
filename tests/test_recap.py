"""Event recap: the hook is the best moment, the rest follows the night in order, no clip hogs the cut."""
from claudio import recap


def _c(path, peak, score, when):
    return {'path': path, 'clip': path, 's': peak - 2, 'e': peak + 2, 'peak': peak, 'score': score, 'when': when, 'why': ''}


def test_select_hook_then_chronological():
    cands = [_c('A', 10, 0.9, 100), _c('A', 12, 0.95, 102), _c('A', 30, 0.8, 120), _c('A', 50, 0.85, 140),
             _c('B', 5, 1.4, 50), _c('C', 8, 0.7, 300), _c('D', 3, 0.6, 10)]
    got = recap.select(cands, 4, per_clip=2, gap=6)
    assert got[0]['path'] == 'B'                                   # the strongest moment opens
    assert all(x['path'] != y['path'] for x, y in zip(got, got[1:]))   # never the same clip twice in a row
    for p in {c['path'] for c in got}:                                  # inside one clip, the night in order
        w = [c['when'] for c in got[1:] if c['path'] == p]
        assert w == sorted(w)
    a = [c['peak'] for c in got if c['path'] == 'A']
    assert len(a) <= 2 and 10 not in a                             # 10 s is too close to the better 12 s


def test_select_relaxes_when_pool_is_small():
    cands = [_c('A', t, 1 - t / 100, t) for t in (5, 9, 13, 17, 21)]
    assert len(recap.select(cands, 5, per_clip=2, gap=6)) == 5
