"""The local model (Ollama): waking the GPU machine when it is off, the model spilled onto the CPU, and Descrever
planos with the vision model — its sentence replaces CLIP's guesses, cached, CLIP when the server is gone."""
import json

from claudio import core, describe, llm


def test_wakes_through_home_assistant_and_waits(monkeypatch):
    calls, answers = [], iter([False, False, False, True])
    monkeypatch.setattr(llm, 'endpoint', lambda: 'http://gpu:11434')
    monkeypatch.setattr(llm, 'up', lambda timeout=3: next(answers))
    monkeypatch.setattr(llm.time, 'sleep', lambda s: None)
    env = {'WAKE_HA_URL': 'https://ha.example', 'WAKE_HA_ENTITY': 'switch.desktop', 'WAKE_HA_TOKEN': 't'}
    monkeypatch.setattr(core, 'env', lambda n, d=None: env.get(n, d))

    class R:
        def read(self):
            return b'[]'
    monkeypatch.setattr(llm.urllib.request, 'urlopen', lambda r, timeout=0: calls.append((r.full_url, r.headers, r.data)) or R())
    ok, note = llm.ensure()
    assert ok and 'Home Assistant' in note
    url, headers, data = calls[0]
    assert url == 'https://ha.example/api/services/switch/turn_on' and json.loads(data) == {'entity_id': 'switch.desktop'}
    assert headers['Authorization'] == 'Bearer t'


def test_nothing_to_wake_says_why(monkeypatch):
    monkeypatch.setattr(llm, 'endpoint', lambda: 'http://gpu:11434')
    monkeypatch.setattr(llm, 'up', lambda timeout=3: False)
    monkeypatch.setattr(core, 'env', lambda n, d=None: d)
    ok, why = llm.ensure()
    assert not ok and 'wake_ha' in why
    monkeypatch.setattr(llm, 'endpoint', lambda: None)
    assert llm.ensure() == (False, 'sem Ollama configurado ("ollama" em ~/.config/claudio/settings.json)')


def test_model_spilled_on_the_cpu_is_reported(monkeypatch):
    monkeypatch.setattr(llm, '_req', lambda path, *a, **k: {'models': [{'name': llm.model(), 'size': 100, 'size_vram': 60}]})
    assert '40% do modelo' in llm.gpu_check()
    monkeypatch.setattr(llm, '_req', lambda path, *a, **k: {'models': [{'name': llm.model(), 'size': 100, 'size_vram': 100}]})
    assert llm.gpu_check() is None


def test_describe_uses_the_vision_model_and_caches_it(tmp_path, monkeypatch):
    import numpy as np
    d = tmp_path / 'cache'
    d.mkdir()
    idx = {'cache': str(d), 'info': {'has_video': True, 'duration': 8.0, 'fps': '24', 'start_tc': None}, 'picture': {}}
    rng = np.random.default_rng(1)
    v = rng.normal(size=512); v /= np.linalg.norm(v)
    monkeypatch.setattr(core, 'build_index', lambda p: idx)
    monkeypatch.setattr(core, 'get_rotation', lambda p: 0)
    monkeypatch.setattr(core, 'heavy_codec', lambda p: False)
    monkeypatch.setattr(core, 'analysis_source', lambda p: p)
    monkeypatch.setattr(core, 'nle_path', lambda p: p)
    monkeypatch.setattr(core, 'local_path', lambda p: p)
    from PIL import Image
    monkeypatch.setattr(core, 'grab', lambda *a, **k: Image.new('RGB', (64, 36)))
    monkeypatch.setattr(describe.visual, 'embed_clip', lambda p: {'times': [0, 2, 4, 6], 'emb': np.array([v] * 4, np.float32)})
    monkeypatch.setattr(describe, '_text_bank', lambda: np.array([v] * len(describe.PROMPTS), np.float32))
    asked = []
    monkeypatch.setattr(describe.llm, 'vision', lambda jpg, prompt, schema: asked.append(1) or
                        {'descricao': 'Homem de camisa de caveiras cantando no palco.', 'plano': 'plano médio', 'lugar': 'palco', 'luz': 'palco'})
    r = describe.describe_clip('/x/a.mp4', names=[], vlm=True)
    L = core.read_log(idx)
    assert L[0]['text'] == 'Homem de camisa de caveiras cantando no palco' and 'ia local' in L[0]['tags']
    assert 'palco' in L[0]['tags'] and 'auto' in L[0]['tags'] and len(asked) == r['stretches']
    describe.describe_clip('/x/a.mp4', names=[], vlm=True)           # cached: the model is not asked again
    assert len(asked) == r['stretches']

    def gone(*a, **k):
        raise OSError('connection refused')
    monkeypatch.setattr(describe.llm, 'vision', gone)
    (d / 'vlm_v1.json').unlink()
    r = describe.describe_clip('/x/a.mp4', names=[], vlm=True)
    assert 'refused' in r['vlm_error'] and 'ia local' not in core.read_log(idx)[0]['tags']
