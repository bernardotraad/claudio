"""The premiere-pro registration in ~/.claude.json is kept lean: compact schemas, the edit tools, names the installed
package lacks dropped (an unknown name would stop premiere-pro-mcp from starting), the editor's own choices kept."""
import json

import pytest


@pytest.fixture
def setup(tmp_path, monkeypatch):
    from claudio import core, premiere_mcp as pm
    pkg = tmp_path / 'npm' / 'node_modules' / 'premiere-pro-mcp'
    (pkg / 'dist' / 'tools').mkdir(parents=True)
    (pkg / 'package.json').write_text('{"version": "9.9.9"}')
    have = [n for n in pm.TOOLS if n != 'slip_edit']                       # this "version" has no slip_edit
    (pkg / 'dist' / 'tools' / 'a.js').write_text('const T = {\n' + ''.join(f'    {n}: {{}},\n' for n in have) + '};')
    cfg = tmp_path / '.claude.json'
    cfg.write_text(json.dumps({'numStartups': 7, 'mcpServers': {
        'claudio': {'command': 'claudio'},
        'premiere-pro': {'type': 'stdio', 'command': 'node', 'args': [str(pkg / 'dist' / 'index.js')],
                         'env': {'PREMIERE_UXP_TOKEN': 'tok', 'PREMIERE_MCP_SCHEMA_PROFILE': 'full'}}}}))
    settings = {}
    monkeypatch.setattr(pm, 'configs', lambda: [str(cfg)])
    monkeypatch.setattr(pm, 'proxy_path', lambda: None)               # panel not installed
    monkeypatch.setattr(pm, 'CACHE', str(tmp_path / 'cache.json'))
    monkeypatch.setattr(core, 'env', lambda name, default=None: settings.get(name.lower(), default))
    return pm, cfg, settings


def test_sync_writes_the_lean_env_and_keeps_the_rest(setup):
    pm, cfg, _ = setup
    [r] = pm.sync()
    assert r['changed'] and r['version'] == '9.9.9' and r['dropped'] == ['slip_edit']
    data = json.loads(cfg.read_text())
    env = data['mcpServers']['premiere-pro']['env']
    assert env['PREMIERE_UXP_TOKEN'] == 'tok'                       # untouched
    assert env['PREMIERE_MCP_SCHEMA_PROFILE'] == 'full'             # the editor's choice wins
    assert env['PREMIERE_MCP_TOOL_PACKS'] == 'essential'
    names = env['PREMIERE_MCP_TOOL_LIST'].split(',')
    assert 'slip_edit' not in names and {'overwrite_clip', 'import_fcp_xml', 'add_to_timeline'} <= set(names)
    assert data['numStartups'] == 7 and data['mcpServers']['claudio'] == {'command': 'claudio'}
    [again] = pm.sync()
    assert not again['changed']                                      # idempotent: no rewrite on every start


def test_extra_tools_and_opt_out(setup):
    pm, cfg, settings = setup
    settings['premiere_mcp_extra_tools'] = 'detect_beats, overwrite_clip'
    pm.sync()
    names = json.loads(cfg.read_text())['mcpServers']['premiere-pro']['env']['PREMIERE_MCP_TOOL_LIST'].split(',')
    assert names[-1] == 'detect_beats' and names.count('overwrite_clip') == 1
    settings['premiere_mcp_lean'] = False
    before = cfg.read_text()
    settings['premiere_mcp_shared'] = False
    [r] = pm.sync()
    assert not r['changed'] and not r['lean'] and cfg.read_text() == before


def test_no_premiere_registration_is_left_alone(setup):
    pm, cfg, _ = setup
    cfg.write_text(json.dumps({'mcpServers': {'claudio': {'command': 'claudio'}}}))
    assert pm.sync() == [] and 'premiere-pro' not in cfg.read_text()


def test_sessions_go_through_the_panels_bridge(setup, monkeypatch):
    pm, cfg, settings = setup
    proxy = r'C:\Users\x\AppData\Roaming\Adobe\CEP\extensions\com.claudio.panel\premiere_proxy.js'
    monkeypatch.setattr(pm, 'proxy_path', lambda: proxy)
    real = json.loads(cfg.read_text())['mcpServers']['premiere-pro']
    [r] = pm.sync()
    assert r['changed'] and r['shared']
    e = json.loads(cfg.read_text())['mcpServers']['premiere-pro']
    assert e['command'] == 'node' and e['args'] == [proxy, '--', real['command'], *real['args']]
    assert e['env']['PREMIERE_UXP_TOKEN'] == 'tok' and 'PREMIERE_MCP_TOOL_LIST' in e['env']
    assert pm.real_command(e) == (real['command'], real['args'])      # what the bridge starts
    assert r['version'] == '9.9.9'                                    # the package is still found through the args
    [again] = pm.sync()
    assert not again['changed']
    settings['premiere_mcp_shared'] = False                           # back to one copy per session
    pm.sync()
    e = json.loads(cfg.read_text())['mcpServers']['premiere-pro']
    assert e['command'] == real['command'] and e['args'] == real['args']


def test_a_node_command_is_kept_for_the_proxy(setup, monkeypatch):
    pm, _, _ = setup
    e = pm.shaped({'command': r'C:\Program Files\nodejs\node.exe', 'args': [r'x\dist\index.js']}, '/p/premiere_proxy.js', lean=False)
    assert e['command'] == r'C:\Program Files\nodejs\node.exe'
    assert e['args'] == ['/p/premiere_proxy.js', '--', r'C:\Program Files\nodejs\node.exe', r'x\dist\index.js']
    assert pm.shaped(e, '/p/premiere_proxy.js', lean=False) == e      # already through the proxy: unchanged
