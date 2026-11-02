"""gpu.status() always names every engine, also on a machine without the GPU venv (a Mac): the doctor and the
narration read each key."""
from claudio import gpu


def test_status_has_every_key_without_the_gpu_venv(monkeypatch, tmp_path):
    monkeypatch.setattr(gpu, 'python', lambda: str(tmp_path / 'nao' / 'python'))
    monkeypatch.setattr(gpu, 'clone_python', lambda: str(tmp_path / 'nem' / 'python'))
    s = gpu.status()
    assert set(s) >= {'python', 'rife', 'tts', 'clone'} and all(s.values())
