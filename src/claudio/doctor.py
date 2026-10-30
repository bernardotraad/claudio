"""claudio-doctor — checks everything claudio needs on this machine (Windows+WSL, Windows, macOS, Linux) and says what to fix.
Read-only: it installs nothing, except `claudio-doctor --fix-browser`, which downloads the Chromium used for overlays."""
import glob, json, os, platform, shutil, subprocess, sys

from claudio import core

OK, WARN, BAD = '✅', '⚠️ ', '❌'


def _run(cmd, **kw):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=60, **kw)
    except Exception as e:
        return None


def check():
    rows = []
    add = lambda st, what, detail='': rows.append((st, what, detail))
    where = 'WSL' if core.IS_WSL else 'macOS' if core.IS_MAC else 'Windows' if core.IS_WIN else 'Linux'
    add(OK, f'sistema: {where} · Python {platform.python_version()} · {platform.machine()}')
    for b in ('ffmpeg', 'ffprobe'):
        p = shutil.which(b)
        add(OK if p else BAD, b, p or ('brew install ffmpeg' if core.IS_MAC else 'winget install Gyan.FFmpeg' if core.IS_WIN else 'sudo apt install ffmpeg'))
    be = core.whisper_backend()
    if be == 'mlx':
        add(OK, 'transcrição: mlx-whisper (Apple Silicon)')
    else:
        try:
            core._preload_cuda_libs()
            import ctranslate2
            n = ctranslate2.get_cuda_device_count()
            add(OK if n else WARN, f'transcrição: faster-whisper {"na GPU (CUDA)" if n else "na CPU"}',
                '' if n else ('sem CUDA: mais lento. NVIDIA → pip install "claudio[cuda]"' if not core.IS_MAC else 'instale mlx-whisper (Apple Silicon)'))
        except Exception as e:
            add(BAD, 'faster-whisper', str(e)[:120])
    g = core.gpu_ffmpeg()
    if core.IS_MAC:
        add(OK, 'decodificação: VideoToolbox (Apple)')
    else:
        add(OK if g else WARN, 'decodificação na GPU (NVDEC)', g or 'opcional: ffmpeg com CUDA em ~/.local/opt/ffmpeg-*/bin (ex.: build BtbN linux64-gpl)')
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(); b.close()
        add(OK, 'overlays/legendas: Chromium do Playwright')
    except ImportError:
        add(WARN, 'overlays/legendas: playwright não instalado', 'pip install "claudio[motion]" (ou uv tool install "claudio[motion] @ git+...")')
    except Exception:
        add(WARN, 'overlays/legendas: Chromium não baixado', 'claudio-doctor --fix-browser')
    try:
        from claudio import motion
        add(OK, 'skill motion-designer', motion.skill_dir())
    except Exception:
        add(WARN, 'skill motion-designer não encontrada', 'opcional: copie para ~/.claude/skills/motion-designer (ou CLAUDIO_MOTION_SKILL)')
    has_key = bool(os.environ.get('ELEVENLABS_API_KEY')) or os.path.exists(os.path.expanduser('~/.config/elevenlabs/api_key'))
    add(OK if has_key else WARN, 'ElevenLabs', 'chave encontrada' if has_key else 'opcional: ~/.config/elevenlabs/api_key (sem ela, narração pelo edge-tts grátis)')
    stt = core.stt_engine()
    add(OK, 'motor de transcrição', 'ElevenLabs Scribe (pago)' if stt == 'elevenlabs' else
        'Whisper local' + (' — Scribe escolhido mas sem chave' if core.env('STT') == 'elevenlabs' else
                           (' (Scribe: {"stt": "elevenlabs"} em ~/.config/claudio/settings.json)' if has_key else '')))
    from claudio import clients, profile
    prof = profile.summary()
    f = prof['pastas']
    folders = f"NLE {prof['nle'] or '(pergunta)'} · brutos {f['brutos']} · projeto {f['projeto']} · trilhas {f['trilhas']} · legendas {f['legendas']} · clientes {core.nle_path(clients.clients_dir(create=False))}"
    add(OK if prof['file'] else WARN, 'perfil do editor', (core.nle_path(prof['file']) + ' — ' if prof['file'] else '') + folders +
        ('' if prof['file'] else '\n       pastas neutras; para as suas, crie ~/.config/claudio/perfil.json (veja docs/arquitetura.md)'))
    claude = shutil.which('claude') or next((p for p in [os.path.expanduser('~/.local/bin/claude'), '/opt/homebrew/bin/claude', '/usr/local/bin/claude'] if os.path.exists(p)), None)
    if core.IS_WSL and not claude:
        win = '/mnt/c/Users/' + os.environ.get('USER', '') + '/.local/bin/claude.exe'
        claude = next(iter(glob.glob('/mnt/c/Users/*/.local/bin/claude.exe')), None)
    add(OK if claude else WARN, 'Claude Code', claude or 'instale: https://docs.claude.com/claude-code')
    cfgs = [os.path.expanduser('~/.claude.json')] + (glob.glob('/mnt/c/Users/*/.claude.json') if core.IS_WSL else [])
    for cfg in cfgs:
        try:
            servers = json.load(open(cfg)).get('mcpServers', {})
        except Exception:
            continue
        wsl_side = core.IS_WSL and cfg == os.path.expanduser('~/.claude.json')
        for name in ('claudio', 'premiere-pro', 'davinci-resolve'):
            if wsl_side and name != 'claudio':
                continue                      # Premiere/Resolve MCPs live on the Windows side, next to the NLEs
            add(OK if name in servers else WARN, f'MCP "{name}" em {core.nle_path(cfg)}', '' if name in servers else 'veja o README: claude mcp add -s user ...')
    try:
        from claudio import panel
        d = os.path.join(panel._ext_dir(), panel.ID)
        add(OK if os.path.isdir(d) else WARN, 'painel no Premiere', core.nle_path(d) if os.path.isdir(d) else 'claudio-panel install')
    except Exception as e:
        add(WARN, 'painel no Premiere', str(e)[:100])
    return rows


def main():
    if '--fix-browser' in sys.argv:
        # headless shell only: overlays and captions launch Chromium headless, and the full browser is +366 MB unused
        subprocess.run([sys.executable, '-m', 'playwright', 'install', '--only-shell', 'chromium'])
    rows = check()
    for st, what, detail in rows:
        print(f'{st} {what}' + (f'\n     → {detail}' if detail and st != OK else (f' — {detail}' if detail else '')))
    bad = sum(1 for r in rows if r[0] == BAD)
    print('\n' + ('tudo certo.' if not bad else f'{bad} item(ns) obrigatório(s) faltando.'))
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
