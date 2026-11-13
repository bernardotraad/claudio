/* Cláudio, o Editor — the Claude Code "premiere-pro" registration, as a thin stdio front for premiere_bridge.js.

   Every Claude Code session used to start its own premiere-pro-mcp, and each copy tries to bind the UXP loopback port
   (127.0.0.1:7777): the first one up kept the UXP plugin, the panel's bridge and every later session ran without it,
   and closing a session handed the port to whichever copy retried first. This relays the session's stdio JSON-RPC to
   the ONE bridge (http://127.0.0.1:7788/mcp), starting it (detached) when /health does not answer, so the sessions and
   the panel share one premiere-pro-mcp and one UXP connection.

   Registered by claudio (premiere_mcp.sync) as:  node premiere_proxy.js -- <the real premiere-pro-mcp command…>
   The bridge reads the real command after "--". Nothing but JSON-RPC goes to stdout; notes go to the bridge's log. */
const http = require('http'), fs = require('fs'), os = require('os'), path = require('path'), cp = require('child_process');

const PORT = +process.env.CLAUDIO_PREMIERE_BRIDGE_PORT || 7788;
const LOG = path.join(os.tmpdir(), 'claudio-premiere-bridge.log');
const log = (m) => { try { fs.appendFileSync(LOG, `${new Date().toISOString()} [proxy ${process.pid}] ${m}\n`); } catch (e) {} };

function request(method, urlPath, body, timeout) {
  return new Promise((resolve, reject) => {
    const rq = http.request({ host: '127.0.0.1', port: PORT, path: urlPath, method, timeout: timeout || 0,
                              headers: body ? { 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream',
                                                'Content-Length': Buffer.byteLength(body) } : {} }, (r) => {
      let b = ''; r.setEncoding('utf8'); r.on('data', (d) => (b += d)); r.on('end', () => resolve({ status: r.statusCode, body: b }));
    });
    rq.on('error', reject);
    rq.on('timeout', () => rq.destroy(new Error('timeout')));
    if (body) rq.write(body);
    rq.end();
  });
}

const healthy = () => request('GET', '/health', null, 800).then((r) => { try { return JSON.parse(r.body).ok === true; } catch (e) { return false; } }, () => false);

let starting = null;
function ensureBridge() {
  if (!starting) starting = (async () => {
    if (await healthy()) return true;
    log('ponte fora do ar: iniciando');
    cp.spawn(process.execPath, [path.join(__dirname, 'premiere_bridge.js'), '--port', String(PORT)],
             { detached: true, stdio: 'ignore', windowsHide: true }).unref();
    for (let i = 0; i < 60; i++) {                   // the bridge answers /health at once; the server initializes in ~3 s
      await new Promise((r) => setTimeout(r, 250));
      if (await healthy()) return true;
    }
    log('a ponte não respondeu em 15 s');
    return false;
  })().finally(() => { setTimeout(() => (starting = null), 0); });
  return starting;
}

const out = (msg) => process.stdout.write(JSON.stringify(msg) + '\n');
const fail = (msg, text) => msg.id !== undefined && out({ jsonrpc: '2.0', id: msg.id, error: { code: -32000, message: text } });

async function relay(line) {
  let msg;
  try { msg = JSON.parse(line); } catch (e) { return; }
  for (let attempt = 0; attempt < 2; attempt++) {
    if (attempt || starting) await ensureBridge();              // first try straight; (re)start only when it fails
    try {
      const r = await request('POST', '/mcp', line);            // no timeout: renders and exports take minutes
      if (r.status === 202 || !r.body) return;                  // a notification
      return process.stdout.write(r.body.trim() + '\n');
    } catch (e) {
      if (attempt) return fail(msg, `a ponte do Premiere (porta ${PORT}) não respondeu: ${e.message}`);
      log(`pedido falhou (${e.message}): reiniciando a ponte`);
    }
  }
}

let buf = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (d) => {
  buf += d;
  let i;
  while ((i = buf.indexOf('\n')) >= 0) {
    const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1);
    if (line) relay(line);                                       // concurrent: a long export never blocks a quick read
  }
});
process.stdin.on('end', () => process.exit(0));
ensureBridge();
