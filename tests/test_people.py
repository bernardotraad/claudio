"""Clustering of faces/voices and names that stick to the person (synthetic prints, no models)."""
import numpy as np

from claudio import people as P


def _prints(seed, centres, n):
    rng = np.random.default_rng(seed)
    out = []
    for c in centres:
        for _ in range(n):
            out.append(P._unit(c + rng.normal(0, 0.06, len(c))))      # ~0.7 within, ~0 across
    return np.array(out, np.float32)


def test_cluster_separates_people():
    rng = np.random.default_rng(1)
    centres = [P._unit(rng.normal(0, 1, 128)) for _ in range(3)]
    E = _prints(2, centres, 6)
    lab = P._cluster(E, 0.42)
    assert len(set(lab)) == 3
    assert all(len(set(lab[i * 6:(i + 1) * 6])) == 1 for i in range(3))


def test_names_survive_a_rerun(tmp_path, monkeypatch):
    rng = np.random.default_rng(3)
    a, b = P._unit(rng.normal(0, 1, 128)), P._unit(rng.normal(0, 1, 128))

    def fake_people(folder, progress=None, paths=None):
        rows = []
        for i, c in enumerate([a, b]):
            rows.append({'id': f'P{i + 1}', 'centroid': c, 'name': None, 'count': 3, 'clips': [], 'members': []})
        named = P._load_names(folder)
        for r in rows:
            for nm in named.get('faces', []):
                if float(r['centroid'] @ P._unit(nm['centroid'])) > P.FACE_T:
                    r['name'] = nm['name']
        return {'faces': rows, 'voices': []}

    monkeypatch.setattr(P, 'people', fake_people)
    folder = str(tmp_path)
    assert P.name(folder, {'P2': 'Caio'}, fake_people(folder)) == {'P2': 'Caio'}
    again = fake_people(folder)
    assert again['faces'][1]['name'] == 'Caio' and again['faces'][0]['name'] is None
