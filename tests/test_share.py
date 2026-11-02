"""One analysis for two machines: the desktop leaves it next to the footage, the Mac (another path to the same files,
another cache) adopts it instead of indexing again — paths rewritten, another take with the same name never mistaken."""
import json, os, time

from claudio import core, share


def _machine(monkeypatch, tmp_path, name):
    c = tmp_path / f'cache_{name}'
    c.mkdir(exist_ok=True)
    monkeypatch.setattr(core, 'CACHE', str(c))


def _setup(tmp_path, monkeypatch):
    disk = tmp_path / 'E' / 'BRUTOS'
    disk.mkdir(parents=True)
    clip = disk / 'C0011.MP4'
    clip.write_bytes(b'x' * 1000)
    (tmp_path / 'Volumes').mkdir()
    os.symlink(tmp_path / 'E', tmp_path / 'Volumes' / 'E')              # the Mac's SMB mount: same files, other path
    monkeypatch.setattr(share, 'enabled', lambda: True)
    _machine(monkeypatch, tmp_path, 'desktop')
    d = core.cache_dir(str(clip))
    json.dump({'version': 9, 'cache': d, 'info': {'path': str(clip), 'nle_path': 'E:\\BRUTOS\\C0011.MP4'}}, open(os.path.join(d, 'index.json'), 'w'))
    json.dump([{'w': 'Olá', 's': 0.1, 'e': 0.4}], open(os.path.join(d, 'words.json'), 'w'))
    json.dump({'proxy': '/mnt/e/BRUTOS/Proxy/C0011.mp4'}, open(os.path.join(d, 'proxy.json'), 'w'))
    os.makedirs(os.path.join(d, 'faces_v1'))
    open(os.path.join(d, 'faces_v1', 'p1.jpg'), 'wb').write(b'jpg')
    return clip, str(tmp_path / 'Volumes' / 'E' / 'BRUTOS' / 'C0011.MP4')


def test_the_mac_adopts_the_desktop_analysis(tmp_path, monkeypatch):
    clip, mac_path = _setup(tmp_path, monkeypatch)
    assert share.publish(str(clip)) >= 3
    _machine(monkeypatch, tmp_path, 'mac')
    d = core.cache_dir(mac_path)                                         # first sight on the Mac
    idx = json.load(open(os.path.join(d, 'index.json')))
    assert idx['info']['path'] == mac_path and idx['cache'] == d
    assert json.load(open(os.path.join(d, 'words.json')))[0]['w'] == 'Olá'
    assert os.path.exists(os.path.join(d, 'faces_v1', 'p1.jpg'))
    assert not os.path.exists(os.path.join(d, 'proxy.json'))             # the proxy is found again on this machine


def test_another_take_with_the_same_name_is_not_adopted(tmp_path, monkeypatch):
    clip, mac_path = _setup(tmp_path, monkeypatch)
    share.publish(str(clip))
    t = os.stat(clip).st_mtime
    os.utime(clip, (t + 3600, t + 3600))                                 # same name and size, another recording
    _machine(monkeypatch, tmp_path, 'mac')
    assert not os.path.exists(os.path.join(core.cache_dir(mac_path), 'index.json'))


def test_newer_work_flows_back_and_forth(tmp_path, monkeypatch):
    clip, mac_path = _setup(tmp_path, monkeypatch)
    share.publish(str(clip))
    _machine(monkeypatch, tmp_path, 'mac')
    core.cache_dir(mac_path)
    _machine(monkeypatch, tmp_path, 'desktop')                           # the desktop redoes the transcript (Scribe)
    d = core.cache_dir(str(clip))
    time.sleep(0.01)
    json.dump([{'w': 'Oi', 's': 0.1, 'e': 0.3}], open(os.path.join(d, 'words.json'), 'w'))
    t = time.time() + 5
    os.utime(os.path.join(d, 'words.json'), (t, t))
    share.publish(str(clip))
    _machine(monkeypatch, tmp_path, 'mac')
    assert share.refresh(mac_path) == 1
    m = core.cache_dir(mac_path)
    assert json.load(open(os.path.join(m, 'words.json')))[0]['w'] == 'Oi'
    assert json.load(open(os.path.join(m, 'index.json')))['info']['path'] == mac_path


def test_publishing_is_opt_in(tmp_path, monkeypatch):
    clip, _ = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(share, 'enabled', lambda: False)
    assert share.publish(str(clip)) == 0 and not os.path.exists(os.path.dirname(share.shared_dir(str(clip))))


def test_a_job_copied_without_analysis_brings_it_from_the_desktop(tmp_path, monkeypatch):
    """The Mac has the job on an external drive but no _claudio: SSH to the desktop, find the job by its folder name,
    tar only the analysis of the missing clips, unpack it into the drive's copy and adopt it."""
    import shutil, subprocess, tarfile
    clip, _ = _setup(tmp_path, monkeypatch)
    share.publish(str(clip))
    job_desktop = tmp_path / 'E'
    hd = tmp_path / 'HD' / 'E'                                           # the same job on the external drive…
    (hd / 'BRUTOS').mkdir(parents=True)
    shutil.copy2(clip, hd / 'BRUTOS' / 'C0011.MP4')                       # …the footage copied, mtime kept, no _claudio
    _machine(monkeypatch, tmp_path, 'mac')
    env = {'SHARE_FROM': 'bernardo@desktop', 'SHARE_ROOTS': 'X:\\Fotos'}
    real_env = core.env
    monkeypatch.setattr(core, 'env', lambda n, d=None: env.get(n, real_env(n, d)))
    monkeypatch.setattr(share, '_reachable', lambda progress: (True, 'desktop ligado por Home Assistant (switch.x)'))
    asked = {}
    real_run = subprocess.run

    def fake_run(cmd, **kw):
        if cmd[0] == 'ssh' and 'powershell' in cmd:
            import base64
            asked['script'] = base64.b64decode(cmd[-1]).decode('utf-16-le')
            tar = tmp_path / 'remote.tar'
            with tarfile.open(tar, 'w') as t:
                t.add(job_desktop / 'BRUTOS' / '_claudio', arcname='BRUTOS/_claudio')
            return subprocess.CompletedProcess(cmd, 0, f'TAR C:/Temp/claudio-1.tar 1\n'.encode(), b'')
        if cmd[0] == 'scp':
            shutil.copy(tmp_path / 'remote.tar', cmd[-1]); return subprocess.CompletedProcess(cmd, 0, b'', b'')
        if cmd[0] == 'ssh':
            return subprocess.CompletedProcess(cmd, 0, b'', b'')
        return real_run(cmd, **kw)
    monkeypatch.setattr(share.subprocess, 'run', fake_run)
    mac_clip = str(hd / 'BRUTOS' / 'C0011.MP4')
    r = share.fetch_missing(str(hd / 'BRUTOS'), [mac_clip])
    assert r['fetched'] == 1 and 'Home Assistant' in r['note']
    assert "$job = 'E'" in asked['script'] and 'BRUTOS\\_claudio\\analise\\C0011.MP4-' in asked['script']
    idx = json.load(open(os.path.join(core.cache_dir(mac_clip), 'index.json')))
    assert idx['info']['path'] == mac_clip                                 # adopted, on the Mac's path
    assert os.path.isdir(hd / 'BRUTOS' / '_claudio' / 'analise')           # and kept on the drive for next time
    assert share.fetch_missing(str(hd / 'BRUTOS'), [mac_clip]) == {'fetched': 0}   # nothing missing any more


def test_the_desktop_never_fetches(tmp_path, monkeypatch):
    monkeypatch.setattr(core, 'env', lambda n, d=None: d)
    assert share.fetch_missing(str(tmp_path), [str(tmp_path / 'x.mp4')]) == {'fetched': 0}
