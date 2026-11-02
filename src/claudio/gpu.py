"""claudio.gpu — the optional torch side: a venv of its own for the jobs that need PyTorch on an NVIDIA GPU, so the
Cláudio itself stays CPU/ONNX (no torch to install, the MCP starts fast). Each job is a script in gpu_workers/, run
as a subprocess with that venv's Python.

  RIFE (slowmo.py)        frame interpolation — github.com/hzwer/Practical-RIFE (MIT), model v4.25
  Kokoro (motion.py)      narration, native pt-BR voices — github.com/hexgrad/kokoro (Apache-2.0)
  Qwen3-TTS (motion.py)   narration with voice cloning — github.com/QwenLM/Qwen3-TTS (Apache-2.0); European Portuguese
  Chatterbox (motion.py)  voice cloning that keeps a Brazilian accent — github.com/resemble-ai/chatterbox (MIT), in a
                          venv of its own (CLAUDIO_CLONE_PYTHON, default ~/.local/opt/claudio-clone): it pins torch 2.6

  python()      CLAUDIO_GPU_PYTHON, default ~/.local/opt/claudio-gpu/bin/python
  rife_dir()    CLAUDIO_RIFE_DIR, default ~/.local/opt/rife/src (Practical-RIFE checkout + train_log/)
  status()      {python, rife, tts: None when ready, else what is missing}
  setup()       claudio-doctor --fix-gpu: uv venv (Python 3.12) + qwen-tts + torchvision, RIFE checkout and model
                (~7 GB; the Qwen weights, ~4.5 GB, come from Hugging Face on first use)
"""
import os, shutil, subprocess

from claudio import core

RIFE_REPO, RIFE_COMMIT = 'https://github.com/hzwer/Practical-RIFE', 'bbfd2ea'
RIFE_MODEL = '1ZKjcbmt1hypiFprJPIKW0Tt0lr_2i7bg'        # v4.25 (Google Drive id from the README; MIT like the code)
PACKAGES = ['qwen-tts==0.1.1', 'kokoro==0.9.4', 'torchvision', 'soundfile', 'gdown']
CLONE_PACKAGES = ['chatterbox-tts==0.1.7']


def clone_python():
    return os.path.expanduser(core.env('CLONE_PYTHON', '~/.local/opt/claudio-clone/bin/python'))


def python():
    return os.path.expanduser(core.env('GPU_PYTHON', '~/.local/opt/claudio-gpu/bin/python'))


def rife_dir():
    return os.path.expanduser(core.env('RIFE_DIR', '~/.local/opt/rife/src'))


def worker(name):
    return os.path.join(os.path.dirname(__file__), 'gpu_workers', name)


def _has(mod):
    return subprocess.run([python(), '-c', f'import {mod}'], capture_output=True).returncode == 0


def status():
    # Chatterbox has its own venv, so it is checked even where the GPU venv is missing (a Mac without one)
    clone = (None if os.path.exists(clone_python()) and subprocess.run([clone_python(), '-c', 'import chatterbox'],
                                                                       capture_output=True).returncode == 0
             else f'Chatterbox não instalado ({clone_python()}): claudio-doctor --fix-gpu')
    if not os.path.exists(python()):
        miss = f'venv da GPU não encontrado ({python()}): claudio-doctor --fix-gpu'
        return {'python': miss, 'rife': miss, 'tts': miss, 'clone': clone}
    return {'python': None,
            'rife': None if os.path.exists(os.path.join(rife_dir(), 'train_log', 'flownet.pkl')) and _has('torchvision')
            else f'RIFE não instalado em {rife_dir()}: claudio-doctor --fix-gpu',
            'tts': None if _has('kokoro') else 'kokoro não instalado no venv da GPU: claudio-doctor --fix-gpu',
            'clone': clone}


def setup(log=print):
    uv = shutil.which('uv') or os.path.expanduser('~/.local/bin/uv')
    if not os.path.exists(uv):
        raise RuntimeError('precisa do uv: curl -LsSf https://astral.sh/uv/install.sh | sh')
    venv = os.path.dirname(os.path.dirname(python()))
    env = {**os.environ, 'TMPDIR': os.path.expanduser('~/.cache/tmp')}   # /tmp is a small tmpfs on WSL: torch is 3 GB
    os.makedirs(env['TMPDIR'], exist_ok=True)
    if not os.path.exists(python()):
        log(f'criando o venv em {venv}')
        subprocess.run([uv, 'venv', '-p', '3.12', venv], check=True, env=env)
    log('instalando torch + qwen-tts + torchvision (alguns GB)')
    subprocess.run([uv, 'pip', 'install', '-p', python(), *PACKAGES, '--index-strategy', 'unsafe-best-match'], check=True, env=env)
    cv = os.path.dirname(os.path.dirname(clone_python()))
    if not os.path.exists(clone_python()):
        log(f'criando o venv da clonagem de voz em {cv}')
        subprocess.run([uv, 'venv', '-p', '3.11', cv], check=True, env=env)
    subprocess.run([uv, 'pip', 'install', '-p', clone_python(), *CLONE_PACKAGES], check=True, env=env)
    rd = rife_dir()
    if not os.path.isdir(os.path.join(rd, '.git')):
        log(f'baixando o Practical-RIFE em {rd}')
        subprocess.run(['git', 'clone', '-q', RIFE_REPO, rd], check=True)
    subprocess.run(['git', '-C', rd, 'checkout', '-q', RIFE_COMMIT], check=True)
    if not os.path.exists(os.path.join(rd, 'train_log', 'flownet.pkl')):
        log('baixando o modelo RIFE v4.25')
        z = os.path.join(rd, 'v425.zip')
        subprocess.run([os.path.join(os.path.dirname(python()), 'gdown'), '-q', RIFE_MODEL, '-O', z], check=True, env=env)
        import zipfile
        with zipfile.ZipFile(z) as f:
            for m in f.namelist():
                if m.startswith('train_log/') and m.endswith(('.py', '.pkl')):
                    os.makedirs(os.path.join(rd, 'train_log'), exist_ok=True)
                    open(os.path.join(rd, m), 'wb').write(f.read(m))
        os.remove(z)
    return status()
