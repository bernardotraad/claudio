/* decupa panel — talk to Claude Code (with the decupa + Premiere MCPs) from inside Premiere.
   Each message runs `claude -p … --output-format stream-json --resume <session>` in the job folder, with the current
   Premiere context (project, active sequence, playhead, selected clips) attached. Tools are limited to the MCP servers
   and file reading: the panel never runs shell commands. The editor stays in control: Claude proposes, asks, and
   builds new sequences; it never overwrites the editor's. */
const cp = require('child_process');
const path = require('path');
const fs = require('fs');
const os = require('os');

const $ = (s) => document.querySelector(s);
const log = $('#log');
let child = null, ctx = {}, sessionKey = null;

function evalHost(script) {
  return new Promise((res) => window.__adobe_cep__.evalScript(script, (r) => res(r)));
}

async function readContext() {
  try { ctx = JSON.parse(await evalHost('decupaContext()')); } catch (e) { ctx = {}; }
  const proj = ctx.project ? path.basename(ctx.project) : 'sem projeto';
  $('#ctx').textContent = `${proj}${ctx.sequence ? ' · ' + ctx.sequence : ''}${ctx.selection && ctx.selection.length ? ' · ' + ctx.selection.length + ' selecionado(s)' : ''}`;
  sessionKey = 'decupa-session:' + (ctx.project || 'none');
  return ctx;
}

function jobFolder() {
  // Bernardo's layout: <job>/#projeto/<name>.prproj  → work from <job>
  if (!ctx.project) return os.homedir();
  let d = path.dirname(ctx.project);
  if (path.basename(d).startsWith('#')) d = path.dirname(d);
  return d;
}

function claudeExe() {
  const cands = [process.env.DECUPA_CLAUDE, path.join(os.homedir(), '.local', 'bin', 'claude.exe'),
                 path.join(os.homedir(), '.local', 'bin', 'claude'), '/usr/local/bin/claude', '/opt/homebrew/bin/claude'];
  return cands.find((c) => c && fs.existsSync(c)) || 'claude';
}

function add(cls, text) {
  const d = document.createElement('div');
  d.className = 'msg ' + cls;
  d.textContent = text;
  log.appendChild(d);
  log.scrollTop = log.scrollHeight;
  return d;
}

function rawFolder() {
  // BRUTOS is the convention; other jobs use VIDEOS / RAW / FOOTAGE / MATERIAL
  const names = ['BRUTOS', 'VIDEOS', 'VÍDEOS', 'RAW', 'FOOTAGE', 'MATERIAL', 'CARTOES', 'CARTÕES'];
  try {
    const dirs = fs.readdirSync(jobFolder(), { withFileTypes: true }).filter((d) => d.isDirectory()).map((d) => d.name);
    const hit = names.map((n) => dirs.find((d) => d.toUpperCase() === n)).find(Boolean);
    if (hit) return path.join(jobFolder(), hit);
  } catch (e) {}
  return jobFolder();
}

const QUICK = {
  index: () => `Analise o material bruto deste trabalho: index_folder na pasta "${rawFolder()}" (mostre o progresso). Se houver perfil do cliente (client_profile), use o vocabulário dele. No fim, me diga em 4–6 linhas o que tem no material.`,
  selects: () => `Faça as selects deste trabalho com make_selects (pasta "${rawFolder()}", XML em "${path.join(jobFolder(), '#projeto', 'decupa_selects.xml')}") e importe no Premiere com import_media. Depois me resuma as escolhas.`,
  notes: () => `Exporte a sequência ativa ("${ctx.sequence || ''}") como FCP XML com o MCP do Premiere, rode compare_with_nle contra a última versão do projeto decupa deste trabalho, e me mostre o que eu mudei e as notas (marcadores) que deixei. Não aplique nada ainda: proponha e espere meu ok.`,
  qc: () => `Faça o QC da sequência ativa ("${ctx.sequence || ''}", ${ctx.frame_size || ''}) antes de eu exportar: exporte como FCP XML com o MCP do Premiere e rode qc_timeline. Liste os problemas e pergunte se quero que você marque cada um como marcador na timeline.`,
  status: () => `Rode index_status e me diga o progresso em uma linha.`,
  overview: () => `Faça a visão geral do projeto: project_digest na pasta "${rawFolder()}", entenda sobre o que é o trabalho, salve com save_project_overview em "${jobFolder()}" e me resuma em 4–6 linhas. Depois me pergunte a duração aproximada do vídeo final e a trilha (uma música específica ou um mood/gênero) e proponha 2–4 direcionamentos concretos a partir do material. Eu escolho um ou explico a minha ideia.`,
  captions: () => `Legendas da sequência ativa ("${ctx.sequence || ''}", ${ctx.frame_size || ''}). Exporte a sequência como FCP XML com o MCP do Premiere e siga os 3 passos: 1) caption_sentences e corrija SÓ a gramática (concordância, regência, crase, pontuação), sem mudar as palavras do falante nem nomes de marca — me mostre a tabela fala → legenda; 2) caption_style_preview (aspect conforme o frame da sequência; se houver perfil do cliente, passe client) e me pergunte qual estilo; 3) com a minha escolha, gere o .srt (captions_srt) e o overlay (caption_overlay) em "${path.join(jobFolder(), '#projeto', 'decupa_overlays')}" e importe o overlay na trilha de vídeo mais alta em 0 s.`,
  vault: () => `Registre este trabalho no vault (conector Vault), seguindo as regras do CLAUDE.md do vault (leia-o com read_note antes): 1) nota do trabalho com cliente, data, pasta "${jobFolder()}", material (câmeras, horas, cor/log, áudio), decisões de montagem desta conversa, versões entregues e onde estão os arquivos (.md da decupagem, XMLs, perfil do cliente); 2) entrada no diário de hoje (append_diary) com um resumo de 2–4 linhas e link pra nota; 3) se o cliente tiver página, linke. Não grave senhas nem dados sensíveis. No fim me diga o que foi escrito.`,
};

function contextBlock() {
  const sel = (ctx.selection || []).map((s) => `  - ${s.name} (${s.track_type}) timeline ${(+s.start_s).toFixed(2)}–${(+s.end_s).toFixed(2)}s, fonte ${(+s.in_s).toFixed(2)}–${(+s.out_s).toFixed(2)}s${s.media ? ' · ' + s.media : ''}`).join('\n');
  return `[Contexto do Premiere — painel decupa]\nPasta do trabalho: ${jobFolder()}\nProjeto: ${ctx.project || '-'}\nSequência ativa: ${ctx.sequence || '-'} (${ctx.frame_size || '-'})\nPlayhead: ${ctx.playhead_s != null ? (+ctx.playhead_s).toFixed(2) + 's' : '-'}\n${sel ? 'Clipes selecionados:\n' + sel : 'Nenhum clipe selecionado.'}\nRegras: o editor está no controle — proponha antes de mudar; nunca sobrescreva a sequência dele (crie uma nova); só renderize se ele pedir. Memória: este chat é separado das outras sessões; o que importar para depois vai para o vault (conector Vault) e para os arquivos do decupa no disco — ofereça registrar no vault quando houver decisões.\n\n`;
}

async function send(text) {
  if (!text.trim() || child) return;
  await readContext();
  add('user', text);
  const sid = localStorage.getItem(sessionKey);
  const args = ['-p', contextBlock() + text, '--output-format', 'stream-json', '--verbose',
                '--allowedTools', 'mcp__decupa,mcp__premiere-pro,mcp__davinci-resolve,mcp__claude_ai_Vault,Read,Glob,Grep',
                // allowedTools only pre-approves; this BLOCKS shell and file edits from the panel
                '--disallowedTools', 'Bash,PowerShell,Edit,Write,NotebookEdit'];
  if (sid) args.push('--resume', sid);
  $('#send').disabled = true; $('#stop').disabled = false;
  $('#status').textContent = 'pensando…';
  let cur = null, buf = '';
  // stdin closed: otherwise claude -p waits 3 s for piped input ("no stdin data received")
  // apps opened from the Dock/Finder get a minimal PATH on macOS: add where claude, node, uv tools and ffmpeg live
  const extra = process.platform === 'win32' ? [] : [path.join(os.homedir(), '.local', 'bin'), '/opt/homebrew/bin', '/usr/local/bin'];
  const env = Object.assign({}, process.env, { PATH: [...extra, process.env.PATH || ''].join(path.delimiter) });
  child = cp.spawn(claudeExe(), args, { cwd: jobFolder(), windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'], env });
  child.stdout.on('data', (chunk) => {
    buf += chunk.toString();
    let i;
    while ((i = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1);
      if (!line) continue;
      let ev; try { ev = JSON.parse(line); } catch (e) { continue; }
      if (ev.type === 'system' && ev.session_id) localStorage.setItem(sessionKey, ev.session_id);
      if (ev.type === 'assistant' && ev.message && ev.message.content) {
        for (const b of ev.message.content) {
          if (b.type === 'text' && b.text.trim()) { cur = add('claude', b.text); $('#status').textContent = ''; }
          if (b.type === 'tool_use') {
            const name = b.name.replace(/^mcp__/, '').replace('__', ' › ');
            add('tool', name);
            $('#status').textContent = 'usando ' + name + '…';
          }
        }
      }
      if (ev.type === 'result') {
        if (ev.session_id) localStorage.setItem(sessionKey, ev.session_id);
        const s = ev.total_cost_usd != null ? ` · US$ ${ev.total_cost_usd.toFixed(2)}` : '';
        add('meta', `${ev.is_error ? 'erro' : 'pronto'} · ${(ev.duration_ms / 1000).toFixed(0)} s${s}`);
      }
    }
  });
  child.stderr.on('data', (d) => { const t = d.toString().trim(); if (t) $('#status').textContent = t.slice(-160); });
  child.on('close', (code) => {
    child = null; $('#send').disabled = false; $('#stop').disabled = true;
    if (code && code !== 0) add('claude err', `O Claude Code terminou com código ${code}. Ele está instalado e logado no Windows? (${claudeExe()})`);
    $('#status').textContent = '';
    readContext();
  });
}

$('#send').onclick = () => { const t = $('#input').value; $('#input').value = ''; send(t); };
$('#input').addEventListener('keydown', (e) => { if (e.key === 'Enter' && e.ctrlKey) { e.preventDefault(); $('#send').click(); } });
$('#stop').onclick = () => { if (child) { child.kill(); add('meta', 'parado'); } };
$('#newchat').onclick = () => { localStorage.removeItem(sessionKey); log.innerHTML = ''; add('meta', 'nova conversa'); };
document.querySelectorAll('#quick button').forEach((b) => (b.onclick = async () => { await readContext(); send(QUICK[b.dataset.q]()); }));

// indexing progress: read <footage>/_decupa/progress.json (written by the background indexer) every 2 s
function findProgressFiles() {
  const out = [];
  const root = jobFolder();
  const visit = (d, depth) => {
    let ents = [];
    try { ents = fs.readdirSync(d, { withFileTypes: true }); } catch (e) { return; }
    for (const e of ents) {
      if (!e.isDirectory()) continue;
      const p = path.join(d, e.name);
      if (e.name === '_decupa') { const f = path.join(p, 'progress.json'); if (fs.existsSync(f)) out.push(f); }
      else if (depth < 2 && !e.name.startsWith('#') && !/^proxies?$/i.test(e.name)) visit(p, depth + 1);
    }
  };
  visit(root, 0);
  return out;
}
let progFiles = [], progScan = 0;
function showProgress() {
  if (Date.now() - progScan > 15000) { progFiles = findProgressFiles(); progScan = Date.now(); }
  let best = null;
  for (const f of progFiles) {
    try { const j = JSON.parse(fs.readFileSync(f, 'utf8')); if (!best || (j.updated || 0) > (best.updated || 0)) best = j; } catch (e) {}
  }
  const box = $('#prog');
  if (best && best.state === 'running' && Date.now() / 1000 - (best.updated || 0) > 60) best.state = 'interrupted';   // writer died
  if (!best || (best.state !== 'running' && Date.now() / 1000 - (best.updated || 0) > 600)) { box.style.display = 'none'; return; }
  box.style.display = 'block';
  box.className = best.state === 'done' ? 'done' : (best.state === 'running' ? '' : 'bad');
  const name = path.basename(String(best.folder || '').replace(/\\/g, '/'));
  box.querySelector('.txt').textContent = `${best.state === 'running' ? 'Indexando' : best.state === 'done' ? 'Indexado' : 'Indexação parada'} ${name}: ${best.line || ''}`;
  box.querySelector('.fill').style.width = Math.round((best.fraction || 0) * 100) + '%';
  const key = 'decupa-overview-offered:' + (best.folder || '');
  if (best.state === 'done' && !localStorage.getItem(key)) {
    localStorage.setItem(key, '1');
    const d = add('claude', `Material todo indexado (${best.files_total || ''} clipes). Quer que eu faça a visão geral do projeto e proponha direcionamentos?  `);
    const b = document.createElement('button'); b.textContent = 'Fazer visão geral'; b.className = 'primary';
    b.onclick = async () => { await readContext(); send(QUICK.overview()); }; d.appendChild(b);
  }
}
setInterval(showProgress, 2000);

readContext().then(() => { add('meta', `pasta do trabalho: ${jobFolder()}`); showProgress(); });
setInterval(() => { if (!child) readContext(); }, 4000);
