"""The motion-designer skill comes from its author's repository at a tested commit: installed when missing, updated
only when Cláudio installed it, a hand-installed copy left alone unless --force (kept beside it), nothing outside the
skill's folder extracted. No network here: the download is a tarball built in the test."""
import io, json, os, tarfile

import pytest

from claudio import skill


def _tarball(extra=()):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tf:
        for name, data in [('skills-abc/motion-designer/templates/timeline.html', b'<html>'),
                           ('skills-abc/motion-designer/SKILL.md', b'---\nname: motion-designer\n---'),
                           ('skills-abc/other-skill/SKILL.md', b'no'), *extra]:
            ti = tarfile.TarInfo(name); ti.size = len(data); tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


@pytest.fixture
def place(tmp_path, monkeypatch):
    monkeypatch.setenv('CLAUDIO_MOTION_SKILL', str(tmp_path / 'motion-designer'))
    monkeypatch.setattr(skill, '_download', lambda url: _tarball([('skills-abc/motion-designer/../../evil.txt', b'x')]))
    return tmp_path


def test_install_update_and_hand_installed(place):
    r = skill.install(progress=lambda *_: None)
    d = place / 'motion-designer'
    assert r['commit'] == skill.COMMIT and (d / 'templates' / 'timeline.html').exists() and not (d / 'other-skill').exists()
    assert not (place / 'evil.txt').exists() and not (place.parent / 'evil.txt').exists()     # no path escapes
    assert json.loads((d / skill.MARKER).read_text())['repo'] == skill.REPO
    assert skill.install(progress=lambda *_: None)['skipped']                                   # same commit: nothing
    (d / skill.MARKER).unlink()                                                                 # as if copied by hand
    assert 'à mão' in skill.install(progress=lambda *_: None)['why']
    r = skill.install(force=True, progress=lambda *_: None)
    assert os.path.isdir(r['backup']) and skill.installed()['commit'] == skill.COMMIT
    assert os.path.basename(os.path.dirname(r['backup'])) == 'skills-backup'    # not beside it: no second skill loads


def test_a_tarball_without_the_skill_changes_nothing(place, monkeypatch):
    monkeypatch.setattr(skill, '_download', lambda url: _tarball()[:0] or _empty())
    with pytest.raises(Exception):
        skill.install(progress=lambda *_: None)
    assert not (place / 'motion-designer').exists()


def _empty():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tf:
        ti = tarfile.TarInfo('skills-abc/README.md'); ti.size = 2; tf.addfile(ti, io.BytesIO(b'hi'))
    return buf.getvalue()
