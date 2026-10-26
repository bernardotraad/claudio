/* Cláudio, o Editor — one Premiere MCP that stays up, so its UXP bridge stays connected.

   premiere-pro-mcp binds the UXP loopback port (127.0.0.1:7777) for as long as its process lives, and the panel used to
   start a fresh copy for every message (claude -p): between messages nobody held the port and the UXP plugin showed
   "disconnected"; during one, the plugin needed ~2 s to reconnect and early UXP calls failed. This keeps ONE copy
   running (the ~/.claude.json "premiere-pro" registration, with its PREMIERE_UXP_TOKEN) and serves it to every
   message as Streamable HTTP on 127.0.0.1:7788/mcp (JSON responses, no SSE). Request ids are remapped so messages
   running at the same time never mix; each client's initialize is answered from the one the server already did.

   Started (detached) by the panel when /health does not answer; one copy per machine (the port is the lock). Exits
   after Premiere has been closed for 10 minutes. node premiere_bridge.js [--port 7788] */
const http = require('http'), fs = require('fs'), os = require('os'), path = require('path'), cp = require('child_process');

const PORT = +(process.argv[process.argv.indexOf('--port') + 1] || 0) || +process.env.CLAUDIO_PREMIERE_BRIDGE_PORT || 7788;
const LOG = path.join(os.tmpdir(), 'claudio-premiere-bridge.log');
const log = (m) => { try { fs.appendFileSync(LOG, `${new Date().toISOString()} ${String(m).replace(/[A-Za-z0-9_\-]{40,}/g, '<redacted>')}\n`); } catch (e) {} };

function registration() {
  const all = JSON.parse(fs.readFileSync(path.join(os.homedir(), '.claude.json'), 'utf8')).mcpServers || {};
  const m = all['premiere-pro'];
  if (!m || !m.command) throw new Error('premiere-pro não está registrado no ~/.claude.json');
  return m;
}

let child = null, ready = null, initResult = null, nextId = 1, buf = '';
const pending = new Map();                         // our id → { resolve, id: the client's id }

function start() {
  const m = registration();
  child = cp.spawn(m.command, m.args || [], { env: Object.assign({}, process.env, m.env || {}), stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true });
  log(`MCP do Premiere iniciado (pid ${child.pid})`);
  buf = '';
  child.stderr.on('data', (d) => log(d));
  child.stdout.on('data', (d) => {
    buf += d;
    let i;
    while ((i = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1);
      if (line) fromServer(line);
    }
  });
  child.on('exit', (code) => {
    log(`MCP do Premiere saiu (${code}); reinicia em 2 s`);
    for (const p of pending.values()) p.resolve({ jsonrpc: '2.0', id: p.id, error: { code: -32000, message: 'o MCP do Premiere reiniciou no meio do pedido' } });
    pending.clear(); child = null; initResult = null;
    ready = new Promise((r) => setTimeout(() => r(start()), 2000));     // requests in the gap wait for the new one
  });
  // the first request binds the UXP port: initialize right away, not on the first message
  return call({ jsonrpc: '2.0', id: 0, method: 'initialize',
                 params: { protocolVersion: '2025-06-18', capabilities: {}, clientInfo: { name: 'claudio-premiere-bridge', version: '1' } } })
    .then((r) => { initResult = r.result; toServer({ jsonrpc: '2.0', method: 'notifications/initialized' }); log('inicializado'); });
}

function toServer(msg) { if (child) child.stdin.write(JSON.stringify(msg) + '\n'); }

function call(msg) {
  return new Promise((resolve) => {
    const id = nextId++;
    pending.set(id, { resolve, id: msg.id });
    toServer({ ...msg, id });
  });
}

function fromServer(line) {
  let msg;
  try { msg = JSON.parse(line); } catch (e) { return; }
  if (msg.id !== undefined && (msg.result !== undefined || msg.error !== undefined)) {
    const p = pending.get(msg.id);
    if (p) { pending.delete(msg.id); p.resolve({ ...msg, id: p.id }); }
    return;
  }
  if (msg.id !== undefined && msg.method)          // a request from the server (ping…): nobody to ask, answer here
    toServer(msg.method === 'ping' ? { jsonrpc: '2.0', id: msg.id, result: {} }
      : { jsonrpc: '2.0', id: msg.id, error: { code: -32601, message: 'não suportado pela ponte do painel' } });
  // notifications (tools/list_changed, logging) have no single client to go to: dropped
}

async function handle(msg) {
  if (msg.id === undefined) {                       // a client notification
    if (msg.method === 'notifications/cancelled') {
      for (const [ours, p] of pending) if (p.id === (msg.params || {}).requestId) toServer({ ...msg, params: { ...msg.params, requestId: ours } });
    }
    return null;
  }
  await ready;
  if (msg.method === 'initialize') return { jsonrpc: '2.0', id: msg.id, result: initResult };
  if (msg.method === 'ping') return { jsonrpc: '2.0', id: msg.id, result: {} };
  return call(msg);
}

const server = http.createServer((req, res) => {
  if (req.url === '/health') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    return res.end(JSON.stringify({ ok: true, pid: process.pid, server: child ? child.pid : null, ready: !!initResult }));
  }
  if (!req.url.startsWith('/mcp')) { res.writeHead(404); return res.end(); }
  if (req.method === 'DELETE') { res.writeHead(200); return res.end(); }
  if (req.method !== 'POST') { res.writeHead(405, { Allow: 'POST' }); return res.end(); }   // no server→client SSE stream
  let body = '';
  req.on('data', (d) => (body += d));
  req.on('end', async () => {
    let msg;
    try { msg = JSON.parse(body); } catch (e) {
      res.writeHead(400, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({ jsonrpc: '2.0', id: null, error: { code: -32700, message: 'JSON inválido' } }));
    }
    const out = (await Promise.all((Array.isArray(msg) ? msg : [msg]).map(handle))).filter(Boolean);
    if (!out.length) { res.writeHead(202); return res.end(); }
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(Array.isArray(msg) ? out : out[0]));
  });
});
server.requestTimeout = 0; server.headersTimeout = 0; server.timeout = 0;      // renders and exports take minutes
server.on('error', (e) => { log(`porta ${PORT}: ${e.code || e}`); process.exit(e.code === 'EADDRINUSE' ? 0 : 1); });
server.listen(PORT, '127.0.0.1', () => { log(`ponte em http://127.0.0.1:${PORT}/mcp`); ready = start(); });

// Premiere closed for 10 min → stop (the panel starts it again when it opens)
let away = 0;
setInterval(() => {
  const q = process.platform === 'win32' ? ['tasklist', ['/FI', 'IMAGENAME eq Adobe Premiere Pro.exe', '/NH']] : ['pgrep', ['-f', 'Adobe Premiere Pro']];
  cp.execFile(q[0], q[1], { windowsHide: true }, (err, out) => {
    away = /Premiere/i.test(out || '') ? 0 : away + 1;
    if (away >= 10) { log('Premiere fechado há 10 min: saindo'); if (child) child.kill(); process.exit(0); }
  });
}, 60000).unref();
