import os

from claudio import panel


def test_sync_reinstalls_only_a_stale_installed_panel(tmp_path, monkeypatch):
    monkeypatch.setattr(panel, '_ext_dir', lambda: str(tmp_path))
    monkeypatch.setattr(panel, '_debug_mode', lambda: None)
    assert not panel.outdated() and panel.sync() is None          # not installed: leave it alone
    dst = panel.install()
    assert not panel.outdated() and panel.sync() is None          # fresh
    with open(os.path.join(dst, 'main.js'), 'a') as f:
        f.write('// old')
    open(os.path.join(dst, '.DS_Store'), 'w').close()
    assert panel.outdated()
    assert panel.sync() == dst and not panel.outdated()
