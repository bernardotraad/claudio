"""Install the decupa panel into Premiere Pro (CEP):  python -m decupa.panel install

Copies panel_premiere/ to the user's CEP extensions folder (Windows: %APPDATA%\\Adobe\\CEP\\extensions\\com.decupa.panel,
macOS: ~/Library/Application Support/Adobe/CEP/extensions/com.decupa.panel) and enables unsigned panels
(PlayerDebugMode=1 for CSXS 9–14) if needed. Restart Premiere, then Window › Extensions › decupa.
"""
import os, shutil, subprocess, sys

from decupa import core

SRC = os.path.join(os.path.dirname(__file__), 'panel_premiere')
ID = 'com.decupa.panel'


def _ext_dir():
    if core.IS_MAC:
        return os.path.expanduser('~/Library/Application Support/Adobe/CEP/extensions')
    if core.IS_WIN:
        return os.path.join(os.environ['APPDATA'], 'Adobe', 'CEP', 'extensions')
    if core.IS_WSL:
        appdata = subprocess.run(['cmd.exe', '/c', 'echo %APPDATA%'], capture_output=True, text=True, cwd='/mnt/c').stdout.strip()
        return core.local_path(appdata) + '/Adobe/CEP/extensions'
    raise RuntimeError('Premiere panels need Windows or macOS')


def _debug_mode():
    if core.IS_MAC:
        for v in range(9, 15):
            subprocess.run(['defaults', 'write', f'com.adobe.CSXS.{v}', 'PlayerDebugMode', '1'], capture_output=True)
    else:
        reg = 'reg.exe' if core.IS_WSL else 'reg'
        for v in range(9, 15):
            subprocess.run([reg, 'add', f'HKCU\\Software\\Adobe\\CSXS.{v}', '/v', 'PlayerDebugMode', '/t', 'REG_SZ', '/d', '1', '/f'],
                           capture_output=True, cwd='/mnt/c' if core.IS_WSL else None)


def install():
    dst = os.path.join(_ext_dir(), ID)
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(SRC, dst, ignore=shutil.ignore_patterns('__pycache__'))
    _debug_mode()
    return dst


def main():
    if sys.argv[1:] in ([], ['install']):
        print('painel instalado em', core.nle_path(install()), '— reinicie o Premiere: Janela › Extensões › decupa')
    else:
        print(__doc__)


if __name__ == '__main__':
    main()
