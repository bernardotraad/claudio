"""Multicam sources: cameras from the editor's Premiere project (originals and proxies on different drives) and
camera folders where only the NLE proxies were kept."""
import gzip
import os

from claudio import core, multicam, run


def _touch(p):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, 'wb').close()
    return p


def test_project_media_pairs_proxies_across_drives(tmp_path):
    ext_drive, laptop = tmp_path / 'hd', tmp_path / 'mac'
    o1 = _touch(str(ext_drive / 'VIDEOS' / 'CAM A' / 'C0001.MP4'))
    o2 = _touch(str(ext_drive / 'VIDEOS' / 'CAM B' / 'C0001.MP4'))     # same stem on another camera
    p1 = _touch(str(laptop / 'VIDEOS' / 'CAM A' / 'Proxy' / 'C0001_Proxy.mov'))
    p2 = _touch(str(laptop / 'VIDEOS' / 'CAM B' / 'Proxy' / 'C0001_Proxy.mov'))
    wav = _touch(str(laptop / 'AUDIOS' / '01 - song.wav'))
    xml = ''.join(f'<Media><ActualMediaFilePath>{p}</ActualMediaFilePath></Media>' for p in (o1, o2, p1, p2, wav))
    prproj = tmp_path / 'job.prproj'
    prproj.write_bytes(gzip.compress(f'<PremiereData>{xml}</PremiereData>'.encode()))

    media = {m['original']: m['proxy'] for m in core.project_media(str(prproj))}
    assert media == {o1: p1, o2: p2, wav: None}

    cams, ext, proxies, offline = run._cams_from_project(str(prproj), set())
    assert sorted(c['clips'] for c in cams.values()) == [1, 1]
    assert [e['files'] for e in ext] == [1]
    assert proxies == {o1: p1, o2: p2} and offline == []

    core.register_proxies(proxies)
    assert core.proxy_for(o2) == p2


def test_offline_original_falls_back_to_its_proxy(tmp_path):
    p1 = _touch(str(tmp_path / 'mac' / 'CAM A' / 'Proxy' / 'C0001_Proxy.mov'))
    gone = str(tmp_path / 'unplugged' / 'CAM A' / 'C0001.MP4')
    prproj = tmp_path / 'job.prproj'
    prproj.write_bytes(gzip.compress(
        f'<x><ActualMediaFilePath>{gone}</ActualMediaFilePath><ActualMediaFilePath>{p1}</ActualMediaFilePath></x>'.encode()))
    cams, _, _, offline = run._cams_from_project(str(prproj), set())
    assert offline == [gone]
    assert list(cams.values())[0]['files'] == [core.nle_path(p1)]


def test_camera_folder_with_only_proxies(tmp_path):
    a = _touch(str(tmp_path / 'VIDEOS' / '01 be' / 'Proxy' / 'BER_1729_Proxy.mov'))
    _touch(str(tmp_path / 'VIDEOS' / '02 rona' / '133A9902.MP4'))
    _touch(str(tmp_path / 'VIDEOS' / '02 rona' / 'Proxy' / '133A9902_Proxy.mov'))
    assert core.media_files(str(tmp_path / 'VIDEOS' / '01 be'), core.VIDEO_EXT) == [a]       # proxies are all there is
    assert [os.path.basename(p) for p in multicam._files(str(tmp_path / 'VIDEOS' / '02 rona'), core.VIDEO_EXT)] == \
        ['133A9902.MP4']                                                                      # originals win
    cams, _ = run._cams_from_folders(str(tmp_path), set())
    assert sorted(k.split(' — ')[0] for k in cams) == ['01 be', '02 rona']


def test_project_media_with_windows_paths(tmp_path, monkeypatch):
    """A project saved by Premiere on Windows (D:\\…, &amp; escapes), read by claudio in WSL: pairs by name all the same."""
    monkeypatch.setattr(core, 'IS_WSL', True)
    paths = [r'D:\AnR &amp; Co\VIDEOS\01 be\BER_1729.MP4', r'C:\Users\b\Proxies\01 be\Proxy\BER_1729_Proxy.mov',
             r'D:\AnR &amp; Co\AUDIOS\01 - song.wav']
    prproj = tmp_path / 'job.prproj'
    prproj.write_bytes(gzip.compress(''.join(f'<ActualMediaFilePath>{p}</ActualMediaFilePath>' for p in paths).encode()))
    media = {m['original']: m['proxy'] for m in core.project_media(str(prproj))}
    assert media == {'/mnt/d/AnR & Co/VIDEOS/01 be/BER_1729.MP4': '/mnt/c/Users/b/Proxies/01 be/Proxy/BER_1729_Proxy.mov',
                     '/mnt/d/AnR & Co/AUDIOS/01 - song.wav': None}


def test_sync_progress_feeds_the_panels_bar(tmp_path):
    import json
    prog, done = run._sync_progress({'folder': str(tmp_path)})
    read = lambda: json.load(open(tmp_path / '_claudio' / 'progress.json'))
    prog('audio C0001.MP4', 0.5)
    j = read()
    assert j['state'] == 'running' and j['kind'] == 'sync' and abs(j['fraction'] - 0.125) < 1e-6 and 'C0001.MP4' in j['line']
    prog('transcript BER_1730.MP4', 0.0)
    assert read()['fraction'] == 0.35 and 'BER_1730.MP4' in read()['line']
    prog('audio late.MP4', 0.1)                       # never goes backwards
    assert read()['fraction'] == 0.35
    done('done', '21 de 21 clipe(s) posicionados')
    assert read()['state'] == 'done' and read()['fraction'] == 1.0
