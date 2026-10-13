"""Paths the editor really uses: '#projeto', spaces, apostrophes, accents."""
import os, pathlib, urllib.parse

from claudio import core

NASTY = "/tmp/ACOUSTIC N' ROLL/2026-09-26 LEOPOLDINA @ MG/#projeto/legendas ção.xml"


def test_file_url_roundtrip():
    u = core.file_url(NASTY)
    assert '#' not in u and ' ' not in u                 # a raw '#' would start a URL fragment
    assert core.local_path(u) == core.local_path(core.nle_path(NASTY)) or core.local_path(u) == NASTY


def test_file_urls_from_premiere():
    if not core.IS_WSL:
        return
    for u, want in [("file://localhost/E%3a/Fotos/2026/ACOUSTIC%20N'%20ROLL/x.MP4", "/mnt/e/Fotos/2026/ACOUSTIC N' ROLL/x.MP4"),
                    ('file://localhost/E%3A/a%20%23b/c.mp4', '/mnt/e/a #b/c.mp4'), ('file://E:/x/y.mp4', '/mnt/e/x/y.mp4'),
                    ('file:///mnt/e/x.mp4', '/mnt/e/x.mp4'), (core.file_url('/tmp/a b/#p/x.xml'), '/tmp/a b/#p/x.xml')]:
        assert core.local_path(u) == want, u


def test_browser_url_keeps_hash_in_path():
    u = pathlib.Path(NASTY).as_uri()                     # what the overlay renderer opens
    assert urllib.parse.urlparse(u).fragment == ''
    assert urllib.parse.unquote(urllib.parse.urlparse(u).path).endswith('#projeto/legendas ção.xml')


def test_env_falls_back_to_old_name(monkeypatch):
    monkeypatch.delenv('CLAUDIO_XYZ', raising=False)
    monkeypatch.setenv('DECUPA_XYZ', 'old')
    assert core.env('XYZ') == 'old'
    monkeypatch.setenv('CLAUDIO_XYZ', 'new')
    assert core.env('XYZ') == 'new'


def test_data_dir_migrates_old_folder(tmp_path):
    job = tmp_path / 'job'
    (job / '_decupa').mkdir(parents=True)
    (job / '_decupa' / 'a.jpg').write_bytes(b'x')
    (job / 'a.md').write_text('<!-- decupa:auto:begin -->\n![s](_decupa/a.jpg)\n<!-- decupa:auto:end -->\nnotas\n', encoding='utf-8')
    (job / '_DECUPA.md').write_text('old', encoding='utf-8')
    d = core.data_dir(str(job))
    assert d.endswith('_claudio') and os.path.exists(os.path.join(d, 'a.jpg'))
    md = (job / 'a.md').read_text(encoding='utf-8')
    assert '(_claudio/a.jpg)' in md and '<!-- claudio:auto:begin -->' in md and 'notas' in md
    assert not (job / '_DECUPA.md').exists()
