/* Cláudio, o Editor — panel: talk to Claude Code (with the claudio + Premiere MCPs) from inside Premiere.
   Each message runs `claude -p … --output-format stream-json --resume <session>` in the job folder, with the current
   Premiere context (project, active sequence, playhead, selected clips, tracks, and the sequence already exported as
   FCP XML) attached, and only the claudio + Premiere MCPs loaded. Tools are limited to the MCP servers
   and file reading: the panel never runs shell commands. The editor stays in control: Claude proposes, asks, and
   builds new sequences; it never overwrites the editor's. */
const cp = require('child_process');
const path = require('path');
const fs = require('fs');
const os = require('os');

const $ = (s) => document.querySelector(s);
const log = $('#log');
let child = null, ctx = {}, sessionKey = null, curProject, hist = [];

function evalHost(script) {
  return new Promise((res) => window.__adobe_cep__.evalScript(script, (r) => res(r)));
}

async function readContext() {
  try { ctx = JSON.parse(await evalHost('claudioContext()')); } catch (e) { ctx = {}; }
  // the slate: project · sequence (+ selection) · playhead
  $('#s-proj').textContent = ctx.project ? path.basename(ctx.project, path.extname(ctx.project)) : 'sem projeto';
  const nsel = ctx.selection && ctx.selection.length;
  $('#s-seq').textContent = (ctx.sequence || '—') + (nsel ? ` · ${nsel} sel.` : '');
  $('#s-seq').title = ctx.sequence ? `${ctx.sequence} (${ctx.frame_size || ''})` : '';
  $('#s-proj').title = ctx.project || '';
  $('#s-tc').textContent = ctx.playhead_s != null ? clock(+ctx.playhead_s) : '--:--';
  sessionKey = 'claudio-session:' + (ctx.project || 'none');
  try {                                   // keep the chat from before the rename (decupa → Cláudio)
    const old = localStorage.getItem('decupa-session:' + (ctx.project || 'none'));
    if (old && !localStorage.getItem(sessionKey)) localStorage.setItem(sessionKey, old);
  } catch (e) {}
  if (ctx.project !== curProject) { curProject = ctx.project; loadHist(); }
  return ctx;
}

// The conversation is saved next to the project (<name>.claudio-chat.json) and reloaded when the panel opens on it
// again, so it never starts from zero: the visible messages and the Claude Code session that --resume continues.
function histPath() {
  if (!ctx.project) return null;
  return path.join(path.dirname(ctx.project), path.basename(ctx.project, path.extname(ctx.project)) + '.claudio-chat.json');
}

function saveHist() {
  const p = histPath();
  if (!p) return;
  try {
    fs.writeFileSync(p + '.tmp', JSON.stringify({ session_id: localStorage.getItem(sessionKey), messages: hist.slice(-400) }, null, 1));
    fs.renameSync(p + '.tmp', p);
  } catch (e) {}
}

function loadHist() {
  log.innerHTML = ''; hist = [];
  const p = histPath();
  let h = null;
  try { if (p && fs.existsSync(p)) h = JSON.parse(fs.readFileSync(p, 'utf8')); } catch (e) {}
  if (!h || !h.messages || !h.messages.length) return;
  if (h.session_id && !localStorage.getItem(sessionKey)) localStorage.setItem(sessionKey, h.session_id);
  for (const m of h.messages) {
    const d = add(m.cls, m.text, false, m.t || Date.now());
    if (m.cls === 'claude') importButtons(d, m.text);
    hist.push(m);
  }
  add('meta', `conversa anterior recarregada (${h.messages.length} mensagens) — o Claude continua de onde parou`, false);
}

function jobFolder() {
  // Bernardo's layout: <job>/#projeto/<name>.prproj  → work from <job>
  if (!ctx.project) return os.homedir();
  let d = path.dirname(ctx.project);
  if (path.basename(d).startsWith('#')) d = path.dirname(d);
  return d;
}

function claudeExe() {
  const cands = [process.env.CLAUDIO_CLAUDE || process.env.DECUPA_CLAUDE, path.join(os.homedir(), '.local', 'bin', 'claude.exe'),
                 path.join(os.homedir(), '.local', 'bin', 'claude'), '/usr/local/bin/claude', '/opt/homebrew/bin/claude'];
  return cands.find((c) => c && fs.existsSync(c)) || 'claude';
}

function clock(sec) {
  const h = Math.floor(sec / 3600), m = Math.floor(sec / 60) % 60, x = (sec % 60).toFixed(1).padStart(4, '0');
  return (h ? h + ':' + String(m).padStart(2, '0') : String(m).padStart(2, '0')) + ':' + x;
}

// Claude answers in Markdown: render the parts it uses (paragraphs, lists, tables, code, bold/italic, headings),
// escaping everything first so nothing in a reply can inject HTML into the panel.
function esc(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
function inline(s) {
  return esc(s).replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
    .replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, '$1<i>$2</i>');
}
// a Premiere label name in a table cell (multicam colours) gets a swatch of that colour
const LABEL_HEX = { cerulean: '#2ba3dc', mango: '#f0962c', forest: '#5fa94c', magenta: '#d4529f', yellow: '#e6cf3a', violet: '#a68ae0',
  teal: '#2db3a6', rose: '#e8768f', caribbean: '#22c4a0', lavender: '#c8a2e8', tan: '#c9a77c', brown: '#9a6b4a', purple: '#8e5ccf',
  iris: '#7d7df0', blue: '#3d6fe0', green: '#3fae5a' };
function swatch(c) { const k = c.replace(/[`*]/g, '').trim().toLowerCase(); return LABEL_HEX[k] ? `<span class="sw" style="background:${LABEL_HEX[k]}"></span>` : ''; }
const LI = /^\s*([-*•]|\d+[.)])\s+/, ROW = /^\s*\|.*\|\s*$/;
function md(src) {
  const L = String(src).replace(/\r/g, '').split('\n'), out = [];
  let i = 0;
  while (i < L.length) {
    const l = L[i];
    if (/^```/.test(l)) {
      const b = []; i++;
      while (i < L.length && !/^```/.test(L[i])) b.push(L[i++]);
      i++; out.push('<pre>' + esc(b.join('\n')) + '</pre>'); continue;
    }
    if (ROW.test(l) && i + 1 < L.length && /^\s*\|?[\s:|-]+\|?\s*$/.test(L[i + 1]) && L[i + 1].includes('-')) {
      const cells = (r) => r.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
      const head = cells(l), rows = []; i += 2;
      while (i < L.length && ROW.test(L[i])) rows.push(cells(L[i++]));
      out.push('<table><thead><tr>' + head.map((c) => '<th>' + inline(c) + '</th>').join('') + '</tr></thead><tbody>' +
               rows.map((r) => '<tr>' + r.map((c) => '<td>' + (swatch(c) ? `<span class="lab">${swatch(c)}${inline(c)}</span>` : inline(c)) + '</td>').join('') + '</tr>').join('') + '</tbody></table>');
      continue;
    }
    let m = l.match(/^#{1,6}\s+(.*)/);
    if (m) { out.push('<h4>' + inline(m[1]) + '</h4>'); i++; continue; }
    if (LI.test(l)) {
      const n = l.match(/^\s*(\d+)[.)]/), items = [];
      while (i < L.length && LI.test(L[i])) {
        let t = L[i++].replace(LI, '');
        while (i < L.length && /^\s{2,}\S/.test(L[i]) && !LI.test(L[i])) t += ' ' + L[i++].trim();
        items.push('<li>' + inline(t) + '</li>');
      }
      out.push(n ? `<ol start="${n[1]}">${items.join('')}</ol>` : `<ul>${items.join('')}</ul>`); continue;
    }
    if (/^>\s?/.test(l)) {
      const b = [];
      while (i < L.length && /^>\s?/.test(L[i])) b.push(L[i++].replace(/^>\s?/, ''));
      out.push('<blockquote>' + inline(b.join(' ')) + '</blockquote>'); continue;
    }
    if (!l.trim()) { i++; continue; }
    const p = [L[i++]];
    while (i < L.length && L[i].trim() && !/^```|^#{1,6}\s|^>\s?/.test(L[i]) && !LI.test(L[i]) && !ROW.test(L[i])) p.push(L[i++]);
    out.push('<p>' + p.map(inline).join('<br>') + '</p>');
  }
  return out.join('');
}

function add(cls, text, keep = true, t = Date.now()) {
  if (keep) { hist.push({ cls, text, t }); saveHist(); }
  const d = document.createElement('div');
  d.className = 'msg ' + cls;
  if (cls === 'user' || cls.startsWith('claude') || cls.startsWith('local')) {
    const hd = document.createElement('div'), bd = document.createElement('div');
    hd.className = 'hd'; bd.className = 'bd';
    const when = new Date(t);
    hd.innerHTML = `<b>${cls === 'user' ? 'você' : cls.startsWith('local') ? 'painel · sem IA' : 'cláudio'}</b><time>${String(when.getHours()).padStart(2, '0')}:${String(when.getMinutes()).padStart(2, '0')}</time>`;
    if (cls === 'user') bd.textContent = text; else bd.innerHTML = md(text);
    d.append(hd, bd);
  } else {
    d.textContent = text;
  }
  log.appendChild(d);
  log.scrollTop = log.scrollHeight;
  return d;
}

// XML paths in Claude's answer get an "Importar no Premiere" button: one file per click, project saved first,
// Premiere's own dialogs shown. (Importing XML through the Premiere MCP crashed Premiere.)
const XML_RE = process.platform === 'win32' ? /[A-Za-z]:\\[^\n"`*<>|]*?\.xml\b/g : /\/[^\n"`*<>|]*?\.xml\b/g;
function importButtons(d, text) {
  const seen = new Set();
  for (const m of text.match(XML_RE) || []) {
    const p = m.trim();
    if (seen.has(p) || !fs.existsSync(p)) continue;
    seen.add(p);
    const b = document.createElement('button');
    b.className = 'import';
    b.textContent = 'Importar no Premiere: ' + path.basename(p);
    b.onclick = async () => {
      b.disabled = true; b.textContent = 'salvando o projeto e importando ' + path.basename(p) + '…';
      let r = {};
      try { r = JSON.parse(await evalHost('claudioImportXml(' + JSON.stringify(p) + ')')); } catch (e) { r = { ok: false, error: String(e) }; }
      b.textContent = r.ok ? `importado: ${path.basename(p)}` : `não importou ${path.basename(p)}: ${r.error || 'cancelado'}`;
      if (!r.ok) b.disabled = false;
    };
    d.appendChild(document.createElement('br'));
    d.appendChild(b);
  }
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
  selects: () => `Faça as selects deste trabalho com make_selects (pasta "${rawFolder()}", XML em "${path.join(jobFolder(), '#projeto', 'claudio_selects.xml')}") e escreva o caminho completo do XML (o painel mostra o botão de importar) — não importe XML pelo MCP do Premiere. Depois me resuma as escolhas.`,
  notes: () => `Com o XML da sequência ativa ("${ctx.sequence || ''}") que o painel já exportou (está no contexto), rode compare_with_nle contra a última versão entregue pelo Cláudio deste trabalho, e me mostre o que eu mudei e as notas (marcadores) que deixei. Não aplique nada ainda: proponha e espere meu ok.`,
  qc: () => `Faça o QC da sequência ativa ("${ctx.sequence || ''}", ${ctx.frame_size || ''}) antes de eu exportar: rode qc_timeline no XML da sequência ativa que o painel já exportou (está no contexto). Liste os problemas e pergunte se quero que você marque cada um como marcador na timeline.`,
  status: () => `Rode index_status e me diga o progresso em uma linha.`,
  overview: () => `Faça a visão geral do projeto: project_digest na pasta "${rawFolder()}", entenda sobre o que é o trabalho, salve com save_project_overview em "${jobFolder()}" e me resuma em 4–6 linhas. Depois me pergunte a duração aproximada do vídeo final e a trilha (uma música específica ou um mood/gênero) e proponha 2–4 direcionamentos concretos a partir do material. Eu escolho um ou explico a minha ideia.`,
  captions: () => `Legendas da sequência ativa ("${ctx.sequence || ''}", ${ctx.frame_size || ''}). Use o XML da sequência ativa que o painel já exportou (está no contexto) e siga os 3 passos: 1) caption_sentences e corrija SÓ a gramática (concordância, regência, crase, pontuação), sem mudar as palavras do falante nem nomes de marca — me mostre a tabela fala → legenda; 2) caption_style_preview (aspect conforme o frame da sequência; se houver perfil do cliente, passe client) e me pergunte qual estilo; 3) com a minha escolha, gere o .srt (captions_srt) e o overlay (caption_overlay) em "${path.join(jobFolder(), '#projeto', 'claudio_overlays')}" e importe o overlay em 0 s numa trilha de vídeo NOVA, acima de todas as que têm clipes (crie a trilha; as trilhas da sequência já estão no contexto).`,
  multicam: () => `Multicâmera deste trabalho, no lugar do PluralEyes. 1) Liste as pastas de "${jobFolder()}" (material de câmera e áudio; ignore #projeto e proxies) e proponha o mapeamento: cada pasta de câmera vira uma câmera com nome curto (ex.: "CAM A — A7III"), e gravadores externos (Zoom, mesa, lapela) vão em external_audio. Proponha uma cor de label do Premiere por câmera e uma para o áudio externo (Cerulean, Mango, Forest, Magenta, Yellow, Violet, Teal, Rose, Caribbean…) numa tabela câmera → pasta → nº de clipes → cor, e ME PERGUNTE antes de rodar. 2) Com o meu ok, rode sync_multicam (correct_drift true, labels com as cores, out_xml "${path.join(jobFolder(), '#projeto', 'claudio_sync.xml')}"). 3) Resuma: clipes posicionados, os aproximados (sem áudio em comum, posicionados pelo relógio da câmera) e o drift do gravador. 4) Confira com multicam_monitor em 2 ou 3 momentos em que todas as câmeras estão rodando e me diga se algo parece fora de sincronia. 5) Escreva o caminho completo do XML (o painel mostra o botão de importar; não importe pelo MCP do Premiere) e explique o próximo passo: a "Synced Sequence" chega com V1..Vn por câmera e cada câmera (vídeo, áudio e bin) na sua cor; dali eu corto direto ou crio uma Multi-Camera Source Sequence.`,
  vault: () => `Registre este trabalho no vault (conector Vault), seguindo as regras do CLAUDE.md do vault (leia-o com read_note antes): 1) nota do trabalho com cliente, data, pasta "${jobFolder()}", material (câmeras, horas, cor/log, áudio), decisões de montagem desta conversa, versões entregues e onde estão os arquivos (.md da decupagem, XMLs, perfil do cliente); 2) entrada no diário de hoje (append_diary) com um resumo de 2–4 linhas e link pra nota; 3) se o cliente tiver página, linke. Não grave senhas nem dados sensíveis. No fim me diga o que foi escrito.`,
};

// ── safety net ──────────────────────────────────────────────────────────────────────────────────────────────
// Before each message: the active sequence's tracks are snapshotted and backed up as FCP XML (#projeto/claudio_backups).
// After the answer: any clip that was already there and got moved, trimmed or removed is reported, with the backup to
// import back — and Claude is told on the next message. New clips on new tracks are fine and not reported.
let lastWarning = '';
async function snapshot(id) {
  try { const r = JSON.parse(await evalHost('claudioSnapshot(' + JSON.stringify(id || '') + ')')); return r.ok ? r : null; } catch (e) { return null; }
}
function backupDir() {
  if (!ctx.project) return null;
  const d = path.join(path.dirname(ctx.project), 'claudio_backups');
  try { fs.mkdirSync(d, { recursive: true }); } catch (e) {}
  return d;
}
async function backup(snap) {
  const d = backupDir();
  if (!d || !snap) return null;
  const stamp = new Date().toISOString().replace(/[-:]/g, '').replace('T', '-').slice(0, 15);
  const p = path.join(d, `${snap.name.replace(/[\\/:*?"<>|]/g, '_').slice(0, 60)} — antes ${stamp}.xml`);
  let ok = false;
  try { ok = JSON.parse(await evalHost('claudioBackup(' + JSON.stringify(snap.id) + ',' + JSON.stringify(p) + ')')).ok; } catch (e) {}
  try {                                                     // keep the last 30
    const old = fs.readdirSync(d).filter((f) => f.endsWith('.xml')).map((f) => path.join(d, f))
      .sort((a, b) => fs.statSync(b).mtimeMs - fs.statSync(a).mtimeMs).slice(30);
    old.forEach((f) => fs.unlinkSync(f));
  } catch (e) {}
  return ok ? p : null;
}
const clipKey = (c) => `${c.name}|${(+c.start).toFixed(3)}|${(+c.end).toFixed(3)}|${(+c.in).toFixed(3)}|${(+c.out).toFixed(3)}`;
function changedClips(before, after) {
  if (!before || !after) return [];
  const out = [];
  for (const t of before.tracks) {
    if (!t.clips.length) continue;
    const now = (after.tracks.find((x) => x.track === t.track) || { clips: [] }).clips;
    const keys = new Set(now.map(clipKey));
    for (const c of t.clips) {
      if (keys.has(clipKey(c))) continue;
      const same = now.find((x) => x.name === c.name && Math.abs(x.start - c.start) < 0.05);
      const how = !same ? 'removido ou movido' : (Math.abs(same.end - c.end) > 0.02 || Math.abs(same.in - c.in) > 0.02 ? 'cortado' : 'alterado');
      out.push(`${t.track} · ${c.name} (${clock(+c.start)}–${clock(+c.end)}): ${how}`);
    }
  }
  return out;
}
function warnChanges(list, snap, bk) {
  const d = add('claude err', `**Atenção: clipes que já estavam na sequência "${snap.name}" mudaram nesta resposta.** Se você não pediu isso, volte para a versão de antes.\n\n` +
    list.slice(0, 12).map((x) => '- ' + x).join('\n') + (list.length > 12 ? `\n- … e mais ${list.length - 12}` : '') +
    (bk ? `\n\nBackup de antes: \`${bk}\`` : ''));
  if (bk) importButtons(d, bk);
  lastWarning = `[Aviso do painel: na sua última resposta, clipes que já estavam na sequência "${snap.name}" foram alterados sem estar previsto: ${list.slice(0, 8).join('; ')}. Explique ao editor o que aconteceu e não mexa em trilhas ocupadas.]\n`;
}

// ── local jobs: claudio-run straight from the panel, no AI model, no credits ─────────────────────────────────
function claudioCmd() {
  // the same registration Claude Code uses (~/.claude.json → mcpServers.claudio), with claudio → claudio-run
  try {
    const cfg = JSON.parse(fs.readFileSync(path.join(os.homedir(), '.claude.json'), 'utf8'));
    const m = cfg.mcpServers && cfg.mcpServers.claudio;
    if (m) {
      if (/wsl(\.exe)?$/i.test(m.command)) {
        const a = m.args || [];
        const exe = a[a.length - 1];
        const d = a.indexOf('-d');
        return { cmd: m.command, pre: [...(d >= 0 ? ['-d', a[d + 1]] : []), '--exec', exe + '-run'] };
      }
      return { cmd: m.command + '-run', pre: [] };
    }
  } catch (e) {}
  return { cmd: path.join(os.homedir(), '.local', 'bin', 'claudio-run'), pre: [] };
}
function runLocal(action, args) {
  return new Promise((res) => {
    const c = claudioCmd();
    const b64 = 'b64:' + Buffer.from(JSON.stringify(args || {}), 'utf8').toString('base64');
    let out = '';
    const p = cp.spawn(c.cmd, [...c.pre, action, b64], { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    p.stdout.on('data', (d) => { out += d.toString(); });
    p.on('close', () => { try { res(JSON.parse(out.trim().split('\n').pop())); } catch (e) { res({ ok: false, error: out.slice(-400) || 'sem resposta' }); } });
    p.on('error', (e) => res({ ok: false, error: String(e) }));
  });
}
async function exportActive() {
  await readContext();
  if (!ctx.sequence) { add('local err', 'Abra a sequência na timeline primeiro.', false); return null; }
  const snap = await snapshot('');
  const p = snap && await backup(snap);
  if (!p) add('local err', 'Não consegui exportar a sequência ativa como XML.', false);
  return p;
}
async function localJob(label, fn) {
  if (child || localBusy) return;
  localBusy = true; document.body.classList.add('busy'); toolStart(label + ' (local, sem IA)');
  try { await fn(); } catch (e) { add('local err', String(e)); }
  localBusy = false; document.body.classList.remove('busy'); toolStart(null); $('#status').textContent = '';
}
let localBusy = false;
const dur = (s) => clock(+s);
const LOCAL = {
  status: () => localJob('status', async () => {
    const r = await runLocal('status', { folder: rawFolder() });
    add('local', r.ok ? (r.jobs.length ? r.jobs.map((j) => `**${path.basename(j.folder.replace(/\\/g, '/'))}**: ${j.line}`).join('\n') : 'Nenhuma indexação nesta pasta.') : r.error);
  }),
  index: () => localJob('indexação', async () => {
    const r = await runLocal('index', { folder: rawFolder() });
    add('local', r.ok ? (r.started ? `Indexação iniciada em segundo plano: ${r.files} vídeos em \`${rawFolder()}\`. A barra embaixo mostra o progresso; pode fechar o painel.` : `Já está indexando: ${r.line}`) : r.error);
    progScan = 0;
  }),
  multicam: () => multicamPlan(),
  stereo: () => {
    const d = add('local', 'Áudio que sai de um lado só (lapela ou um microfone em L ou R) passa a sair dos dois. Quer também **nivelar as falas** (cada clipe com fala no mesmo volume, até +12 dB)? O resultado vem numa sequência nova.', false);
    for (const [txt, norm] of [['Só deixar estéreo', false], ['Estéreo + nivelar falas', true]]) {
      const b = document.createElement('button'); b.textContent = txt;
      b.onclick = () => { d.querySelectorAll('button').forEach((x) => (x.disabled = true)); stereoRun(norm); };
      d.appendChild(b);
    }
  },
  flatten: () => localJob('desaninhar', async () => {
    const x = await exportActive(); if (!x) return;
    const r = await runLocal('flatten', { nle_xml: x });
    if (!r.ok) return add('local err', r.error);
    const m = add('local', `**${r.nests_expanded.length} aninhada(s)** viraram **${r.clips_video} clipes de vídeo** e ${r.clips_audio} de áudio direto nas trilhas.` +
      (r.dropped.length ? `\n\nFicou de fora (copie da sequência antiga para a trilha de cima): ${r.dropped.join('; ')}` : '') + `\n\nXML: \`${r.nle_path}\``);
    importButtons(m, r.nle_path);
  }),
  qc: () => localJob('QC', async () => {
    const x = await exportActive(); if (!x) return;
    const r = await runLocal('qc', { nle_xml: x, platform: null });
    if (!r.ok) return add('local err', r.error);
    const iss = (r.issues || []).slice(0, 15).map((i) => `- \`${i.tc}\` ${i.severity === 'high' ? '**' + i.issue + '**' : i.issue}${i.detail ? ' — ' + i.detail : ''}`).join('\n');
    const a = r.audio || {};
    add('local', `**QC de "${ctx.sequence}"**: ${(r.issues || []).length} ponto(s).\n\n${iss || '- nada na imagem'}\n\n**Áudio:** ${a.lufs != null ? a.lufs + ' LUFS' : '—'} (alvo ${a.platform_target_lufs}), pico ${a.true_peak_db} dBTP` +
      ((a.notes || []).length ? '\n' + a.notes.map((n) => '- ' + n).join('\n') : '') +
      ((a.ducking || []).length ? '\n\n| começa | termina | abaixar a música |\n|---|---|---|\n' + a.ducking.map((d) => `| \`${d.tc}\` | ${dur(d.end)} | ${d.lower_music_by_db} dB |`).join('\n') : ''));
  }),
};
async function multicamPlan() {
  await localJob('multicâmera: lendo as pastas', async () => {
    const r = await runLocal('multicam_plan', { folder: jobFolder() });
    if (!r.ok) return add('local err', r.error);
    const cams = Object.entries(r.cameras || {});
    const rows = cams.map(([n, c]) => `| ${n} | \`${path.basename(c.folder.replace(/\\/g, '/'))}\` | ${c.clips} | ${c.label} |`)
      .concat((r.external_audio || []).map((e) => `| áudio externo | \`${path.basename(e.folder.replace(/\\/g, '/'))}\` | ${e.files} | ${r.audio_label} |`));
    const d = add('local', `**Multicâmera** — achei ${cams.length} câmera(s)` + ((r.external_audio || []).length ? ' e um gravador externo' : '') + ':\n\n' +
      '| fonte | pasta | arquivos | cor |\n|---|---|---|---|\n' + rows.join('\n') +
      (r.ready ? '\n\nSincronizo pelo áudio (sem IA) e gero a "Synced Sequence" com cada câmera na sua cor? Se o mapeamento estiver errado, peça ao Cláudio.' : `\n\n${r.note}`));
    if (!r.ready) return;
    const go = document.createElement('button'); go.textContent = 'Sincronizar (sem IA)';
    const ai = document.createElement('button'); ai.textContent = 'Ajustar com o Cláudio (IA)';
    go.onclick = () => { go.disabled = ai.disabled = true; multicamSync(r); };
    ai.onclick = async () => { go.disabled = ai.disabled = true; await readContext(); send(QUICK.multicam()); };
    d.appendChild(go); d.appendChild(ai);
  });
}
async function multicamSync(plan) {
  await localJob('multicâmera: sincronizando pelo áudio', async () => {
    const cameras = {}, labels = {};
    for (const [n, c] of Object.entries(plan.cameras)) { cameras[n] = c.folder; labels[n] = c.label; }
    if (plan.audio_label) labels.AUDIO = plan.audio_label;
    const r = await runLocal('sync', { cameras, external_audio: (plan.external_audio || []).map((e) => e.folder), labels,
                                       out_xml: path.join(jobFolder(), '#projeto', 'claudio_sync.xml') });
    if (!r.ok) return add('local err', r.error);
    const clips = r.clips || [];
    const placed = clips.filter((c) => c.start_s != null);
    const approx = placed.filter((c) => /relógio|aprox/i.test(String(c.method || '')));
    const dr = r.drift;
    const drift = dr ? [`${dr.reference}: ${dr.ppm} ppm` + (dr.corrected_file ? ` → \`${path.basename(dr.corrected_file.replace(/\\/g, '/'))}\`` : '')] : [];
    const m = add('local', `**Sincronizado:** ${placed.length} de ${clips.length} clipe(s)` + (approx.length ? `, ${approx.length} pelo relógio da câmera (aproximados — sem áudio em comum)` : '') + '.' +
      ((r.unplaced || []).length ? `\n\nNão posicionados: ${r.unplaced.slice(0, 8).join(', ')}` : '') +
      (drift.length ? `\n\nDrift do gravador corrigido: ${drift.join('; ')}` : '') +
      `\n\nXML: \`${r.xml.nle_path}\` — ${r.xml.tracks_video} trilha(s) de vídeo, ${r.xml.tracks_audio} de áudio, cada câmera na sua cor.`);
    importButtons(m, r.xml.nle_path);
    const chk = document.createElement('button'); chk.textContent = 'Conferir o sync com os olhos (IA)';
    chk.onclick = async () => { chk.disabled = true; await readContext();
      send(`Confira o sync multicâmera com multicam_monitor (sync_json "${r.sync_json}") em 2 ou 3 momentos em que todas as câmeras estão rodando e me diga em poucas linhas se algo parece fora de sincronia.`); };
    m.appendChild(chk);
  });
}

async function stereoRun(normalize) {
  await localJob('áudio estéreo', async () => {
    const x = await exportActive(); if (!x) return;
    const r = await runLocal('stereo', { nle_xml: x, normalize });
    if (!r.ok) return add('local err', r.error);
    const fx = r.made_dual_mono || [];
    const m = add('local', `**${fx.length} clipe(s)** com áudio de um lado só agora saem dos dois lados` +
      (fx.length ? ':\n\n| clipe | na timeline | lado com som |\n|---|---|---|\n' + fx.slice(0, 20).map((f) => `| ${f.clip} | ${dur(f.tl)} | ${f.side} |`).join('\n') : '.') +
      ((r.two_live_channels_kept || []).length ? `\n\nMantidos com dois microfones: ${r.two_live_channels_kept.length}.` : '') +
      (normalize ? `\n\n**${(r.normalized || []).length} fala(s) niveladas** em ${r.target_lufs} LUFS` + ((r.normalized || []).some((n) => n.capped) ? ' (algumas no teto de +12 dB: o áudio da câmera estava muito baixo)' : '') + '.' : '') +
      ((r.skipped || []).length ? `\n\nSem mexer: ${r.skipped.join('; ')}` : '') + `\n\nSequência nova: **${r.sequence}** · XML: \`${r.nle_path}\``);
    importButtons(m, r.nle_path);
  });
}

// what Claude would otherwise ask the Premiere MCP for (export the sequence, list the tracks) goes in the context:
// the panel already exported the active sequence (backup) and read every track (snapshot) before this message
function tracksLine(snap) {
  if (!snap) return '';
  const fmt = (k) => snap.tracks.filter((t) => t.track[0] === k).map((t) => `${t.track}: ${t.clips.length || 'vazia'}`).join(' · ');
  return `Trilhas (clipes por trilha): ${fmt('V')} | ${fmt('A')}\n`;
}
function contextBlock(snap, xml) {
  const sel = (ctx.selection || []).map((s) => `  - ${s.name} (${s.track_type}) timeline ${(+s.start_s).toFixed(2)}–${(+s.end_s).toFixed(2)}s, fonte ${(+s.in_s).toFixed(2)}–${(+s.out_s).toFixed(2)}s${s.media ? ' · ' + s.media : ''}`).join('\n');
  const warn = lastWarning; lastWarning = '';
  return warn + `[Contexto do Premiere — painel Cláudio, o Editor]\nPasta do trabalho: ${jobFolder()}\nProjeto: ${ctx.project || '-'}\nSequência ativa: ${ctx.sequence || '-'} (${ctx.frame_size || '-'})\nPlayhead: ${ctx.playhead_s != null ? (+ctx.playhead_s).toFixed(2) + 's' : '-'}\n${tracksLine(snap)}${xml ? `XML da sequência ativa, exportado agora pelo painel: ${xml}\n` : ''}${sel ? 'Clipes selecionados:\n' + sel : 'Nenhum clipe selecionado.'}\nRegras: para LER a sequência (compare_with_nle, qc_timeline, caption_sentences, stereo_fix, flatten_timeline…) use o XML acima e as trilhas acima — não exporte nem liste pelo MCP do Premiere; use o MCP do Premiere só para MEXER no Premiere (importar mídia, criar trilha, colocar clipe, marcadores). O editor está no controle — proponha antes de mudar; nunca sobrescreva a sequência dele (crie uma nova); só renderize se ele pedir. Nunca aninhe sequências: os clipes montados vão direto nas trilhas de UMA sequência (o editor corrige a cor take a take); para juntar partes, monte uma sequência só ou rode flatten_timeline. Overlays, legendas e qualquer mídia nova entram SEMPRE numa trilha NOVA acima de todas as que têm clipes: nunca use overwrite/insert numa trilha ocupada (adjustment layer, títulos, b-roll) — liste as trilhas antes e, se não tiver certeza, pergunte. XML (.xml) nunca vai pelo import_media do Premiere (derruba o Premiere): escreva o caminho completo do XML na resposta — o painel mostra um botão \"Importar no Premiere\" — ou ele importa com Ctrl+I; mídia comum (.mov/.wav/.srt) pode ir pelo import_media. Memória: este chat é separado das outras sessões; o que importar para depois vai para o vault (conector Vault) e para os arquivos do claudio no disco — ofereça registrar no vault quando houver decisões.\n\n`;
}

// Only claudio + premiere-pro are loaded (the same registrations as ~/.claude.json): otherwise every message also
// starts every other MCP and claude.ai connector (Gmail, Drive, Resolve…) and pays for their tools and instructions.
// The claude.ai connectors (Vault, Storyblocks) only exist in the full setup, so a message that asks for them gets it.
const WANTS_CONNECTORS = /\bvault\b|storyblocks|banco de (imagens|vídeos)|stock/i;
function mcpArgs(text) {
  if (WANTS_CONNECTORS.test(text)) return [];
  try {
    const all = (JSON.parse(fs.readFileSync(path.join(os.homedir(), '.claude.json'), 'utf8')).mcpServers) || {};
    if (!all.claudio || !all['premiere-pro']) return [];
    const f = path.join(os.tmpdir(), 'claudio-panel-mcp.json');
    fs.writeFileSync(f, JSON.stringify({ mcpServers: { claudio: all.claudio, 'premiere-pro': all['premiere-pro'] } }));
    return ['--strict-mcp-config', '--mcp-config', f];
  } catch (e) { return []; }
}

// a running tool shows its name and a clock, so a long render never looks frozen
let toolTimer = null;
function toolStart(name) {
  clearInterval(toolTimer); toolTimer = null;
  if (!name) { if (child) $('#status').textContent = 'pensando…'; return; }
  const t0 = Date.now();
  const tick = () => { const s = Math.round((Date.now() - t0) / 1000); $('#status').textContent = `usando ${name}… ${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`; };
  tick(); toolTimer = setInterval(tick, 1000);
}
function showImage(type, data) {
  const d = document.createElement('div');
  d.className = 'msg shot';
  const im = document.createElement('img');
  im.src = `data:${type};base64,${data}`;
  im.title = 'clique para ampliar';
  im.onclick = () => d.classList.toggle('big');
  d.appendChild(im);
  log.appendChild(d);
  log.scrollTop = log.scrollHeight;
}

async function send(text) {
  if (!text.trim() || child) return;
  await readContext();
  add('user', text);
  const before = ctx.sequence ? await snapshot('') : null;
  const bk = before ? await backup(before) : null;
  const sid = localStorage.getItem(sessionKey);
  const model = ($('#model') && $('#model').value) || 'sonnet';
  const args = ['-p', contextBlock(before, bk) + text, '--output-format', 'stream-json', '--verbose', '--model', model,
                '--allowedTools', 'mcp__claudio,mcp__premiere-pro,mcp__claude_ai_Vault,mcp__claude_ai_Storyblocks,Read,Glob,Grep',
                // allowedTools only pre-approves; this BLOCKS shell and file edits from the panel
                '--disallowedTools', 'Bash,PowerShell,Edit,Write,NotebookEdit',
                // the only built-in tools the panel uses (~20k tokens less per message). ToolSearch must stay: without
                // it every Premiere MCP tool definition is sent up front (~580k tokens, more than fits)
                '--tools', 'Read,Glob,Grep,ToolSearch', ...mcpArgs(text)];
  if (sid) args.push('--resume', sid);
  $('#send').disabled = true; $('#stop').disabled = false; document.body.classList.add('busy');
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
      if (ev.type === 'system' && ev.session_id) { localStorage.setItem(sessionKey, ev.session_id); saveHist(); }
      if (ev.type === 'assistant' && ev.message && ev.message.content) {
        for (const b of ev.message.content) {
          if (b.type === 'text' && b.text.trim()) { cur = add('claude', b.text); importButtons(cur, b.text); $('#status').textContent = ''; }
          if (b.type === 'tool_use') {
            const name = b.name.replace(/^mcp__/, '').replace('__', ' › ');
            add('tool', name);
            toolStart(name);
          }
        }
      }
      // what Claude LOOKED at (contact sheets, frames, people board, find_shot): shown in the chat, not saved
      if (ev.type === 'user' && ev.message && Array.isArray(ev.message.content)) {
        toolStart(null);
        for (const r of ev.message.content) {
          if (r.type !== 'tool_result' || !Array.isArray(r.content)) continue;
          for (const c of r.content) {
            if (c.type === 'image' && c.source && c.source.data) showImage(c.source.media_type || 'image/jpeg', c.source.data);
          }
        }
      }
      if (ev.type === 'result') {
        if (ev.session_id) { localStorage.setItem(sessionKey, ev.session_id); saveHist(); }
        const s = ev.total_cost_usd != null ? ` · US$ ${ev.total_cost_usd.toFixed(2)}` : '';
        add('meta', `${ev.is_error ? 'erro' : 'pronto'} · ${(ev.duration_ms / 1000).toFixed(0)} s${s}`);
      }
    }
  });
  child.stderr.on('data', (d) => { const t = d.toString().trim(); if (t) $('#status').textContent = t.slice(-160); });
  child.on('close', (code) => {
    child = null; $('#send').disabled = false; $('#stop').disabled = true; document.body.classList.remove('busy'); toolStart(null);
    if (code && code !== 0) add('claude err', `O Claude Code terminou com código ${code}. Ele está instalado e logado no Windows? (${claudeExe()})`);
    $('#status').textContent = '';
    readContext();
    if (before) snapshot(before.id).then((after) => { const ch = changedClips(before, after); if (ch.length) warnChanges(ch, before, bk); });
  });
}

$('#send').onclick = () => { const t = $('#input').value; $('#input').value = ''; send(t); };
$('#input').addEventListener('keydown', (e) => { if (e.key === 'Enter' && e.ctrlKey) { e.preventDefault(); $('#send').click(); } });
$('#stop').onclick = () => { if (child) { child.kill(); add('meta', 'parado'); } };
// the bin of actions folds away to give the conversation the room (remembered)
try { if (localStorage.getItem('claudio-bin-shut') === '1') document.body.classList.add('binshut'); } catch (e) {}
$('#binhead').onclick = () => {
  const shut = document.body.classList.toggle('binshut');
  try { localStorage.setItem('claudio-bin-shut', shut ? '1' : '0'); } catch (e) {}
};
$('#newchat').onclick = () => { localStorage.removeItem(sessionKey); log.innerHTML = ''; hist = []; saveHist(); add('meta', 'nova conversa', false); };
document.querySelectorAll('#quick button').forEach((b) => (b.onclick = async () => {
  if (LOCAL[b.dataset.q]) return LOCAL[b.dataset.q]();       // no AI: runs on this machine, no credits
  await readContext(); send(QUICK[b.dataset.q]());
}));
try { const m = localStorage.getItem('claudio-model'); if (m && $('#model')) $('#model').value = m; } catch (e) {}
if ($('#model')) $('#model').onchange = () => { try { localStorage.setItem('claudio-model', $('#model').value); } catch (e) {} };

// indexing progress: read <footage>/_claudio/progress.json (written by the background indexer) every 2 s
function findProgressFiles() {
  const out = [];
  const root = jobFolder();
  const visit = (d, depth) => {
    let ents = [];
    try { ents = fs.readdirSync(d, { withFileTypes: true }); } catch (e) { return; }
    for (const e of ents) {
      if (!e.isDirectory()) continue;
      const p = path.join(d, e.name);
      if (e.name === '_claudio' || e.name === '_decupa') { const f = path.join(p, 'progress.json'); if (fs.existsSync(f)) out.push(f); }
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
  const key = 'claudio-overview-offered:' + (best.folder || '');
  if (best.state === 'done' && !localStorage.getItem(key)) {
    localStorage.setItem(key, '1');
    const d = add('claude', `Material todo indexado (${best.files_total || ''} clipes). Quer que eu faça a visão geral do projeto e proponha direcionamentos?  `);
    const b = document.createElement('button'); b.textContent = 'Fazer visão geral'; b.className = 'primary';
    b.onclick = async () => { await readContext(); send(QUICK.overview()); }; d.appendChild(b);
  }
}
setInterval(showProgress, 2000);

readContext().then(() => { add('meta', `pasta do trabalho: ${jobFolder()}`, false); showProgress(); });
setInterval(() => { if (!child) readContext(); }, 4000);
