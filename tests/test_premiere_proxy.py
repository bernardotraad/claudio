"""premiere_proxy.js: two Claude Code sessions registered through the proxy share ONE premiere-pro-mcp, started by the
first proxy through the panel's bridge (the UXP port then stays with that one copy instead of the first session)."""
import json, os, shutil, socket, subprocess, sys, time, urllib.request

import pytest

PANEL = os.path.join(os.path.dirname(__file__), '..', 'src', 'claudio', 'panel_premiere')
NODE = shutil.which('node')
pytestmark = pytest.mark.skipif(not NODE, reason='node não instalado')

FAKE = r'''
// a stand-in premiere-pro-mcp: answers initialize / tools/list / tools/call with its pid and one env var
let b = '';
process.stdin.on('data', (d) => {
  b += d; let i;
  while ((i = b.indexOf('\n')) >= 0) {
    const m = JSON.parse(b.slice(0, i)); b = b.slice(i + 1);
    if (m.id === undefined) continue;
    const result = m.method === 'initialize' ? { protocolVersion: '2025-06-18', capabilities: { tools: {} }, serverInfo: { name: 'fake', version: '0' } }
      : m.method === 'tools/list' ? { tools: [{ name: 'whoami', inputSchema: { type: 'object' } }] }
      : { content: [{ type: 'text', text: JSON.stringify({ pid: process.pid, tag: process.env.FAKE_TAG }) }] };
    process.stdout.write(JSON.stringify({ jsonrpc: '2.0', id: m.id, result }) + '\n');
  }
});
'''


def _free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close()
    return p


class Session:
    def __init__(self, env):
        self.p = subprocess.Popen([NODE, os.path.join(PANEL, 'premiere_proxy.js'), '--', NODE, env['FAKE_JS']],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env, text=True, encoding='utf-8')
        self.n = 0

    def ask(self, method, params=None):
        self.n += 1
        self.p.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': self.n, 'method': method, 'params': params or {}}) + '\n')
        self.p.stdin.flush()
        return json.loads(self.p.stdout.readline())

    def close(self):
        self.p.stdin.close(); self.p.wait(10)


@pytest.fixture
def env(tmp_path):
    port = _free_port()
    fake = tmp_path / 'fake_mcp.js'
    fake.write_text(FAKE, encoding='utf-8')
    proxy = os.path.join(os.path.abspath(PANEL), 'premiere_proxy.js')
    (tmp_path / '.claude.json').write_text(json.dumps({'mcpServers': {'premiere-pro': {
        'type': 'stdio', 'command': NODE, 'args': [proxy, '--', NODE, str(fake)], 'env': {'FAKE_TAG': 'do-registro'}}}}))
    e = dict(os.environ, HOME=str(tmp_path), USERPROFILE=str(tmp_path), CLAUDIO_PREMIERE_BRIDGE_PORT=str(port),
             FAKE_JS=str(fake))
    yield e
    try:                                                             # the bridge runs detached: stop it
        h = json.loads(urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=2).read())
        for pid in (h.get('server'), h.get('pid')):
            if pid:
                if sys.platform == 'win32':
                    subprocess.run(['taskkill', '/F', '/PID', str(pid)], capture_output=True)
                else:
                    os.kill(pid, 9)
    except Exception:
        pass


def test_two_sessions_share_one_premiere_mcp(env):
    a = Session(env)
    try:
        init = a.ask('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 'a', 'version': '0'}})
        assert init['result']['serverInfo']['name'] == 'fake'        # the bridge answers from the server's own initialize
        assert a.ask('tools/list')['result']['tools'][0]['name'] == 'whoami'
        b = Session(env)
        try:
            b.ask('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 'b', 'version': '0'}})
            who_a = json.loads(a.ask('tools/call', {'name': 'whoami', 'arguments': {}})['result']['content'][0]['text'])
            who_b = json.loads(b.ask('tools/call', {'name': 'whoami', 'arguments': {}})['result']['content'][0]['text'])
            assert who_a['pid'] == who_b['pid']                      # ONE premiere-pro-mcp for both sessions
            assert who_a['tag'] == 'do-registro'                     # started from the real command after "--", its env
        finally:
            b.close()
    finally:
        a.close()
