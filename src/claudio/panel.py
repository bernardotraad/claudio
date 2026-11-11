"""Install the claudio panel into Premiere Pro (CEP):  python -m claudio.panel install

Copies panel_premiere/ to the user's CEP extensions folder (Windows: %APPDATA%\\Adobe\\CEP\\extensions\\com.claudio.panel,
macOS: ~/Library/Application Support/Adobe/CEP/extensions/com.claudio.panel) and enables unsigned panels
(PlayerDebugMode=1 for CSXS 9–14) if needed. Restart Premiere, then Window › Extensions › Cláudio, o Editor.
"""
import os, shutil, subprocess, sys

from claudio import core

SRC = os.path.join(os.path.dirname(__file__), 'panel_premiere')
ID = 'com.claudio.panel'


def _ext_dir():
    if core.IS_MAC:
        return os.path.expanduser('~/Library/Application Support/Adobe/CEP/extensions')
    if core.IS_WIN:
        return os.path.join(os.environ['APPDATA'], 'Adobe', 'CEP', 'extensions')
    if core.IS_WSL:
        # stdin=DEVNULL: on a stdio MCP server cmd.exe would otherwise read the client's first message (initialize)
        appdata = subprocess.run(['cmd.exe', '/c', 'echo %APPDATA%'], capture_output=True, text=True, cwd='/mnt/c',
                                 stdin=subprocess.DEVNULL).stdout.strip()
        return core.local_path(appdata) + '/Adobe/CEP/extensions'
    raise RuntimeError('Premiere panels need Windows or macOS')


def _debug_mode():
    if core.IS_MAC:
        for v in range(9, 15):
            subprocess.run(['defaults', 'write', f'com.adobe.CSXS.{v}', 'PlayerDebugMode', '1'], capture_output=True,
                           stdin=subprocess.DEVNULL)
    else:
        reg = 'reg.exe' if core.IS_WSL else 'reg'
        for v in range(9, 15):
            subprocess.run([reg, 'add', f'HKCU\\Software\\Adobe\\CSXS.{v}', '/v', 'PlayerDebugMode', '/t', 'REG_SZ', '/d', '1', '/f'],
                           capture_output=True, cwd='/mnt/c' if core.IS_WSL else None, stdin=subprocess.DEVNULL)


def install():
    old = os.path.join(_ext_dir(), 'com.decupa.panel')      # the same panel before the rename (v0.7)
    if os.path.exists(old):
        shutil.rmtree(old)
    dst = os.path.join(_ext_dir(), ID)
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(SRC, dst, ignore=shutil.ignore_patterns('__pycache__'))
    _debug_mode()
    return dst


def _files(root):
    out = {}
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x != '__pycache__']
        for f in files:
            if f != '.DS_Store':
                full = os.path.join(d, f)
                out[os.path.relpath(full, root)] = full
    return out


def outdated():
    """True when the panel is installed but differs from the one in this package (an upgrade does not recopy it)."""
    try:
        dst = os.path.join(_ext_dir(), ID)
    except Exception:
        return False
    if not os.path.isdir(dst):
        return False
    a, b = _files(SRC), _files(dst)
    if a.keys() != b.keys():
        return True
    for rel, src in a.items():
        with open(src, 'rb') as x, open(b[rel], 'rb') as y:
            if x.read() != y.read():
                return True
    return False


def sync():
    """Reinstall the panel if it is installed and stale. Never raises; returns the path when it reinstalled."""
    try:
        return install() if outdated() else None
    except Exception:
        return None


def main():
    if sys.argv[1:] in ([], ['install']):
        print('painel instalado em', core.nle_path(install()), '— reinicie o Premiere: Janela › Extensões › Cláudio, o Editor')
    else:
        print(__doc__)


if __name__ == '__main__':
    main()
