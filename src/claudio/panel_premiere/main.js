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

// the editor's job folders (claudio.profile: perfil.json over these defaults), read once from claudio-run at start
let PROF = { pastas: { brutos: 'brutos', audio: 'brutos/audio', fotos: 'brutos/fotos', projeto: 'projeto', trilhas: 'trilhas',
  legendas: 'legendas', overlays: 'projeto/claudio_overlays', backups: 'claudio_backups' },
  raw_names: ['brutos', 'BRUTOS', 'VIDEOS', 'VÍDEOS', 'RAW', 'FOOTAGE', 'MATERIAL', 'CARTOES', 'CARTÕES'] };

function jobFolder() {
  // <job>/<projeto>/<name>.prproj  → work from <job> (a '#…' folder counts as the project folder too)
  if (!ctx.project) return os.homedir();
  let d = path.dirname(ctx.project);
  const b = path.basename(d).toLowerCase();
  if (b.startsWith('#') || b === PROF.pastas.projeto.split('/').pop().toLowerCase()) d = path.dirname(d);
  return d;
}

function jobPath(name, ...rest) {
  return path.join(jobFolder(), ...PROF.pastas[name].split('/'), ...rest);
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
// links ([texto](url) and bare http(s) URLs) become <a>; a click opens them in the default browser (openExternal).
// Code and links are set aside as placeholders first so bold/italic never reach inside a URL.
function inline(s) {
  const keep = [], put = (h) => `\u0001${keep.push(h) - 1}\u0002`;
  const a = (url, label) => `<a href="${url}" title="${url}">${label}</a>`;
  const bare = (u) => { const m = u.match(/^(.*?)([.,;:!?)\]]*)$/); return a(m[1], m[1]) + m[2]; };
  return esc(s)
    .replace(/`([^`]+)`/g, (m, c) => put(/^https?:\/\/\S+$/.test(c) ? a(c, `<code>${c}</code>`) : `<code>${c}</code>`))
    .replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)"]+)\)/g, (m, t, u) => put(a(u, t)))
    .replace(/https?:\/\/[^\s<>"\u0001]+/g, (u) => put(bare(u)))
    .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
    .replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, '$1<i>$2</i>')
    .replace(/\u0001(\d+)\u0002/g, (m, n) => keep[n]);
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
  if (cls.startsWith('user') || cls.startsWith('claude') || cls.startsWith('local')) {
    const hd = document.createElement('div'), bd = document.createElement('div');
    hd.className = 'hd'; bd.className = 'bd';
    const when = new Date(t);
    const who = cls === 'user more' ? 'você · complemento' : cls === 'user' ? 'você' : cls.startsWith('local') ? 'painel · sem IA' : 'cláudio';
    hd.innerHTML = `<b>${who}</b><time>${String(when.getHours()).padStart(2, '0')}:${String(when.getMinutes()).padStart(2, '0')}</time>`;
    if (cls.startsWith('user')) bd.textContent = text; else bd.innerHTML = md(text);
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
const PLAN_RE = process.platform === 'win32' ? /[A-Za-z]:\\[^\n"`*<>|]*?\.claudio-mogrt\.jsx\b/g : /\/[^\n"`*<>|]*?\.claudio-mogrt\.jsx\b/g;
function mogrtButtons(d, text) {
  const seen = new Set();
  for (const m of text.match(PLAN_RE) || []) {
    const p = m.trim();
    if (seen.has(p) || !fs.existsSync(p)) continue;
    seen.add(p);
    const b = document.createElement('button');
    b.className = 'import';
    b.textContent = 'Aplicar legendas (MOGRT) na sequência ativa';
    b.onclick = () => placeMogrts(p, b);
    d.appendChild(document.createElement('br'));
    d.appendChild(b);
  }
}
async function placeMogrts(p, b) {
  if (b) { b.disabled = true; b.textContent = 'colocando as legendas…'; }
  let r = {};
  try { r = JSON.parse(await evalHost('claudioPlaceMogrts(' + JSON.stringify(p) + ')')); } catch (e) { r = { ok: false, error: String(e) }; }
  const msg = r.ok ? `**${r.placed} de ${r.blocks} legendas** na trilha nova **${r.track}**, cada uma um MOGRT: selecione e mude o texto em Gráficos essenciais › Texto, ou corte o clipe para mudar o tempo.`
    + ((r.errors || []).length ? `\n\nNão entraram: ${r.errors.join('; ')}` : '')
    : `Não coloquei as legendas: ${r.error || (r.errors || []).join('; ')}`;
  if (b) b.textContent = r.ok ? `legendas colocadas (${r.placed}) em ${r.track}` : 'falhou — veja a mensagem';
  add(r.ok ? 'local' : 'local err', msg);
  return r;
}
// a caption overlay .mov in Claude's answer gets "Colocar overlay": new top track, overwrite at 0 s (never an insert,
// which pushed the whole sequence to the right)
const MOV_RE = process.platform === 'win32' ? /[A-Za-z]:\\[^\n"`*<>|]*?\.mov\b/g : /\/[^\n"`*<>|]*?\.mov\b/g;
function overlayButtons(d, text) {
  const seen = new Set();
  for (const m of text.match(MOV_RE) || []) {
    const p = m.trim();
    if (seen.has(p) || !/legendas?_/i.test(path.basename(p)) || !fs.existsSync(p)) continue;
    seen.add(p);
    const b = document.createElement('button');
    b.className = 'import';
    b.textContent = 'Colocar overlay na sequência ativa: ' + path.basename(p);
    b.onclick = () => placeOverlay(p, b);
    d.appendChild(document.createElement('br'));
    d.appendChild(b);
  }
}
async function placeOverlay(p, b) {
  if (b) { b.disabled = true; b.textContent = 'colocando o overlay…'; }
  let r = {};
  try { r = JSON.parse(await evalHost('claudioPlaceOverlay(' + JSON.stringify(p) + ')')); } catch (e) { r = { ok: false, error: String(e) }; }
  if (b) b.textContent = r.ok ? `overlay em ${r.track}` : 'falhou — veja a mensagem';
  add(r.ok ? 'local' : 'local err', r.ok ? `Overlay **${r.name}** em 0 s na trilha nova **${r.track}**, por cima — nada mais na sequência mudou de lugar. Desligue o olho da trilha de legenda (C1) pra não duplicar.`
    : `Não coloquei o overlay: ${r.error}`);
  return r;
}
// a soundtrack plan (.claudio-trilha.jsx) in Claude's answer: placed by the panel, one NEW audio track per sequence,
// each sequence backed up first. Live answers apply it at once (the editor asked for it in one go); reloaded ones
// only show the button.
const MUSIC_RE = process.platform === 'win32' ? /[A-Za-z]:\\[^\n"`*<>|]*?\.claudio-trilha\.jsx\b/g : /\/[^\n"`*<>|]*?\.claudio-trilha\.jsx\b/g;
const musicDone = new Set((() => { try { return JSON.parse(localStorage.getItem('claudio-music-done') || '[]'); } catch (e) { return []; } })());
const musicSave = () => { try { localStorage.setItem('claudio-music-done', JSON.stringify([...musicDone].slice(-50))); } catch (e) {} };
function musicButtons(d, text) {
  const seen = new Set();
  for (const m of text.match(MUSIC_RE) || []) {
    const p = m.trim();
    if (seen.has(p) || !fs.existsSync(p)) continue;
    seen.add(p);
    const b = document.createElement('button');
    b.className = 'import music';
    b.dataset.plan = p;
    b.textContent = 'Colocar a trilha nas sequências';
    if (musicDone.has(p)) { b.disabled = true; b.textContent = 'trilha colocada'; }
    b.onclick = () => placeMusic(p, b);
    d.appendChild(document.createElement('br'));
    d.appendChild(b);
  }
}
async function placeMusic(p, b) {
  if (musicDone.has(p) && b && b.disabled) return;
  musicDone.add(p); musicSave();
  if (b) { b.disabled = true; b.textContent = 'colocando a trilha…'; }
  let P = { sequences: [] };
  try { P = JSON.parse(fs.readFileSync(p, 'utf8').replace(/^\s*var CLAUDIO_MUSIC\s*=\s*/, '').replace(/;\s*$/, '')); } catch (e) {}
  const bks = [];
  for (const s of P.sequences) {                      // a backup of every sequence that is about to change
    const id = s.id || await evalHost('claudioSeqIdByName(' + JSON.stringify(s.sequence) + ')');
    const sn = id ? await snapshot(id) : null;
    const bk = sn && await backup(sn);
    if (bk) bks.push(bk);
  }
  let r = {};
  try { r = JSON.parse(await evalHost('claudioPlaceMusic(' + JSON.stringify(p) + ')')); } catch (e) { r = { ok: false, error: String(e) }; }
  const res = r.results || [];
  const okN = res.filter((x) => x.ok).length;
  const plan = (n) => P.sequences.find((s) => s.sequence === n) || {};
  const rows = res.map((x) => {
    const q = plan(x.sequence);
    return x.ok ? `| ${x.sequence} | ${q.title || ''} | **${x.track}** | ${q.under_db === q.gaps_db ? `${q.gaps_db} dB (sem ducking)` : `${q.under_db} dB sob a fala · ${q.gaps_db} dB nos respiros` + (q.ends_db != null && q.ends_db !== q.gaps_db ? ` · ${q.ends_db} dB na abertura/vinheta` : '')} | ${x.keys} |`
      + ((x.errors || []).length ? ` ${x.errors.join('; ')}` : '')
      : `| ${x.sequence} | — | — | não colocada: ${x.error || (x.errors || []).join('; ')} | — |`;
  });
  if (b) b.textContent = r.ok && okN ? `trilha colocada em ${okN} sequência(s)` : 'falhou — veja a mensagem';
  if (!(r.ok && okN)) { musicDone.delete(p); musicSave(); if (b) b.disabled = false; }
  add(r.ok && okN ? 'local' : 'local err', r.ok ? `**Trilha em ${okN} de ${res.length} sequência(s)**, cada uma numa trilha de áudio nova (nada que já estava mudou de lugar).\n\n` +
    '| sequência | música | trilha | nível | keyframes |\n|---|---|---|---|---|\n' + rows.join('\n') +
    `\n\nO ducking está nos keyframes de Volume › Nível do clipe de música: arraste os pontos se quiser mais ou menos. Backup antes: \`${bks.length ? path.dirname(bks[0]) : '—'}\`.`
    : `Não coloquei a trilha: ${r.error}`);
  return r;
}
function importButtons(d, text) {
  mogrtButtons(d, text);
  overlayButtons(d, text);
  musicButtons(d, text);
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
  // the profile's footage folder first; other jobs use VIDEOS / RAW / FOOTAGE / MATERIAL
  const names = PROF.raw_names.map((n) => n.toUpperCase());
  try {
    const dirs = fs.readdirSync(jobFolder(), { withFileTypes: true }).filter((d) => d.isDirectory()).map((d) => d.name);
    const hit = names.map((n) => dirs.find((d) => d.toUpperCase() === n)).find(Boolean);
    if (hit) return path.join(jobFolder(), hit);
  } catch (e) {}
  return jobFolder();
}

const QUICK = {
  index: () => `Analise o material bruto deste trabalho: index_folder na pasta "${rawFolder()}" (mostre o progresso). Se houver perfil do cliente (client_profile), use o vocabulário dele. No fim, me diga em 4–6 linhas o que tem no material.`,
  selects: () => `Faça as selects deste trabalho com make_selects (pasta "${rawFolder()}", XML em "${jobPath('projeto', 'claudio_selects.xml')}") e escreva o caminho completo do XML (o painel mostra o botão de importar) — não importe XML pelo MCP do Premiere. Depois me resuma as escolhas.`,
  notes: () => `Com o XML da sequência ativa ("${ctx.sequence || ''}") que o painel já exportou (está no contexto), rode compare_with_nle contra a última versão entregue pelo Cláudio deste trabalho, e me mostre o que eu mudei e as notas (marcadores) que deixei. Não aplique nada ainda: proponha e espere meu ok.`,
  qc: () => `Faça o QC da sequência ativa ("${ctx.sequence || ''}", ${ctx.frame_size || ''}) antes de eu exportar: rode qc_timeline no XML da sequência ativa que o painel já exportou (está no contexto). Liste os problemas e pergunte se quero que você marque cada um como marcador na timeline.`,
  status: () => `Rode index_status e me diga o progresso em uma linha.`,
  overview: () => `Faça a visão geral do projeto: project_digest na pasta "${rawFolder()}", entenda sobre o que é o trabalho, salve com save_project_overview em "${jobFolder()}" e me resuma em 4–6 linhas. Depois me pergunte a duração aproximada do vídeo final e a trilha (uma música específica ou um mood/gênero) e proponha 2–4 direcionamentos concretos a partir do material. Eu escolho um ou explico a minha ideia.`,
  captions: () => `Legendas da sequência ativa ("${ctx.sequence || ''}", ${ctx.frame_size || ''}). Use o XML da sequência ativa que o painel já exportou (está no contexto) e siga os passos: 1) caption_sentences e corrija SÓ a gramática (concordância, regência, crase, pontuação), sem mudar as palavras do falante nem nomes de marca — me mostre a tabela fala → legenda; 2) caption_style_preview (aspect conforme o frame da sequência; se houver perfil do cliente, passe client) e me pergunte qual estilo; 3) com a minha escolha, gere SÓ o .srt (captions_srt com style = o estilo escolhido, e client se houver perfil — os blocos saem por frase, no tamanho de linha desse estilo) em "${jobPath('legendas')}" com o nome da sequência e coloque-o na sequência como trilha de legenda (Premiere MCP create_caption_track; se não der, me diga o caminho para eu arrastar). NÃO gere o overlay ainda: eu reviso palavras e tempo na trilha de legenda do Premiere e aperto "Overlay da legenda revisada" ou "Legenda MOGRT" (o Cláudio lê a trilha direto do projeto, não precisa exportar o .srt). Termine dizendo o estilo escolhido.`,
  captions_final: () => `A legenda da sequência ativa ("${ctx.sequence || ''}", ${ctx.frame_size || ''}) foi revisada por mim no Premiere (o painel acabou de salvar o projeto). Leia a legenda direto da sequência com premiere_captions(prproj "${ctx.project || ''}", sequence "${ctx.sequence || ''}", nle_xml = o XML da sequência ativa do contexto): ela grava <sequência>_revisada.srt em ${PROF.pastas.legendas} e aprende o que eu corrigi no texto. Só se a sequência não tiver trilha de legenda, use o .srt revisado mais recente de "${jobPath('legendas')}" (se houver dúvida entre arquivos, me pergunte). Gere o overlay com caption_overlay(srt=esse arquivo), usando o XML da sequência ativa do contexto e o estilo escolhido antes nesta conversa (se não souber, pergunte). Não refaça correções do transcript: o texto e os tempos do .srt são os meus. Saída em "${jobPath('legendas')}". NÃO coloque o .mov na timeline pelo MCP do Premiere: escreva o caminho completo do .mov na resposta — o painel mostra o botão "Colocar overlay", que cria a trilha nova no topo e põe o clipe em 0 s por sobrescrita, sem mexer em mais nada.`,
  multicam: () => `Multicâmera deste trabalho, no lugar do PluralEyes. 1) Liste as pastas de "${jobFolder()}" (material de câmera e áudio; ignore ${PROF.pastas.projeto}, ${PROF.pastas.trilhas}, ${PROF.pastas.legendas} e proxies) e proponha o mapeamento: cada pasta de câmera vira uma câmera com nome curto (ex.: "CAM A — A7III"), e gravadores externos (Zoom, mesa, lapela) vão em external_audio. Proponha uma cor de label do Premiere por câmera e uma para o áudio externo (Cerulean, Mango, Forest, Magenta, Yellow, Violet, Teal, Rose, Caribbean…) numa tabela câmera → pasta → nº de clipes → cor, e ME PERGUNTE antes de rodar. 2) Com o meu ok, rode sync_multicam (correct_drift true, labels com as cores, out_xml "${jobPath('projeto', 'claudio_sync.xml')}"). 3) Resuma: clipes posicionados, os aproximados (sem áudio em comum, posicionados pelo relógio da câmera) e o drift do gravador. 4) Confira com multicam_monitor em 2 ou 3 momentos em que todas as câmeras estão rodando e me diga se algo parece fora de sincronia. 5) Escreva o caminho completo do XML (o painel mostra o botão de importar; não importe pelo MCP do Premiere) e explique o próximo passo: a "Synced Sequence" chega com V1..Vn por câmera e cada câmera (vídeo, áudio e bin) na sua cor; dali eu corto direto ou crio uma Multi-Camera Source Sequence.`,
  aprender: (r) => `Rodei sem IA o aprendizado do cliente "${r.cliente}" com ${r.videos.length} vídeos já entregues (receita base: ${r.base}). A receita rascunho está em "${r.receita}" (nome "${r.receita_nome}") e as medidas em house_style.medido_videos do perfil (client_profile("${r.cliente}")). Agora o que não é número: 1) leia recipe("${r.receita_nome}") e o perfil; 2) olhe 3–4 vídeos representativos (o típico, o mais curto, o mais longo, o mais novo) com contact_sheet do vídeo inteiro (16 quadros) e transcript — vídeos: ${r.videos.slice(0, 12).join(', ')}; escreva só o que vê e ouve: estrutura (o que os 3 primeiros segundos fazem, o corpo, o fechamento/CTA, com tempos típicos), tom e quem fala, o que o b-roll mostra e quando entra, a legenda (estilo, posição, palavras por bloco — confirme ou corrija a estimativa), grafismos/logo/cartela, a música, e o que é entregue (formato, duração, versões); 3) numa mensagem só, me pergunte o que não dá para ver (o que eu faço à mão e o que o Cláudio deve entregar, quem aprova, o que o cliente sempre pede ou nunca aceita); 4) reescreva a receita e grave com save_recipe("${r.receita_nome}", markdown) mantendo o frontmatter e a seção "Medido nos vídeos prontos", reescrevendo Brief, Etapas, Legendas e Regras de gosto para ESTE cliente, e me mostre o resultado em 6–10 linhas; 5) com o meu ok, save_client_profile("${r.cliente}", {"receita": "${r.receita_nome}"}).`,
  recipe: (r) => `Monte este trabalho com a receita "${r.titulo}": chame recipe("${r.nome}") e siga o brief, as etapas e as regras dela (as regras comuns vêm junto). Material bruto: "${rawFolder()}". Pasta do trabalho: "${jobFolder()}". Projeto dos edits (o \`project\` do build_edit / event_recap / export_edit): "${jobPath('projeto')}". Se o material ainda não estiver indexado, comece por index_folder e mostre o progresso. Se houver perfil do cliente (client_profile), ele vale acima dos padrões da receita. Escreva o caminho completo de cada XML que gerar (o painel mostra o botão de importar).`,
  autocut: () => `Corte automático da multicâmera da sequência ativa ("${ctx.sequence || ''}") por quem está falando. O XML e as trilhas estão no contexto. 1) Proponha numa tabela: cada trilha de vídeo → quem ela enquadra (ou "aberto" para o plano geral) e cada pessoa → a trilha de áudio do microfone dela (olhe os nomes dos clipes; se precisar, contact_sheet de um frame de cada câmera) e ME PERGUNTE antes de rodar. 2) Com o meu ok, rode auto_cut_multicam (min_hold 2 s; max_hold 12 s). 3) Me diga quanto tempo ficou em cada câmera e quantos cortes, e escreva o caminho do XML (o painel mostra o botão de importar). As câmeras originais ficam embaixo, desativadas.`,
  captions_mogrt: () => `Legenda em MOGRT editável da sequência ativa ("${ctx.sequence || ''}", ${ctx.frame_size || ''}). Use o .srt revisado mais recente de "${jobPath('legendas')}" se houver (senão faça os passos de caption_sentences e me mostre as correções), o estilo escolhido nesta conversa (se não souber, caption_style_preview e me pergunte) e rode caption_mogrt (out_dir "${jobPath('legendas')}"). Escreva o caminho completo do plano .claudio-mogrt.jsx — o painel mostra o botão "Aplicar legendas (MOGRT)".`,
  vault: () => `Registre este trabalho no vault (conector Vault), seguindo as regras do CLAUDE.md do vault (leia-o com read_note antes): 1) nota do trabalho com cliente, data, pasta "${jobFolder()}", material (câmeras, horas, cor/log, áudio), decisões de montagem desta conversa, versões entregues e onde estão os arquivos (.md da decupagem, XMLs, perfil do cliente); 2) entrada no diário de hoje (append_diary) com um resumo de 2–4 linhas e link pra nota; 3) se o cliente tiver página, linke. Não grave senhas nem dados sensíveis. No fim me diga o que foi escrito.`,
};

// ── safety net ──────────────────────────────────────────────────────────────────────────────────────────────
// Before each message: the active sequence's tracks are snapshotted and backed up as FCP XML (<project folder>/<backups>).
// After the answer: any clip that was already there and got moved, trimmed or removed is reported, with the backup to
// import back — and Claude is told on the next message. New clips on new tracks are fine and not reported.
let lastWarning = '';
async function snapshot(id) {
  try { const r = JSON.parse(await evalHost('claudioSnapshot(' + JSON.stringify(id || '') + ')')); return r.ok ? r : null; } catch (e) { return null; }
}
function backupDir() {
  if (!ctx.project) return null;
  const d = path.join(path.dirname(ctx.project), PROF.pastas.backups);
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
  try {                                                     // keep the last 30 (XML + trilhas.json)
    const old = fs.readdirSync(d).filter((f) => f.endsWith('.xml') || f.endsWith(' — trilhas.json')).map((f) => path.join(d, f))
      .sort((a, b) => fs.statSync(b).mtimeMs - fs.statSync(a).mtimeMs).slice(60);
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
// apps opened from the Dock/Finder get a minimal PATH on macOS: add where claude, node, uv tools and ffmpeg live
function childEnv() {
  const extra = process.platform === 'win32' ? [] : [path.join(os.homedir(), '.local', 'bin'), '/opt/homebrew/bin', '/usr/local/bin'];
  return Object.assign({}, process.env, { PATH: [...extra, process.env.PATH || ''].join(path.delimiter) });
}
function runLocal(action, args) {
  return new Promise((res) => {
    const c = claudioCmd();
    const b64 = 'b64:' + Buffer.from(JSON.stringify(args || {}), 'utf8').toString('base64');
    let out = '';
    const p = cp.spawn(c.cmd, [...c.pre, action, b64], { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'], env: childEnv() });
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
// "Limpar falas": pauses, hesitations and what was said for the editor come out; zoom=true also alternates 100/107.5 %
// on the jump cuts (offered as a second button under the result, like the import)
const cleanSpeech = (zoom, xml) => localJob(zoom ? 'limpando as falas (com zoom)' : 'limpando as falas', async () => {
  const x = xml || await exportActive(); if (!x) return;
  const r = await runLocal('clean', { nle_xml: x, zoom });
  if (!r.ok || r.error) return add('local err', r.error || 'falhou');
  const fl = (r.fillers_cut || []).map((f) => `"${f.word}" em ${dur(f.at)}`);
  const nt = (r.notes_cut || []).map((n) => `${n.what} em ${dur(n.at)}: "${n.said}"`);
  const mk = (r.markers || []).length;
  const z = r.zoom || {};
  const m = add('local', `**${r.pauses_cut} pausa(s)** e **${fl.length} vício(s)** a menos: ${dur(r.before_s)} → **${dur(r.after_s)}** (−${r.removed_s.toFixed(1)} s).` +
    (fl.length ? `\n\nVícios: ${fl.slice(0, 12).join(', ')}` : '') +
    (nt.length ? `\n\nFalas pro editor/set que saíram: ${nt.slice(0, 8).join(' · ')}` : '') +
    (mk ? `\n\n${mk} marcador(es) na sequência nova (vermelho = saiu, amarelo = conferir).` : '') +
    (r.crossfades ? `\n\n${r.crossfades} crossfade(s) de 2 quadros nas emendas (sem clique).` : '') +
    (zoom ? `\n\nZoom: ${z.jump || 0} pedaço(s) em 107,5% nos jump cuts.` : '') +
    ((r.music_tracks_kept || []).length ? `\n\nMúsica sem cortes: ${r.music_tracks_kept.join(', ')}.` : '') +
    (r.note ? `\n\n_${r.note}_` : '') + droppedLine(r) + `\n\nSequência nova: **${r.sequence}** · XML: \`${r.nle_path}\``);
  importButtons(m, r.nle_path);
  if (!zoom) {
    const b = document.createElement('button');
    b.className = 'import';
    b.textContent = 'Refazer com zoom nos jump cuts (100/107,5%)';
    b.onclick = () => { b.disabled = true; cleanSpeech(true, x); };
    m.appendChild(document.createElement('br'));
    m.appendChild(b);
  }
});
const dur = (s) => clock(+s);
// "Trilha sonora": the editor ticks the sequences, they are exported NOW (each one also a backup), and the AI takes it
// from there with the Storyblocks connector (the word in the message loads it): brief, choose with the editor,
// download into the music folder, one plan that the panel places on new audio tracks
async function musicMenu() {
  await readContext();
  let seqs = [];
  try { seqs = JSON.parse(await evalHost('claudioSequences()')).sequences || []; } catch (e) {}
  if (!seqs.length) return add('local err', 'Nenhuma sequência neste projeto.', false);
  const d = add('local', 'Trilha sonora (Storyblocks): marque as sequências que vão ganhar música — uma música para todas ou uma para cada, o Claude pergunta depois.', false);
  const picks = seqs.map((s) => {
    const lb = document.createElement('label');
    lb.style.cssText = 'display:block;margin-top:5px;font-size:11px;cursor:pointer';
    const cb = document.createElement('input');
    cb.type = 'checkbox'; cb.checked = s.name === ctx.sequence;
    lb.append(cb, ' ' + s.name);
    d.appendChild(lb);
    return [cb, s];
  });
  const go = document.createElement('button');
  go.className = 'primary'; go.textContent = 'Escolher a trilha';
  go.onclick = async () => {
    const sel = picks.filter(([cb]) => cb.checked).map(([, s]) => s);
    if (!sel.length) return;
    d.querySelectorAll('button, input').forEach((x) => (x.disabled = true));
    connectorsUntil = Date.now() + 45 * 60 * 1000;
    const xmls = [];
    await localJob('exportando as sequências', async () => {
      for (const q of sel) {
        toolStart(`exportando ${q.name}`);
        const sn = await snapshot(q.id);
        const p = sn && await backup(sn);
        if (p) xmls.push({ name: q.name, id: q.id, xml: p }); else add('local err', `${q.name}: não consegui exportar a sequência`);
      }
    });
    if (!xmls.length) return;
    send(`Trilha sonora pelo Storyblocks para estas sequências, exportadas AGORA do Premiere (use estes XMLs):\n` +
      xmls.map((x) => `- "${x.name}" (sequence_id ${x.id}): ${x.xml}`).join('\n') +
      `\n\n1) music_brief com esses XMLs; veja o perfil do cliente (client_profile) se houver — trilha/estilo da última vez. ` +
      `2) Numa mensagem só, me pergunte o que faltar: mood/gênero (proponha um a partir do conteúdo), uma música para todas ou uma por sequência, e se a música que já existe em alguma fica. ` +
      `3) Busque no conector Storyblocks (áudio/música; instrumental quando houver fala; BPM, energia e duração mínima do brief; prefira a versão com duração mais próxima) e mostre 3–5 opções por sequência numa tabela (título, BPM, duração, link de prévia), com uma recomendada. Espere a minha escolha. ` +
      `4) Com a escolha: pegue o link de download no Storyblocks e rode fetch_music(url, job "${jobFolder()}", title = nome da música) para cada uma. ` +
      `5) music_plan(job "${jobFolder()}", sequences = [{nle_xml, music, sequence_id}]) — mode auto, a não ser que eu peça outro trecho — e escreva o caminho completo do .claudio-trilha.jsx: o painel coloca sozinho, numa trilha de áudio nova de cada sequência, com backup antes. Não importe nem coloque a música pelo MCP do Premiere. ` +
      `6) Resuma em 2–3 linhas por sequência: onde a música entra, de que ponto da música, nível sob a fala e nos respiros, e qualquer emenda (música repetida) para eu conferir.`);
  };
  d.appendChild(go);
}
// ── recipes: the MONTAR buttons come from the recipes (claudio-run recipes); this one is the fallback without WSL ──
let RECIPES = [{ nome: 'recap-evento', titulo: 'Aftermovie / resumo de evento', quando: 'material de um evento e uma música',
  botao: { rotulo: 'Recap de evento', dica: 'Aftermovie do evento: melhores momentos no beat da música' } }];
function numberButtons() {
  document.querySelectorAll('#quick button').forEach((b, k) => { const i = b.querySelector('i'); if (i) i.textContent = String(k + 1).padStart(2, '0'); });
}
function renderRecipeButtons(list) {
  const slot = $('#recipe-slot');
  if (!slot) return;
  slot.innerHTML = '';
  for (const r of list.filter((x) => x.botao)) {
    const b = document.createElement('button');
    b.dataset.q = 'recipe:' + r.nome; b.style.setProperty('--c', 'var(--forest)'); b.title = r.botao.dica || r.quando || '';
    b.innerHTML = '<i></i><span></span>'; b.querySelector('span').textContent = r.botao.rotulo || r.titulo;
    b.onclick = async () => { await readContext(); send(QUICK.recipe(r)); };
    slot.appendChild(b);
  }
  numberButtons();
}
async function loadRecipes() {
  const r = await runLocal('recipes', { job: jobFolder() }).catch(() => null);
  if (r && r.ok && Array.isArray(r.receitas) && r.receitas.length) RECIPES = r.receitas;
  renderRecipeButtons(RECIPES);
  return r && r.ok ? r : null;
}
// ── learn a client from videos already delivered (claudio-run learn: no AI), then optionally the AI pass ──
function learnLine(r) {
  const m = r.medido || {}, md = (k, u = '') => (m[k] ? `${m[k].mediana}${u}` : '—');
  const cap = m.legenda ? `legenda queimada em ${m.legenda.com_legenda_pct}% (blocos de ~${(m.legenda.bloco_s || {}).mediana} s, estimativa)` : 'sem legenda queimada';
  return `**${r.videos.length} vídeos** medidos · ${m.formato} · duração ${md('duracao_s', ' s')} · corte a cada ${md('corte_a_cada_s', ' s')} · ` +
    `fala ${md('fala_pct', '%')} · rosto na tela ${md('rosto_pct', '%')} · vinheta final ${md('vinheta_final_s', ' s')} · ${cap} · ${md('lufs', ' LUFS')}` +
    `\n\nReceita base: **${r.base}**. Rascunho: \`${r.receita}\`` + (r.errors && r.errors.length ? `\n\nSem medir: ${r.errors.join('; ')}` : '');
}
async function learnMenu() {
  await readContext();
  const d = add('local', 'Aprender um cliente com vídeos já entregues: o Cláudio mede formato, ritmo, fala, legenda e loudness (sem IA, transcrição pelo Whisper local) e escreve a receita do cliente. Com IA, ele também olha os vídeos e escreve estrutura, tom e o que o cliente espera.', false);
  const field = (label, value, ph) => {
    const lb = document.createElement('label'); lb.style.cssText = 'display:block;margin-top:6px;font-size:11px';
    const inp = document.createElement('input'); inp.value = value || ''; inp.placeholder = ph || '';
    inp.style.cssText = 'width:100%;box-sizing:border-box;margin-top:2px';
    lb.append(label, inp); d.appendChild(lb); return inp;
  };
  const client = field('Cliente', '', 'nome do cliente (o mesmo do perfil)');
  const folder = field('Pasta dos vídeos entregues', '', process.platform === 'win32' ? 'D:\\...\\exportados' : '/Volumes/.../exportados');
  const limit = field('Quantos vídeos (os mais novos)', '12');
  if (window.cep && window.cep.fs && window.cep.fs.showOpenDialogEx) {
    const pick = document.createElement('button'); pick.textContent = 'Escolher pasta…';
    pick.onclick = () => { const r = window.cep.fs.showOpenDialogEx(false, true, 'Vídeos já entregues ao cliente', folder.value || jobFolder()); if (r && r.data && r.data[0]) folder.value = r.data[0]; };
    d.appendChild(pick);
  }
  const run = (withAI) => async () => {
    if (!client.value.trim() || !folder.value.trim()) return add('local err', 'Preencha o cliente e a pasta.', false);
    d.querySelectorAll('button, input').forEach((x) => (x.disabled = true));
    let res = null;
    await localJob('aprendendo com os vídeos prontos', async () => {
      res = await runLocal('learn', { client: client.value.trim(), folder: folder.value.trim(), limit: parseInt(limit.value, 10) || 12 });
      if (!res.ok || res.error) { add('local err', res.error || 'falhou'); res = null; return; }
      add('local', learnLine(res));
    });
    if (res && withAI) send(QUICK.aprender(res));
  };
  for (const [t, ai] of [['Medir (sem IA)', false], ['Medir e refinar com IA', true]]) {
    const b = document.createElement('button'); b.textContent = t; if (ai) b.className = 'primary'; b.onclick = run(ai); d.appendChild(b);
  }
}
async function recipeMenu() {
  await readContext();
  const r = await loadRecipes();
  const client = r && r.cliente;
  const d = add('local', 'Montar com receita: escolha o tipo de projeto. ' +
    (client ? `O cliente deste trabalho costuma pedir **${client}**.` : 'Receitas suas ficam em ~/.config/claudio/receitas.'), false);
  const picks = RECIPES.map((x) => {
    const lb = document.createElement('label');
    lb.style.cssText = 'display:block;margin-top:6px;font-size:11px;cursor:pointer';
    const rb = document.createElement('input');
    rb.type = 'radio'; rb.name = 'claudio-recipe'; rb.checked = x.nome === client;
    const t = document.createElement('b'); t.textContent = ' ' + x.titulo;
    const q = document.createElement('div'); q.textContent = x.quando || ''; q.style.cssText = 'opacity:.7;margin-left:18px';
    lb.append(rb, t, q);
    d.appendChild(lb);
    return [rb, x];
  });
  const go = document.createElement('button');
  go.className = 'primary'; go.textContent = 'Montar';
  go.onclick = () => {
    const sel = picks.find(([rb]) => rb.checked);
    if (!sel) return;
    d.querySelectorAll('button, input').forEach((x) => (x.disabled = true));
    send(QUICK.recipe(sel[1]));
  };
  d.appendChild(go);
}
const LOCAL = {
  aprender: () => learnMenu(),
  montar: () => recipeMenu(),
  music: () => musicMenu(),
  clean: () => cleanSpeech(false),
  sfx: () => localJob('sonorizando', async () => {
    const x = await exportActive(); if (!x) return;
    const r = await runLocal('sfx', { nle_xml: x, job: jobFolder() });
    if (!r.ok || r.error) return add('local err', r.error || 'falhou');
    const m = add('local', `**${r.whooshes} whoosh** nas entradas de b-roll e **${r.pops} pop** nas palavras-chave, na trilha nova **${r.sfx_track}** (${r.levels_db.whoosh} / ${r.levels_db.pop} dB).` +
      `\n\nSons: \`${path.dirname(r.sounds.whoosh[0])}\` — troque por outros pondo whoosh*.wav / pop*.wav numa pasta (sfx_dir do perfil do cliente).` +
      droppedLine(r) + `\n\nSequência nova: **${r.sequence}** · XML: \`${r.nle_path}\``);
    importButtons(m, r.nle_path);
  }),
  captions_mogrt: () => localJob('legenda MOGRT: estilos', async () => {
    await readContext(); await evalHost('claudioSave()');          // the caption fixed in Premiere is read from the saved project
    const st = await runLocal('mogrt_styles', { job: jobFolder(), prproj: ctx.project, sequence: ctx.sequence });
    if (!st.ok) return add('local err', st.error);
    const d = add('local', `**Legenda em MOGRT** (editável no Premiere, sem render). ` +
      (st.srt ? `Vou usar a legenda \`${path.basename(st.srt.replace(/\\/g, '/'))}\` (blocos e tempos dela${/_revisada\.srt$/.test(st.srt) ? ', lida da trilha de legenda da sequência' : ''}).` : `Não achei legenda na sequência nem .srt revisado em ${PROF.pastas.legendas}: uso a transcrição com as correções já aprovadas.`) +
      ` Qual estilo?` + (st.recommended ? ` (recomendado: **${st.recommended}**, o do cliente ${st.client})` : ''), false);
    for (const s of st.styles) {
      const b = document.createElement('button'); b.textContent = s + (s === st.recommended ? ' ★' : '');
      b.onclick = () => { d.querySelectorAll('button').forEach((x) => (x.disabled = true)); mogrtRun(s, st.srt); };
      d.appendChild(b);
    }
  }),
  status: () => localJob('status', async () => {
    const r = await runLocal('status', { folder: rawFolder() });
    add('local', r.ok ? (r.jobs.length ? r.jobs.map((j) => `**${path.basename(j.folder.replace(/\\/g, '/'))}**: ${j.line}`).join('\n') : 'Nenhuma indexação nesta pasta.') : r.error);
  }),
  index: () => localJob('indexação', async () => {
    const r = await runLocal('index', { folder: rawFolder() });
    const pf = r.profile && r.profile.client ? `\n\nPerfil **${r.profile.client}** aplicado: ` + [r.profile.vocabulary ? 'vocabulário' : '', r.profile.rotation ? `brutos girados ${r.profile.rotation}° (gravados na vertical)` : ''].filter(Boolean).join(' · ') + '.' : '';
    add('local', r.ok ? (r.started ? `Indexação iniciada em segundo plano: ${r.files} vídeos em \`${rawFolder()}\`. A barra embaixo mostra o progresso (primeiro ela lê a duração de cada vídeo, uns segundos por arquivo); pode fechar o painel.` : `Já está indexando: ${r.line}`) + pf : r.error);
    progScan = 0;
  }),
  multicam: () => multicamPlan(),
  describe: () => localJob('descrevendo os planos', async () => {
    await readContext();
    const sel = [...new Set((ctx.selection || []).map((c) => c.media).filter(Boolean))];
    const r = await runLocal('describe', sel.length ? { paths: sel } : { folder: rawFolder() });
    if (!r.ok) return add('local err', r.error);
    const ex = (r.results || []).filter((x) => x.sample && x.sample.length).slice(0, 3)
      .map((x) => `- **${x.clip}** ${dur(x.sample[0].s)}–${dur(x.sample[0].e)}: ${x.sample[0].text}`).join('\n');
    const d = add('local', `**${r.clips} clipe(s)** descritos em **${r.stretches} trechos** (${sel.length ? 'os selecionados na timeline' : 'a pasta toda'}), ` +
      'gravados no `.md` de cada clipe (seção 👁 Cenas, marcados como auto). Descrições feitas por você ou pela IA continuam lá.\n\n' + ex +
      ((r.errors || []).length ? `\n\nCom erro: ${r.errors.slice(0, 5).join('; ')}` : '') +
      '\n\nÉ uma primeira passada automática (tipo de plano, lugar, objetos, quem aparece, o que é falado). Para um clipe importante, selecione-o e peça a descrição com IA.');
    if (sel.length) {
      const b = document.createElement('button'); b.textContent = 'Descrever os selecionados com IA';
      b.onclick = async () => { b.disabled = true; await readContext();
        send(`Para cada clipe selecionado (${sel.map((p) => path.basename(p)).join(', ')}): contact_sheet e annotate com o que aparece em cada trecho (enquadramento, ação, pessoas, lugar, qualidade de uso), substituindo as descrições automáticas. Seja conciso.`); };
      d.appendChild(b);
    }
  }),
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
  stt: () => localJob('transcrição: lendo as sequências', async () => {
    await readContext(); await evalHost('claudioSave()');          // caption tracks are read from the saved project
    let seqs = [];
    try { seqs = JSON.parse(await evalHost('claudioSequences()')).sequences || []; } catch (e) {}
    // what each sequence plays on A1 (where the captions come from) AS IT IS NOW in Premiere, not a saved XML
    const media = {};
    for (const s of seqs) {
      const sn = await snapshot(s.id);
      const a1 = sn ? (sn.tracks.find((t) => t.track === 'A1') || { clips: [] }).clips : [];
      media[s.id] = [...new Set(a1.filter((c) => c.media && c.enabled !== false).map((c) => c.media))];
    }
    const r = await runLocal('stt_check', { paths: [...new Set(Object.values(media).flat())], prproj: ctx.project });
    if (!r.ok) return add('local err', r.error);
    sttMenu(seqs, media, r);
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
// ── Scribe (ElevenLabs) on/off, and the captions of the sequences Whisper transcribed, redone ──────────────────────
function paintStt(r) {
  if (r && r.ok !== false && $('#stt-label')) $('#stt-label').textContent = r.stt === 'elevenlabs' ? 'Scribe: ligado' : 'Scribe: desligado';
}
function sttMenu(seqs, media, r) {
  const clips = r.clips || {}, cap = r.captioned || {};
  const speech = (p) => clips[p] && clips[p].speech && !clips[p].lyrics;
  const whisper = (p) => speech(p) && clips[p].engine !== 'elevenlabs';
  const rows = seqs.filter((s) => media[s.id].some(speech));
  const on = r.stt === 'elevenlabs';
  const d = add('local', `Transcrição agora: **${on ? 'Scribe (ElevenLabs, pago)' : 'Whisper (local)'}**.` +
    (rows.length ? ` Sequências com fala (como estão agora na timeline) — marque as que quer legendar de novo com o Scribe:` : ' Nenhuma sequência deste projeto tem fala em A1.'), false);
  const tg = document.createElement('button');
  tg.textContent = on ? 'Desligar o Scribe (voltar ao Whisper)' : 'Ligar o Scribe';
  tg.onclick = async () => {
    const s = await runLocal('stt', { set: on ? 'whisper' : 'elevenlabs' });
    paintStt(s);
    tg.disabled = true;
    add('local', !s.ok ? s.error : s.stt === 'elevenlabs' ? 'Scribe **ligado**: o que for analisado daqui pra frente sai pelo ElevenLabs.' :
      (s.chosen === 'elevenlabs' && !s.has_key ? 'Não achei a chave do ElevenLabs (`~/.config/elevenlabs/api_key` no Ubuntu): continuo no Whisper.' : 'Scribe **desligado**: volta ao Whisper (local, grátis).'));
  };
  d.appendChild(tg);
  const picks = [];
  for (const s of rows) {
    const w = media[s.id].filter(whisper), n = media[s.id].filter(speech).length;
    const lb = document.createElement('label');
    lb.style.cssText = 'display:block;margin-top:5px;font-size:11px;cursor:pointer';
    const cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = w.length > 0 && (!!cap[s.name] || s.name === ctx.sequence);
    lb.append(cb, ` ${s.name} — ${n} clipe(s) com fala, ` + (w.length ? `${w.length} pelo Whisper` : 'já no Scribe') + (cap[s.name] ? ` · legendada (${cap[s.name]} blocos)` : ''));
    d.appendChild(lb);
    picks.push([cb, s]);
  }
  if (!rows.length) return;
  const go = document.createElement('button');
  const todo = () => [...new Set(picks.filter(([cb]) => cb.checked).flatMap(([, s]) => media[s.id].filter(whisper)))];
  const label = () => {
    const t = todo(), min = t.reduce((a, p) => a + (clips[p].duration || 0), 0) / 60;
    go.textContent = `Refazer com o Scribe + revisar gramática` + (t.length ? ` (${t.length} clipe(s), ~${min.toFixed(1)} min de áudio)` : '');
  };
  picks.forEach(([cb]) => (cb.onchange = label));
  label();
  go.className = 'primary';
  go.onclick = () => {
    const sel = picks.filter(([cb]) => cb.checked).map(([, s]) => s);
    if (!sel.length) return;
    d.querySelectorAll('button, input').forEach((x) => (x.disabled = true));
    sttRedo(sel, todo());
  };
  d.appendChild(go);
}
async function sttRedo(sel, todo) {
  let xmls = [];
  await localJob('Scribe', async () => {
    let s = await runLocal('stt', {});
    if (s.stt !== 'elevenlabs') s = await runLocal('stt', { set: 'elevenlabs' });
    paintStt(s);
    if (s.stt !== 'elevenlabs') return add('local err', 'Não achei a chave do ElevenLabs (`~/.config/elevenlabs/api_key` no Ubuntu): nada foi refeito.');
    const errs = [];
    for (let i = 0; i < todo.length; i++) {
      const nm = path.basename(todo[i].replace(/\\/g, '/'));
      toolStart(`Scribe ${i + 1}/${todo.length}: ${nm}`);
      const r = await runLocal('retranscribe', { path: todo[i] });
      if (!r.ok || r.stt_error || r.engine !== 'elevenlabs') errs.push(`${nm}: ${r.error || r.stt_error || 'ficou no Whisper'}`);
    }
    // the sequences exported NOW, one by one: the editor may have changed them since any XML on disk
    for (const q of sel) {
      toolStart(`exportando ${q.name}`);
      const sn = await snapshot(q.id);
      const p = sn && await backup(sn);
      if (p) xmls.push({ name: q.name, xml: p }); else errs.push(`${q.name}: não consegui exportar a sequência`);
    }
    add('local', `**${todo.length - errs.filter((e) => !/sequência$/.test(e)).length} de ${todo.length} clipe(s)** retranscritos pelo Scribe` +
      ` e **${xmls.length} sequência(s)** exportadas como estão agora.` + (errs.length ? '\n\nCom problema:\n' + errs.map((e) => '- ' + e).join('\n') : '') +
      (xmls.length ? '\n\nAgora a revisão de gramática (IA).' : ''));
  });
  if (!xmls.length) return;
  const out = jobPath('legendas');
  send(`Legendas refeitas com a transcrição do Scribe (ElevenLabs). As sequências abaixo foram exportadas AGORA do Premiere, do jeito que estão na timeline — use estes XMLs (não os de ${PROF.pastas.projeto}/xml nem versões antigas):\n` +
    xmls.map((x) => `- "${x.name}": ${x.xml}`).join('\n') +
    `\n\nPara cada uma: 1) caption_sentences(nle_xml) e corrija SÓ a gramática (concordância, regência, crase, pontuação), sem mudar as palavras do falante nem nomes de marca; o Scribe escreve número por extenso — valor, preço, data e porcentagem viram algarismos ("setecentos e cinquenta reais" → "R$ 750"); 2) captions_srt com o mesmo nle_xml, corrected (e client se houver perfil do cliente), out_srt "${out}\\<nome da sequência> (scribe).srt"; 3) uma tabela por sequência só com as falas que mudaram (fala → legenda). Não mexa nas sequências nem na timeline. No fim, liste os .srt e pergunte se quero que você coloque cada um como trilha de legenda NOVA na sua sequência (a legenda antiga fica; eu apago depois de conferir).`);
}
function droppedLine(r) {
  const d = r.dropped || [];
  if (!d.length) return '';
  const mg = d.filter((x) => /MOGRT/.test(x));
  return (mg.length ? `\n\n**${mg.length} legenda(s) MOGRT ficaram de fora** (o XML não leva o texto delas): aplique as legendas de novo na sequência nova com o 18 (Legenda MOGRT).` : '') +
    (d.length > mg.length ? `\n\nTambém ficou de fora: ${d.filter((x) => !/MOGRT/.test(x)).slice(0, 6).join('; ')}` : '');
}
async function mogrtRun(style, srt) {
  await localJob('legenda MOGRT (o After Effects monta o modelo na 1ª vez, ~1 min)', async () => {
    const x = await exportActive(); if (!x) return;
    const r = await runLocal('mogrt', { nle_xml: x, style, srt: srt ? 'auto' : null, job: jobFolder(), prproj: ctx.project, sequence: ctx.sequence });
    if (!r.ok || r.error) return add('local err', r.error || 'falhou');
    const m = add('local', `Modelo **${style}** pronto (\`${path.basename(r.mogrt.replace(/\\/g, '/'))}\`), **${r.blocks} blocos**` + (r.from_srt ? ' da legenda revisada' : '') + '. Colocando na sequência…' +
      ((r.learned || []).length ? `\n\nAprendi ${r.learned.length} correção(ões) de texto da letra (valem nas próximas legendas desta música): ` + r.learned.map((l) => `«${l.was}» → «${l.now}»`).join('; ') : ''));
    await placeMogrts(r.plan, null);
  });
}
async function multicamPlan() {
  await localJob('multicâmera: lendo as pastas', async () => {
    const r = await runLocal('multicam_plan', { folder: jobFolder() });
    if (!r.ok) return add('local err', r.error);
    const cams = Object.entries(r.cameras || {});
    const rows = cams.map(([n, c]) => `| ${n} | \`${path.basename(c.folder.replace(/\\/g, '/'))}\` | ${c.clips} | ${c.label} |`)
      .concat((r.external_audio || []).map((e) => `| áudio externo | \`${path.basename(e.folder.replace(/\\/g, '/'))}\` | ${e.files} | ${r.audio_label} |`));
    const d = add('local', `**Multicâmera** — achei ${cams.length} câmera(s)` + ((r.external_audio || []).length ? ' e um gravador externo' : '') + ':\n\n' +
      '| fonte | pasta | arquivos | cor |\n|---|---|---|---|\n' + rows.join('\n') +
      (r.music ? `\n\n**Parece um clipe musical:** \`${path.basename(r.guide.replace(/\\/g, '/'))}\` vira a trilha guia, cada take é alinhado contra ela e os takes que se sobrepõem ficam em trilhas empilhadas. Takes de refrão repetido saem marcados para você conferir.` : '') +
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
    const r = await runLocal('sync', { cameras, external_audio: (plan.external_audio || []).map((e) => e.folder), labels, music: !!plan.music,
                                       out_xml: jobPath('projeto', 'claudio_sync.xml') });
    if (!r.ok) return add('local err', r.error);
    const clips = r.clips || [];
    const placed = clips.filter((c) => c.start_s != null);
    const approx = placed.filter((c) => /relógio|aprox/i.test(String(c.method || '')));
    const amb = placed.filter((c) => c.also_fits_at_s && c.also_fits_at_s.length);
    const dr = r.drift;
    const drift = dr ? [`${dr.reference}: ${dr.ppm} ppm` + (dr.corrected_file ? ` → \`${path.basename(dr.corrected_file.replace(/\\/g, '/'))}\`` : '')] : [];
    const m = add('local', `**Sincronizado:** ${placed.length} de ${clips.length} clipe(s)` + (approx.length ? `, ${approx.length} pelo relógio da câmera (aproximados — sem áudio em comum)` : '') + '.' +
      ((r.unplaced || []).length ? `\n\nNão posicionados: ${r.unplaced.slice(0, 8).join(', ')}` : '') +
      (amb.length ? '\n\n**Confira (trecho repetido da música, ex.: refrão):**\n\n| take | coloquei em | também cabe em |\n|---|---|---|\n' +
        amb.map((c) => `| ${c.clip} | ${dur(c.start_s)} | ${c.also_fits_at_s.map(dur).join(', ')} |`).join('\n') : '') +
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
  const mc = snap.tracks.some((t) => t.clips.some((c) => c.multicam));
  return `Trilhas (clipes por trilha): ${fmt('V')} | ${fmt('A')}\n` +
    (snap.json ? `Trilhas COMPLETAS lidas direto do Premiere (cada clipe: trilha, início/fim na timeline, entrada/saída na fonte, mídia; multicam marcados, com a sequência-fonte e as trilhas dela em multicam_sources): ${snap.json}\n` : '') +
    (mc ? 'ATENÇÃO: esta sequência tem clipes MULTICAM, que o FCP XML não exporta (vêm vazios). Para ler cortes, posições e a música use o JSON acima (Read), não o XML; a câmera ativa de cada trecho não é exposta — se precisar, olhe um quadro com export_frame do MCP do Premiere.\n' : '');
}
// the full read of every track, saved next to the XML backup: works where the XML does not (multicam)
async function saveTracks(snap, xmlPath) {
  if (!snap || !xmlPath) return snap;
  const out = { sequence: snap.name, id: snap.id, tracks: snap.tracks, multicam_sources: {} };
  const names = new Set();
  snap.tracks.forEach((t) => t.clips.forEach((c) => { if (c.multicam && c.source) names.add(c.source); }));
  for (const n of names) {
    try {
      const id = await evalHost('claudioSeqIdByName(' + JSON.stringify(n) + ')');
      const src = id ? await snapshot(id) : null;
      if (src) out.multicam_sources[n] = { tracks: src.tracks.filter((t) => t.clips.length) };
    } catch (e) {}
  }
  const p = xmlPath.replace(/\.xml$/i, '') + ' — trilhas.json';
  try { fs.writeFileSync(p, JSON.stringify(out, null, 1)); snap.json = p; } catch (e) {}
  return snap;
}
function contextBlock(snap, xml) {
  const sel = (ctx.selection || []).map((s) => `  - ${s.name} (${s.track_type}) timeline ${(+s.start_s).toFixed(2)}–${(+s.end_s).toFixed(2)}s, fonte ${(+s.in_s).toFixed(2)}–${(+s.out_s).toFixed(2)}s${s.media ? ' · ' + s.media : ''}`).join('\n');
  const warn = lastWarning; lastWarning = '';
  return warn + `[Contexto do Premiere — painel Cláudio, o Editor]\nPasta do trabalho: ${jobFolder()}\nProjeto: ${ctx.project || '-'}\nSequência ativa: ${ctx.sequence || '-'} (${ctx.frame_size || '-'})\nPlayhead: ${ctx.playhead_s != null ? (+ctx.playhead_s).toFixed(2) + 's' : '-'}\n${tracksLine(snap)}${xml ? `XML da sequência ativa, exportado agora pelo painel: ${xml}\n` : ''}${sel ? 'Clipes selecionados:\n' + sel : 'Nenhum clipe selecionado.'}\nRegras: para LER a sequência (compare_with_nle, qc_timeline, caption_sentences, stereo_fix, flatten_timeline…) use o XML acima e as trilhas acima (o JSON das trilhas completas quando o XML vier incompleto, ex.: multicam) — não exporte nem liste pelo MCP do Premiere; use o MCP do Premiere só para MEXER no Premiere (importar mídia, criar trilha, colocar clipe, marcadores). O editor está no controle — proponha antes de mudar; nunca sobrescreva a sequência dele (crie uma nova); só renderize se ele pedir. Nunca aninhe sequências: os clipes montados vão direto nas trilhas de UMA sequência (o editor corrige a cor take a take); para juntar partes, monte uma sequência só ou rode flatten_timeline. Overlays, legendas e qualquer mídia nova entram SEMPRE numa trilha NOVA acima de todas as que têm clipes: nunca use overwrite/insert numa trilha ocupada (adjustment layer, títulos, b-roll) — liste as trilhas antes e, se não tiver certeza, pergunte. XML (.xml) nunca vai pelo import_media do Premiere (derruba o Premiere): escreva o caminho completo do XML na resposta — o painel mostra um botão \"Importar no Premiere\" — ou ele importa com Ctrl+I; mídia comum (.mov/.wav/.srt) pode ir pelo import_media. Para pôr um clipe na timeline use overwrite_clip numa trilha nova — NUNCA add_to_timeline/insert, que empurra a sequência inteira pra direita. Memória: este chat é separado das outras sessões; o que importar para depois vai para o vault (conector Vault) e para os arquivos do claudio no disco — ofereça registrar no vault quando houver decisões.\n\n`;
}

// Only claudio + premiere-pro are loaded (the same registrations as ~/.claude.json): otherwise every message also
// starts every other MCP and claude.ai connector (Gmail, Drive, Resolve…) and pays for their tools and instructions.
// The claude.ai connectors (Vault, Storyblocks) only exist in the full setup (~45% more input tokens per message), so
// a message that asks for them gets it, and the Vault·Stock toggle turns it on for every message.
const WANTS_CONNECTORS = /\bvault\b|storyblocks|banco de (imagens|vídeos)|stock/i;
let connectorsOn = false;
try { connectorsOn = localStorage.getItem('claudio-connectors') === '1'; } catch (e) {}
// ── one Premiere MCP that stays up (premiere_bridge.js): its UXP bridge stays connected between messages ──────────
// A fresh premiere-pro-mcp per message held the UXP port only while the message ran: the UXP plugin sat
// "disconnected" and early UXP calls raced its reconnect. The bridge runs one copy and serves it as HTTP.
const BRIDGE_PORT = 7788, BRIDGE_URL = `http://127.0.0.1:${BRIDGE_PORT}/mcp`;
const EXT_DIR = path.dirname(decodeURIComponent(new URL(location.href).pathname)).replace(/^\/([A-Za-z]:)/, '$1');
let bridgeUp = false;
function bridgeHealth() {
  return new Promise((res) => {
    const rq = require('http').get(`http://127.0.0.1:${BRIDGE_PORT}/health`, { timeout: 800 }, (r) => {
      let b = ''; r.on('data', (d) => (b += d)); r.on('end', () => { try { res(JSON.parse(b).ok === true); } catch (e) { res(false); } });
    });
    rq.on('error', () => res(false)); rq.on('timeout', () => { rq.destroy(); res(false); });
  });
}
async function ensureBridge() {
  if (await bridgeHealth()) return (bridgeUp = true);
  try {
    const m = JSON.parse(fs.readFileSync(path.join(os.homedir(), '.claude.json'), 'utf8')).mcpServers['premiere-pro'];
    const node = /node(\.exe)?$/i.test(m.command) ? m.command : 'node';
    cp.spawn(node, [path.join(EXT_DIR, 'premiere_bridge.js'), '--port', String(BRIDGE_PORT)],
             { detached: true, stdio: 'ignore', windowsHide: true, env: childEnv() }).unref();
  } catch (e) { return (bridgeUp = false); }
  for (let i = 0; i < 20; i++) {                     // the server initializes in ~3 s
    await new Promise((r) => setTimeout(r, 500));
    if (await bridgeHealth()) return (bridgeUp = true);
  }
  return (bridgeUp = false);
}
// the soundtrack flow spans several messages ("a segunda", "pode baixar"): Storyblocks stays loaded for a while
let connectorsUntil = 0;
function mcpArgs(text) {
  if (connectorsOn || WANTS_CONNECTORS.test(text) || Date.now() < connectorsUntil) return [];
  try {
    const all = (JSON.parse(fs.readFileSync(path.join(os.homedir(), '.claude.json'), 'utf8')).mcpServers) || {};
    if (!all.claudio || !all['premiere-pro']) return [];
    const f = path.join(os.tmpdir(), 'claudio-panel-mcp.json');
    const pr = bridgeUp ? { type: 'http', url: BRIDGE_URL } : all['premiere-pro'];   // no bridge: the old per-message copy
    fs.writeFileSync(f, JSON.stringify({ mcpServers: { claudio: all.claudio, 'premiere-pro': pr } }));
    // without the connectors the AI must not try the vault (it wrote the notes out for me to paste): one line pointing
    // to button 18, which runs with them and resumes this same conversation
    return ['--strict-mcp-config', '--mcp-config', f, '--append-system-prompt',
      'Nesta mensagem o conector Vault NÃO está carregado (economia de tokens do painel). Não tente registrar no vault nem ' +
      'escreva o texto para colar: se houver decisão ou informação nova que valha registrar, termine com uma linha só ' +
      'pedindo para apertar "20 Registrar no vault" — esse botão roda com o conector e continua esta mesma conversa.'];
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
  im.onclick = () => zoomImage(im.src);
  d.appendChild(im);
  log.appendChild(d);
  log.scrollTop = log.scrollHeight;
}
// the panel is narrow, so "ampliar" is a viewer over the whole panel: fit, click for 100 % (scrolls), Esc/backdrop closes,
// and "abrir fora" hands it to the system viewer to zoom for real
function zoomImage(src) {
  const box = document.createElement('div'), im = document.createElement('img'), bar = document.createElement('div');
  box.id = 'zoom'; bar.className = 'zbar';
  im.src = src; im.title = 'clique: tamanho real / ajustar';
  const btn = (label, fn) => { const b = document.createElement('button'); b.textContent = label; b.onclick = (e) => { e.stopPropagation(); fn(); }; bar.appendChild(b); };
  btn('abrir fora', () => {
    const m = src.match(/^data:image\/(\w+);base64,(.*)$/);
    if (!m) return;
    const f = path.join(os.tmpdir(), `claudio-imagem-${Date.now()}.${m[1] === 'jpeg' ? 'jpg' : m[1]}`);
    try { fs.writeFileSync(f, Buffer.from(m[2], 'base64')); openExternal(f); } catch (e) {}
  });
  btn('✕ fechar', () => close());
  const close = () => { box.remove(); document.removeEventListener('keydown', onKey); };
  const onKey = (e) => { if (e.key === 'Escape') close(); };
  im.onclick = (e) => { e.stopPropagation(); box.classList.toggle('full'); };
  box.onclick = close;
  box.append(bar, im);
  document.body.appendChild(box);
  document.addEventListener('keydown', onKey);
}
// links and images leave the panel through the system: CEP's own call for web links, the OS opener otherwise
function openExternal(target) {
  if (/^https?:\/\//i.test(target)) {
    try { if (window.cep && window.cep.util && window.cep.util.openURLInDefaultBrowser) return window.cep.util.openURLInDefaultBrowser(target); } catch (e) {}
  }
  try {
    if (process.platform === 'win32') cp.spawn('rundll32', ['url.dll,FileProtocolHandler', target], { detached: true, stdio: 'ignore' }).unref();
    else cp.spawn(process.platform === 'darwin' ? 'open' : 'xdg-open', [target], { detached: true, stdio: 'ignore' }).unref();
  } catch (e) {}
}
log.addEventListener('click', (e) => {
  const a = e.target.closest && e.target.closest('a[href]');
  if (!a) return;
  e.preventDefault();
  if (/^https?:\/\//i.test(a.href)) openExternal(a.href);
});

async function send(text) {
  if (!text.trim() || child) return;
  await readContext();
  add('user', text);
  await ensureBridge();
  const before = ctx.sequence ? await snapshot('') : null;
  const bk = before ? await backup(before) : null;
  if (before && bk) await saveTracks(before, bk);
  const sid = localStorage.getItem(sessionKey);
  const model = ($('#model') && $('#model').value) || 'sonnet';
  // the message goes in on stdin (stream-json) and stdin stays open while Claude works: what the editor types meanwhile
  // is written there too (complement) and Claude reads it between tool calls, in the same task. Closed at the result.
  const args = ['-p', '--input-format', 'stream-json', '--output-format', 'stream-json', '--verbose', '--model', model,
                '--allowedTools', 'mcp__claudio,mcp__premiere-pro,mcp__claude_ai_Vault,mcp__claude_ai_Storyblocks,Read,Glob,Grep',
                // allowedTools only pre-approves; this BLOCKS shell and file edits from the panel
                '--disallowedTools', 'Bash,PowerShell,Edit,Write,NotebookEdit',
                // the only built-in tools the panel uses (~20k tokens less per message). ToolSearch must stay: without
                // it every Premiere MCP tool definition is sent up front (~580k tokens, more than fits)
                '--tools', 'Read,Glob,Grep,ToolSearch', ...mcpArgs(text)];
  if (sid) args.push('--resume', sid);
  $('#send').textContent = 'Complementar'; $('#stop').disabled = false; document.body.classList.add('busy');
  $('#input').placeholder = 'Faltou algo? Escreva e ele junta à tarefa atual, sem parar…';
  $('#status').textContent = 'pensando…';
  let cur = null, buf = '', why = '', errTail = '';
  child = cp.spawn(claudeExe(), args, { cwd: jobFolder(), windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'], env: childEnv() });
  child.stdin.on('error', () => {});
  tell(contextBlock(before, bk) + text);
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
          if (b.type === 'text' && b.text.trim()) {
            cur = add('claude', b.text); importButtons(cur, b.text); $('#status').textContent = '';
            cur.querySelectorAll('button.music:not([disabled])').forEach((m) => placeMusic(m.dataset.plan, m));
          }
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
      // why it failed, in Claude's own words (expired login, unknown session…), for the message at the end
      if (ev.type === 'assistant' && ev.error && ev.message && Array.isArray(ev.message.content)) {
        why = ev.message.content.map((c) => c.text || '').join(' ').trim() || String(ev.error);
      }
      if (ev.type === 'result') {
        // a complement sent too late for this turn is still in stdin: closing only ends the input, Claude answers it first
        try { child && child.stdin.end(); } catch (e) {}
        if (ev.is_error && typeof ev.result === 'string' && ev.result.trim()) why = ev.result.trim();
        if (ev.session_id) { localStorage.setItem(sessionKey, ev.session_id); saveHist(); }
        const s = ev.total_cost_usd != null ? ` · US$ ${ev.total_cost_usd.toFixed(2)}` : '';
        add('meta', `${ev.is_error ? 'erro' : 'pronto'} · ${(ev.duration_ms / 1000).toFixed(0)} s${s}`);
      }
    }
  });
  child.stderr.on('data', (d) => { const t = d.toString().trim(); if (t) { errTail = (errTail + '\n' + t).slice(-600); $('#status').textContent = t.slice(-160); } });
  child.on('close', (code) => {
    child = null; $('#send').textContent = 'Enviar'; $('#input').placeholder = PLACEHOLDER; $('#stop').disabled = true; document.body.classList.remove('busy'); toolStart(null);
    if (code && code !== 0) {
      const txt = (why || errTail || '').trim();
      if (/authenticat|oauth|log ?in|not logged|credential/i.test(txt))
        add('claude err', `**O login do Claude Code no Windows venceu.** Abra um PowerShell, rode \`${claudeExe()}\`, digite \`/login\`, entre pelo navegador e saia com \`/exit\`. Depois mande a mensagem de novo.\n\n_${txt.slice(0, 200)}_`);
      else if (/session|conversation/i.test(txt) && /not found|no conversation|invalid/i.test(txt))
        add('claude err', `A conversa salva deste trabalho não abriu (${txt.slice(0, 160)}). Clique em **nova conversa** e mande de novo.`);
      else
        add('claude err', `O Claude Code terminou com código ${code}` + (txt ? `: ${txt.slice(0, 300)}` : `. Ele está instalado e logado no Windows? (${claudeExe()})`));
    }
    $('#status').textContent = '';
    readContext();
    if (before) snapshot(before.id).then((after) => { const ch = changedClips(before, after); if (ch.length) warnChanges(ch, before, bk); });
  });
}

const PLACEHOLDER = $('#input').placeholder;
function tell(text) {
  child.stdin.write(JSON.stringify({ type: 'user', message: { role: 'user', content: text } }) + '\n');
}
// while Claude works the composer adds to the same task instead of waiting or interrupting
function complement(text) {
  if (!text.trim() || !child || !child.stdin.writable) return false;
  add('user more', text);
  tell('[Complemento do editor, enviado enquanto você trabalhava — vale para a tarefa atual, sem recomeçar o que já foi feito]\n' + text);
  return true;
}

$('#send').onclick = () => {
  const t = $('#input').value;
  if (child ? !complement(t) : !t.trim()) return;   // nothing went out: keep what was typed
  $('#input').value = '';
  if (!child) send(t);
};
$('#input').addEventListener('keydown', (e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); $('#send').click(); } });
// on the Mac Premiere swallows modifier shortcuts before the panel sees them unless the panel asks for them (36 = Return, 76 = keypad Enter)
if (process.platform === 'darwin') try {
  window.__adobe_cep__.registerKeyEventsInterest(JSON.stringify([36, 76].flatMap((keyCode) => [{ keyCode, metaKey: true }, { keyCode, ctrlKey: true }])));
} catch (e) {}
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
  await readContext();
  if (b.dataset.q === 'captions_final') await evalHost('claudioSave()');   // the fixed caption is read from the saved project
  send(QUICK[b.dataset.q]());
}));
try { const m = localStorage.getItem('claudio-model'); if (m && $('#model')) $('#model').value = m; } catch (e) {}
if ($('#conn')) {
  const paint = () => $('#conn').setAttribute('aria-pressed', connectorsOn ? 'true' : 'false');
  paint();
  $('#conn').onclick = () => { connectorsOn = !connectorsOn; paint(); try { localStorage.setItem('claudio-connectors', connectorsOn ? '1' : '0'); } catch (e) {} };
}
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

runLocal('profile', {}).then((r) => { if (r && r.pastas) PROF = r; }).catch(() => {});
renderRecipeButtons(RECIPES);
loadRecipes().catch(() => {});
readContext().then(() => { add('meta', `pasta do trabalho: ${jobFolder()}`, false); showProgress(); });
runLocal('stt', {}).then(paintStt).catch(() => {});
ensureBridge().catch(() => {});
setInterval(() => { if (!child) readContext(); }, 4000);
