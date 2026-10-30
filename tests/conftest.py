"""Fast tests on synthetic media: no GPU, no Whisper, no Premiere. Run: pytest -q
Cache goes to a temp folder (set before claudio is imported)."""
import os, shutil, subprocess, tempfile

import pytest

_CACHE = tempfile.mkdtemp(prefix='claudio-test-cache-')
os.environ['CLAUDIO_CACHE'] = _CACHE
os.environ.setdefault('CLAUDIO_GPU', '0')
os.environ['CLAUDIO_PROFILE'] = os.path.join(_CACHE, 'perfil.json')     # no profile: the neutral defaults, never the machine's
os.environ['CLAUDIO_STT'] = 'whisper'                                   # never the paid STT, whatever settings.json says
os.environ.setdefault('CLAUDIO_WHISPER', 'tiny')                        # 75 MB, seconds on a CPU (CI caches it)
os.environ.setdefault('CLAUDIO_MLX_MODEL', 'mlx-community/whisper-tiny')   # the same on a Mac (mlx-whisper)

needs_ffmpeg = pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg not installed')


def _has_chromium():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch().close()
        return True
    except Exception:
        return False


needs_browser = pytest.mark.skipif(not _has_chromium(), reason='playwright chromium not installed')


def _has_whisper():
    try:
        import faster_whisper  # noqa: F401
        return os.environ.get('CLAUDIO_TEST_WHISPER', '1') != '0'
    except ImportError:
        return False


needs_whisper = pytest.mark.skipif(not _has_whisper(), reason='faster-whisper not installed (or CLAUDIO_TEST_WHISPER=0)')
SPEECH = os.path.join(os.path.dirname(__file__), 'data', 'fala_pt.flac')   # 1 s of silence, then the same line twice


@pytest.fixture(scope='session')
def speech_clip(tmp_path_factory):
    """13 s 16:9 clip whose sound is a synthetic pt-BR voice (Windows "Maria"): 'Olá. Este é um teste do Cláudio, o
    editor.' said twice, the first word at ~1 s."""
    p = tmp_path_factory.mktemp('speech') / 'fala.mp4'
    ff('-f', 'lavfi', '-i', 'testsrc2=s=320x180:r=25:d=13.3', '-i', SPEECH, '-shortest', '-pix_fmt', 'yuv420p',
       '-c:a', 'aac', str(p))
    return p


def ff(*args):
    subprocess.run(['ffmpeg', '-v', 'error', '-y', *args], check=True)


@pytest.fixture(scope='session')
def multicam_shoot(tmp_path_factory):
    """Two cameras and a recorder hearing the same pink noise: CAM A starts 2 s and CAM B 6 s into the recorder.
    The folder name has a '#', spaces and an apostrophe, like the editor's real paths."""
    root = tmp_path_factory.mktemp('job') / "ACOUSTIC N' ROLL #01"
    for d in ('A', 'B', 'SOM'):
        (root / d).mkdir(parents=True)
    master = root / 'SOM' / 'ZOOM.wav'
    ff('-f', 'lavfi', '-i', 'anoisesrc=d=40:c=pink:r=48000:a=0.3', '-ac', '2', str(master))
    ff('-f', 'lavfi', '-i', 'testsrc2=s=320x180:r=25:d=30', '-ss', '2', '-t', '30', '-i', str(master), '-shortest',
       '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac', str(root / 'A' / 'A001.mp4'))
    ff('-f', 'lavfi', '-i', 'smptebars=s=320x180:r=25:d=30', '-ss', '6', '-t', '30', '-i', str(master), '-shortest',
       '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac', str(root / 'B' / 'B001.mp4'))
    return root


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_CACHE, ignore_errors=True)
