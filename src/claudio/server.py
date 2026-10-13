"""Cláudio, o Editor (MCP server "claudio") — lets Claude actually SEE and HEAR footage before cutting in Premiere / Resolve.

Run:  python3 server.py            (stdio MCP)
Workflow the tools are designed for:
  1. index_media / index_folder   → probe, transcript (word timestamps), cuts, focus/exposure/motion
  2. overview                     → what is in the clip (speech? b-roll? shots, takes, usable windows)
  3. contact_sheet / timeline_strip / frames  → LOOK at the picture (and words + waveform) at exact times
  4. takes (dialogue) · broll (no speech) · find_phrase · find_shot (describe a shot) · people / find_person → evidence-ranked candidates
  5. annotate                     → write the decupagem log (what you saw) so it persists
  6. snap_cut → export_timeline / nle_cut_list   → frame-accurate handoff to the Premiere / Resolve MCPs
  7. build_edit → edit_storyboard → export_edit    → a reviewable timeline in Premiere / Resolve (no render)
  Multicam: sync_multicam (PluralEyes-style, from audio) → XML. Every indexed clip gets a <clip>.md next to it.
  Log footage (S-Log/C-Log/BMD) looks flat on purpose — the user grades it in the NLE. Render only when asked.
"""
import os, queue, sys, threading, traceback

from mcp.server.fastmcp import FastMCP, Image
from claudio import core, montage

mcp = FastMCP('claudio', instructions=__doc__)

# ── background indexing (GPU work is serialized) with progress the user can follow ──
import time as _time
from mcp.server.fastmcp import Context
from claudio import sidecar

_jobs, _q = {}, queue.Queue()
STAGE_W = {'probe': (0.0, 0.02), 'audio': (0.02, 0.08), 'picture': (0.10, 0.65), 'speech': (0.75, 0.25)}
_batch = {'total_s': 0.0, 'done_s': 0.0, 't_start': None, 'busy_s': 0.0}


def _job_fraction(j):
    if j['state'] == 'done':
        return 1.0
    if j['state'] != 'running' or not j.get('stage'):
        return 0.0
    base, w = STAGE_W.get(j['stage'], (0.0, 0.0))
    return min(1.0, base + w * j.get('progress', 0))


def _worker():
    while True:
        path, lang, force, vocab, md = _q.get()
        job = _jobs[path]
        t0 = _time.time()
        try:
            job['state'] = 'running'
            idx = core.build_index(path, lang, lambda stage, f: job.update(stage=stage, progress=round(min(f, 1), 3)), force, vocab)
            job['state'] = 'done'
            if md:
                try:
                    job['md'] = core.nle_path(sidecar.write_clip_md(idx))
                except Exception as e:
                    job['md_error'] = str(e)
        except Exception as e:
            job.update(state='error', error=f'{e}', trace=traceback.format_exc()[-1500:])
        finally:
            _batch['done_s'] += job.get('duration', 0)
            _batch['busy_s'] += _time.time() - t0
            if md:
                try:
                    sidecar.write_folder_md(os.path.dirname(path), progress=None if _q.qsize() == 0 else _progress_line())
                except Exception:
                    pass
            _q.task_done()


threading.Thread(target=_worker, daemon=True).start()


def _enqueue(path, lang, force=False, vocabulary=None, md=True):
    path = core.local_path(path)
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    j = _jobs.get(path)
    if j and j['state'] in ('queued', 'running'):
        return path
    try:
        dur = float(core.light_index(path)['info']['duration'])
    except Exception:
        dur = 0.0
    if _q.qsize() == 0 and not any(x['state'] == 'running' for x in _jobs.values()):
        _batch.update(total_s=0.0, done_s=0.0, t_start=_time.time(), busy_s=0.0)
    _batch['total_s'] += dur
    _jobs[path] = {'state': 'queued', 'stage': None, 'progress': 0, 'duration': dur, 'name': os.path.basename(path)}
    _q.put((path, lang, force, vocabulary, md))
    return path


def _progress():
    """Batch progress in seconds of footage, measured speed and time left."""
    run = [j for j in _jobs.values() if j['state'] == 'running']
    cur = run[0] if run else None
    done_s = _batch['done_s'] + (cur['duration'] * _job_fraction(cur) if cur else 0)
    total = max(_batch['total_s'], 1e-6)
    el = _time.time() - (_batch['t_start'] or _time.time())
    speed = done_s / el if el > 5 and done_s > 0 else None            # seconds of footage per second
    eta = (total - done_s) / speed if speed else None
    n_all = sum(1 for j in _jobs.values() if j['state'] in ('queued', 'running', 'done', 'error'))
    n_done = sum(1 for j in _jobs.values() if j['state'] in ('done', 'error'))
    return {'fraction': round(done_s / total, 4), 'files_done': n_done, 'files_total': n_all,
            'current': cur['name'] if cur else None, 'stage': (cur or {}).get('stage'), 'stage_progress': (cur or {}).get('progress'),
            'speed_x_realtime': round(speed, 2) if speed else None, 'eta_min': round(eta / 60, 1) if eta else None,
            'errors': [j['name'] for j in _jobs.values() if j['state'] == 'error']}


STAGE_PT = {'probe': 'lendo metadados', 'audio': 'áudio', 'picture': 'imagem (cortes, foco, movimento)', 'speech': 'transcrição'}


def _progress_line(p=None):
    p = p or _progress()
    s = f"{int(p['fraction'] * 100)}% · {p['files_done']}/{p['files_total']} clipes"
    if p['current']:
        s += f" · agora: {p['current']} ({STAGE_PT.get(p['stage'], p['stage'] or '')} {int((p['stage_progress'] or 0) * 100)}%)"
    if p['eta_min'] is not None:
        s += f" · ~{p['eta_min']:.0f} min restantes ({p['speed_x_realtime']}× tempo real)"
    return s


def _idx(path, lang='pt'):
    """Return the index, or raise a clear 'still indexing' error the model can act on."""
    p = core.local_path(path)
    j = _jobs.get(p)
    if j and j['state'] in ('queued', 'running'):
        raise RuntimeError(f'still indexing — {_progress_line()} — call index_status, then retry')
    try:
        ip = os.path.join(core.cache_dir(p), 'index.json')
    except FileNotFoundError:
        raise FileNotFoundError(f'not found: {p}')
    if not os.path.exists(ip):
        _enqueue(p, lang)
        raise RuntimeError('not indexed yet — indexing started; call index_status and retry when done')
    return core.build_index(p, lang)


def _summary(idx):
    i = idx['info']
    out = {k: i.get(k) for k in ('name', 'nle_path', 'duration', 'fps', 'width', 'height', 'start_tc', 'codec',
                                 'rotation', 'vfr', 'audio_channels')}
    out['user_rotation'] = core.get_rotation(i['path'])
    if idx.get('audio'):
        a = idx['audio']
        out['audio'] = {k: a[k] for k in ('speech_ratio', 'noise_floor_db', 'peak_db', 'clipped_ms')}
    if idx.get('picture'):
        out['hard_cuts_sec'] = idx['picture']['cuts_sec'][:60]
    try:
        c = core.color_profile(idx) if i.get('has_video') else None
        if c:
            out['camera'] = c.get('camera')
            out['color'] = {k: c.get(k) for k in ('profile', 'confidence', 'log', 'note')}
    except Exception:
        pass
    sp = idx.get('speech')
    out['kind'] = 'dialogue' if sp and sp['n_words'] > max(8, i['duration'] / 6) else 'mostly_no_speech'
    if sp:
        out['language'] = sp['language']
        out['words'] = sp['n_words']
    return out


async def _wait_with_progress(ctx, paths, max_wait_s):
    """Report progress to the client every 2 s (shown to the user) until the jobs finish or max_wait_s passes."""
    import asyncio
    t0 = _time.time()
    last = None
    while _time.time() - t0 < max_wait_s:
        pend = [p for p in paths if _jobs.get(p, {}).get('state') in ('queued', 'running')]
        pr = _progress()
        line = _progress_line(pr)
        if ctx is not None and line != last:
            try:
                await ctx.report_progress(pr['fraction'] * 100, 100, line)
            except Exception:
                pass
            last = line
        if not pend:
            return True
        await asyncio.sleep(2)
    return False


# ───────────────────────────── indexing ─────────────────────────────
@mcp.tool()
async def index_media(path: str, ctx: Context, language: str = 'pt', wait_seconds: int = 600, force: bool = False,
                      vocabulary: str = None) -> dict:
    """Analyse one video (Windows path like D:\\Footage\\A001.mov or Linux path): probe, camera + colour space (log vs
    Rec.709), audio, picture (cuts, sharpness, motion), transcript with word timestamps. Writes <clip>.md next to the
    video. Shows progress while it runs; returns when done (or state=running after wait_seconds — index_status)."""
    p = _enqueue(path, language, force, vocabulary) if force or vocabulary or not _is_indexed(path) else core.local_path(path)
    await _wait_with_progress(ctx, [p], wait_seconds)
    j = _jobs.get(p, {'state': 'done'})
    if j['state'] == 'done':
        return {'state': 'done', 'md': j.get('md'), **_summary(core.build_index(p, language))}
    return {'path': p, 'progress': _progress_line(), **{k: v for k, v in j.items() if k != 'trace'}}


def _is_indexed(path):
    try:
        idx_p = os.path.join(core.cache_dir(core.local_path(path)), 'index.json')
        return os.path.exists(idx_p)
    except FileNotFoundError:
        return False


@mcp.tool()
def set_rotation(degrees: int, paths: list[str] = None, folder: str = None) -> dict:
    """Clips shot with the camera on its side (vertical framing) often carry no rotation metadata: if a contact sheet
    shows people sideways, set 90 (clockwise) / 270 / 180 here. Applies everywhere after: sheets, crops, renders, XML,
    and the clip's .md (its contact sheet is redrawn upright)."""
    files = [core.local_path(p) for p in (paths or [])]
    if folder:
        root = core.local_path(folder)
        files += [os.path.join(dp, f) for dp, _, fn in os.walk(root) for f in sorted(fn) if os.path.splitext(f)[1].lower() in core.VIDEO_EXT]
    for f in files:
        core.set_rotation(f, degrees)
        sp = os.path.join(core.data_dir(os.path.dirname(f), create=False), os.path.splitext(os.path.basename(f))[0] + '.jpg')
        if os.path.exists(sp):
            os.remove(sp)                      # our own contact sheet: redraw it upright
        if _is_indexed(f):
            try:
                sidecar.write_clip_md(core.build_index(f))
            except Exception:
                pass
    return {'rotated': [core.nle_path(f) for f in files], 'degrees': degrees}


@mcp.tool()
async def index_folder(folder: str, ctx: Context, language: str = 'pt', vocabulary: str = None, wait_minutes: float = 10) -> dict:
    """Index every video in a folder (card dump / VIDEOS / BRUTOS). Runs in its OWN background process — it keeps going
    if this chat ends or the Premiere panel closes — and skips clips already indexed. Writes <clip>.md + contact sheet
    next to each video and keeps _CLAUDIO.md updated with the progress. Shows live progress here for up to wait_minutes,
    then returns; index_status(folder) reports it any time later.
    vocabulary: names the transcript must get right — brand, people, places ("INC Empreendimentos, Juiz de Fora").
    Spellings the editor fixed in captions are learned per folder and added here automatically; caption tools return
    them as learned_vocabulary — offer to save them in the client profile (save_client_profile).
    4K long-GOP footage the GPU can't decode (e.g. Canon HEVC 4:2:2) is analysed from keyframes (~3× faster);
    proxies (Proxy/ or Proxies/) are used when present; links always point to the originals."""
    import asyncio, subprocess as sp
    from claudio import indexer
    root = core.local_path(folder)
    files = indexer.list_videos(root)
    from claudio import motion
    new_terms = motion.unapplied_vocabulary(root)  # spellings the editor fixed in captions, not yet applied here
    if new_terms:
        have = {x.strip().lower() for x in (vocabulary or '').split(',') if x.strip()}
        extra = [t for t in motion.folder_vocabulary(root) if t.lower() not in have]
        vocabulary = ', '.join(([vocabulary] if vocabulary else []) + extra)   # re-applied to stored words: no re-transcription
        motion.mark_vocabulary_applied(root)
    pending = [f for f in files if not _is_indexed(f) or vocabulary]
    j = indexer.read_job(root)
    if pending and not (j and j.get('state') == 'running'):
        indexer.launch_detached(root, vocabulary, language)      # survives the chat, the panel and Claude Desktop
        await asyncio.sleep(4)
    # already-indexed clips without their .md get one now
    for f in files:
        if f not in pending:
            try:
                if not os.path.exists(sidecar.md_path(f)):
                    sidecar.write_clip_md(core.build_index(f))
            except Exception:
                pass
    t0 = _time.time()
    j = indexer.read_job(root)
    while pending and _time.time() - t0 < wait_minutes * 60:
        j = indexer.read_job(root)
        if not j or j.get('state') != 'running':
            break
        try:
            await ctx.report_progress(j.get('fraction', 0) * 100, 100, indexer.progress_line(j))
        except Exception:
            pass
        await asyncio.sleep(3)
    j = indexer.read_job(root) if pending else j
    return {'videos': len(files), 'to_index': len(pending), 'already_indexed': len(files) - len(pending),
            'progress': indexer.progress_line(j) if pending else 'tudo já indexado',
            'state': (j or {}).get('state', 'done' if not pending else 'starting'),
            'continues_in_background': bool(j and j.get('state') == 'running'),
            'errors': (j or {}).get('errors', []),
            'folder_index': core.nle_path(os.path.join(root, '_CLAUDIO.md'))}


@mcp.tool()
def index_status(folder: str = None) -> dict:
    """Progress of indexing: percentage, clips done/total, current clip and step, speed, minutes left — for a folder or
    for every recent job. Background folder jobs survive the chat that started them. Tell the user the `progress` line."""
    from claudio import indexer
    jobs = [indexer.read_job(folder)] if folder else indexer.all_jobs()
    jobs = [j for j in jobs if j]
    out = {'jobs': [{'folder': j['folder'], 'progress': indexer.progress_line(j), 'state': j['state'], 'errors': j.get('errors', [])[:10]} for j in jobs]}
    pr = _progress()
    if pr['files_total']:
        out['this_session'] = _progress_line(pr)
    return out


@mcp.tool()
def write_sidecars(folder: str) -> dict:
    """(Re)write <clip>.md + contact sheets for every indexed video in a folder, and the folder's _CLAUDIO.md."""
    root = core.local_path(folder)
    out = []
    for dp, dn, fn in os.walk(root):
        dn[:] = [x for x in dn if x.lower() not in core.PROXY_DIRS and not x.startswith(('_claudio', '_decupa', '.'))]
        for f in sorted(fn):
            p = os.path.join(dp, f)
            if os.path.splitext(f)[1].lower() in core.VIDEO_EXT and _is_indexed(p):
                out.append(core.nle_path(sidecar.write_clip_md(core.build_index(p))))
        sidecar.write_folder_md(dp)
    return {'written': len(out), 'files': out[:50]}


@mcp.tool()
def overview(path: str) -> dict:
    """Everything known about one indexed clip in compact form: technical info, kind (dialogue vs no speech),
    transcript segments with times, hard cuts, top b-roll windows and the existing decupagem log."""
    idx = _idx(path)
    out = _summary(idx)
    if idx.get('speech'):
        out['transcript'] = [{'s': s['s'], 'e': s['e'], 'text': s['text']} for s in idx['speech']['segments']][:200]
    out['broll_top'] = [{k: w[k] for k in ('s', 'e', 'camera', 'score')} for w in core.broll_windows(idx)['windows'][:8]]
    out['log'] = core.read_log(idx)
    return out


@mcp.tool()
def library(folder: str) -> list:
    """One line per indexed clip in a folder: duration, kind, words, shots, and log entries — to plan an edit
    across many takes/cards before looking closer."""
    root = core.local_path(folder)
    out = []
    for dp, _, fn in os.walk(root):
        for f in sorted(fn):
            p = os.path.join(dp, f)
            if os.path.splitext(f)[1].lower() in core.VIDEO_EXT and _is_indexed(p):
                idx = core.build_index(p)
                s = _summary(idx)
                out.append({'path': s['nle_path'], 'dur': round(s['duration'], 1), 'kind': s['kind'],
                            'words': s.get('words', 0), 'shots': len(s.get('hard_cuts_sec', [])) + 1,
                            'first_words': (idx['speech']['segments'][0]['text'][:80] if idx.get('speech') and idx['speech']['segments'] else ''),
                            'log': [l['text'] for l in core.read_log(idx)][:5]})
    return out


# ───────────────────────────── seeing ─────────────────────────────
@mcp.tool()
def contact_sheet(path: str, start: float = None, end: float = None, count: int = 12) -> Image:
    """LOOK at the clip: a grid of `count` frames evenly spaced over [start, end) seconds (whole clip if
    omitted). Every tile is stamped with source timecode, clip seconds and the words spoken at that instant.
    Use 12–24 for a whole clip, then zoom into ranges."""
    sheet, _ = core.contact_sheet(_idx(path), start, end, max(2, min(count, 48)))
    return Image(data=core.to_jpeg(sheet), format='jpeg')


@mcp.tool()
def timeline_strip(path: str, start: float, end: float, thumbs: int = 8) -> Image:
    """For picking exact in/out points (keep ranges ≤ ~20 s): thumbnails on top, audio waveform (blue = above
    silence threshold), each transcribed word as a box on the same time axis (red box = low ASR confidence),
    hard cuts as red lines, and a seconds ruler."""
    return Image(data=core.to_jpeg(core.timeline_strip(_idx(path), start, end, max(2, min(thumbs, 16)))), format='jpeg')


@mcp.tool()
def frames(path: str, times: list[float]) -> Image:
    """Full-size frames (up to 6) at exact seconds, stamped with TC and frame number — check focus, eyes
    closed, framing, the first/last frame of a cut."""
    return Image(data=core.to_jpeg(core.frames_image(_idx(path), [float(t) for t in times])), format='jpeg')


# ───────────────────────────── deciding ─────────────────────────────
@mcp.tool()
def transcript(path: str, start: float = 0, end: float = None, words: bool = False) -> dict:
    """Transcript of a range: segments, or every word with start/end/confidence when words=True."""
    idx = _idx(path)
    end = idx['info']['duration'] if end is None else end
    if words:
        return {'words': [w for w in core.load_words(idx) if w['e'] > start and w['s'] < end]}
    return {'segments': [s for s in (idx.get('speech') or {}).get('segments', []) if s['e'] > start and s['s'] < end]}


@mcp.tool()
def find_phrase(phrase: str, paths: list[str], max_hits: int = 10) -> list:
    """Where is this line said? Fuzzy search over word-level transcripts of one or more clips; returns each
    hit with start/end, similarity and the clip, best first. Repeated takes show up as multiple hits."""
    import difflib
    q = [core.norm(w) for w in phrase.split() if core.norm(w)]
    hits = []
    for p in paths:
        idx = _idx(p)
        W = core.load_words(idx)
        toks = [core.norm(w['w']) for w in W]
        n = len(q)
        for i in range(0, max(1, len(W) - n + 1)):
            for L in (n - 1, n, n + 1):
                if L < 1 or i + L > len(W):
                    continue
                r = difflib.SequenceMatcher(None, q, toks[i:i + L], autojunk=False).ratio()
                if r >= 0.6:
                    hits.append({'path': idx['info']['nle_path'], 's': W[i]['s'], 'e': W[i + L - 1]['e'],
                                 'said': ' '.join(w['w'] for w in W[i:i + L]), 'similarity': round(r, 2)})
    hits.sort(key=lambda h: (-h['similarity'], h['s']))
    best = []
    for h in hits:  # drop overlapping duplicates
        if not any(b['path'] == h['path'] and b['s'] < h['e'] and h['s'] < b['e'] for b in best):
            best.append(h)
    return best[:max_hits]


@mcp.tool()
def takes(path: str, pause_gap: float = 0.7, prefer: str = 'last') -> dict:
    """Dialogue decupagem: splits speech into utterances at pauses ≥ pause_gap s, groups repeated attempts of the
    same line (incl. false starts) and ranks them by completeness, ASR confidence, fillers, focus, shake and
    — with prefer='last' — position (the last full take usually wins). Verify the winner with timeline_strip."""
    return core.takes_report(_idx(path), pause_gap, prefer)


@mcp.tool()
def scenes(folder: str = None, paths: list[str] = None, prefer: str = 'last') -> dict:
    """Cross-clip decupagem of a shoot: reads the slate called out loud ("cena 3, take 2, parte 3 ... ação ...
    corta"), groups clips by scene/part/shot, takes the performance between 'ação' and 'corta', flags aborted
    takes ("peraí", "de novo"), compares each take's lines with the consensus version and ranks them
    (prefer='last' favours later takes). mode tells you whether to judge by lines (dialogue), by performance
    (improvised) or by picture (no_dialogue). Clips without slate are listed apart (b-roll / candid / chatter).
    All clips must be indexed (index_folder first)."""
    files = [core.local_path(p) for p in (paths or [])]
    if folder:
        root = core.local_path(folder)
        files += [os.path.join(dp, f) for dp, _, fn in os.walk(root) for f in sorted(fn)
                  if os.path.splitext(f)[1].lower() in core.VIDEO_EXT and not f.startswith('._')]
    return core.scenes_report([_idx(f) for f in sorted(set(files))], prefer)


@mcp.tool()
def broll(path: str, min_len: float = 1.5) -> dict:
    """No-speech decupagem: splits the picture at hard cuts, then into runs of the same camera behaviour
    (static / pan_left / pan_right / tilt_up / tilt_down) that are in focus, exposed and not shaky. Returns usable
    windows (best first) and rejected stretches with the reason (soft/blown/dark/shaky/chaotic). Sharpness is
    relative to this clip's best focus (1.0 = sharpest 10%). Then LOOK at each window with contact_sheet."""
    return core.broll_windows(_idx(path), min_len)


@mcp.tool()
def quality(path: str, start: float, end: float) -> dict:
    """Measured picture quality for a range: relative sharpness, luma, clipped/crushed %, camera motion mix, shake."""
    return core.range_quality(_idx(path), start, end)


@mcp.tool()
def annotate(path: str, start: float, end: float, text: str, tags: list[str] = [], rating: int = None) -> list:
    """Write what you SAW into the clip's decupagem log (persists across sessions; exported as markers in XML).
    e.g. text='close das mãos amassando massa, luz lateral', tags=['broll','close','maos'], rating=4."""
    idx = _idx(path)
    log = core.annotate(idx, start, end, text, tags, rating)
    try:
        sidecar.write_clip_md(idx)
        sidecar.write_folder_md(os.path.dirname(idx['info']['path']))
    except Exception:
        pass
    return log


@mcp.tool()
def read_annotations(path: str) -> list:
    """The decupagem log of one clip."""
    return core.read_log(_idx(path))


@mcp.tool()
def snap_cut(path: str, start: float, end: float, mode: str = 'speech', tighten: bool = False,
             pad_in: float = 0.12, pad_out: float = 0.2) -> dict:
    """Turn approximate seconds into frame-accurate in/out (+ source TC). mode='speech': keeps your range but moves
    any boundary that would clip a word out of it, then onto the quietest audio frame nearby; tighten=True trims
    to the words instead (jump-cut talking heads). mode='picture': snaps to hard cuts so no flash frame leaks in."""
    return core.snap(_idx(path), start, end, pad_in, pad_out, mode, tighten)


# ───────────────────────────── handoff to Premiere / Resolve ─────────────────────────────
PLAN_DOC = """plan = {"name": "Seq name", "fps": optional "30000/1001", "clips": [
   {"path": "D:\\\\x\\\\A001.mov", "in": 12.3, "out": 18.9, "snap": "speech"|"picture"|null, "tighten": false, "label": "take 3"},
   {"path": "...", "in_frame": 100, "out_frame": 220} ]}   (out is exclusive)"""


def nle_cut_list(plan: dict) -> list:
    """Frame-accurate numbers for each planned clip, ready for the Resolve MCP (insert_to_timeline: start_frame=
    in_frame, end_frame=end_frame_inclusive, record_frame) or the Premiere MCP (insert/overwrite with in/out
    seconds). """
    return core.plan_for_nle(plan)


def export_timeline(plan: dict, out_path: str, format: str = 'fcp7xml') -> dict:
    """Write the cut as a timeline file. format='fcp7xml' (Premiere: do NOT import the .xml through the Premiere MCP (importFiles on an FCP XML crashes Premiere) — give the editor the path and ask them to import it (File › Import / Ctrl+I); Resolve: import_timeline_from_file)
    or 'edl' (CMX3600). Clip markers from annotate() are included in XML. Returns the path to import (as the NLE sees it). """
    out = core.local_path(out_path)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    return core.export_edl(plan, out) if format == 'edl' else core.export_fcp7(plan, out)


for _f in (nle_cut_list, export_timeline):
    _f.__doc__ += '\n' + PLAN_DOC
    mcp.tool()(_f)

# ───────────────────────────── montage: from logged footage to a reviewable cut ─────────────────────────────
import json as _json

_render_jobs = {}


@mcp.tool()
def music_map(path: str, start: float = 0.0, end: float = None) -> dict:
    """MUSIC FIRST. Map a song (or any audio, incl. a concert's board mix): tempo (with half/double-time
    alternatives), beat grid, bar downbeats, energy sections (low/mid/high), the drop, quiet bars. Use it before
    choosing shots — the song decides where cuts land. Full grid is cached for build_edit."""
    return montage.music_summary(montage.music_map(path, start, end))


@mcp.tool()
def music_windows(path: str, duration: float, n: int = 3) -> list:
    """Best stretches of a song for a cut of `duration` seconds: start and end on downbeats, prefer ending on a
    section change and holding the drop in the first 20–60 %. Offer these to the user before building."""
    return montage.music_windows(montage.music_map(path), duration, n)


@mcp.tool()
def sync_audio(video: str, audio: str, max_offset: float = 120.0) -> dict:
    """Offset between a camera clip and an external recorder file (audio_time = video_time + offset_sec). Use it
    for ext_audio on a shot. reliable=False means the two files don't share sound (e.g. a voice-over)."""
    return montage.sync_audio(video, audio, max_offset)


@mcp.tool()
def reframe_preview(path: str, times: list[float], aspect: str = '9:16', x: float = None, y: float = None,
                    zoom: float = 1.0) -> list:
    """See the crop for a vertical/square version: frames with the crop box drawn (outside dimmed). Without x/y it
    suggests the crop from where the in-focus subject is. Returns the image + the reframe_x/reframe_y/zoom to put on
    the shot."""
    img, meta = montage.crop_preview(_idx(path), [float(t) for t in times], aspect, x, y, zoom)
    return [Image(data=core.to_jpeg(img), format='jpeg'), _json.dumps(meta)]


@mcp.tool()
def save_brief(project: str, brief: dict) -> dict:
    """Store the answers of the intake (see the `montar` prompt) in <project>/brief.json so every version follows them."""
    p = montage.project_dir(project)
    _json.dump(brief, open(os.path.join(p, 'brief.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    return {'saved': core.nle_path(os.path.join(p, 'brief.json'))}


@mcp.tool()
def read_project(project: str) -> dict:
    """Brief + list of edit versions (with duration/warnings) + renders of a project folder."""
    p = montage.project_dir(project)
    bp = os.path.join(p, 'brief.json')
    vers = []
    for f in sorted(os.listdir(p)):
        if f.startswith('edit_v') and f.endswith('.json'):
            e = _json.load(open(os.path.join(p, f), encoding='utf-8'))
            vers.append({'file': f, 'duration': e.get('duration'), 'shots': len(e.get('shots', [])),
                         'broll': len(e.get('overlays', [])), 'warnings': e.get('warnings', [])})
    return {'brief': _json.load(open(bp, encoding='utf-8')) if os.path.exists(bp) else None, 'versions': vers,
            'renders': [core.nle_path(os.path.join(p, 'renders', f)) for f in sorted(os.listdir(os.path.join(p, 'renders')))]}


@mcp.tool()
async def sync_multicam(cameras: dict, ctx: Context, external_audio: list[str] = None, out_xml: str = None,
                        fps: str = None, correct_drift: bool = True, labels: dict = None) -> dict:
    """PluralEyes-style multicam sync from the clips' own audio — no timecode needed.
    cameras: {"CAM A": "E:\\\\Show\\\\01 HRCS", "CAM B": "E:\\\\Show\\\\02 BÊ"} (folder or list of files per camera).
    external_audio: recorder files/folders (board mix, lav recorders) — used as the reference clock when given.
    Places every clip on one timeline (graph of audio matches + transcript fallback), measures the recorder's clock
    drift on long clips and writes a corrected copy (<name>_drift_corrected±X.wav in claudio_drift_corrected/), then
    exports an FCP7 XML like PluralEyes' ("Synced Sequence": V1..Vn = cameras, stereo pairs per camera, recorder on the
    last pair; one bin per camera). Every camera gets its own Premiere label colour on its video, its audio and its bin clips (external audio too); labels={'CAM A': 'Mango', 'AUDIO': 'Caribbean'} overrides the default palette (Cerulean, Mango, Forest, Magenta, Yellow, Violet, Teal, Rose...; external audio Caribbean). Premiere: do NOT import the .xml through the Premiere MCP (importFiles on an FCP XML crashes Premiere) — give the editor the path and ask them to import it (File › Import / Ctrl+I); Resolve: import_timeline_from_file. Shows progress."""
    import asyncio, json as _j
    from claudio import multicam
    state = {'stage': 'audio', 'f': 0.0}
    def prog(stage, f):
        state.update(stage=stage, f=f)
    loop = asyncio.get_running_loop()
    task = loop.run_in_executor(None, lambda: multicam.sync_cameras(cameras, external_audio, fps, correct_drift, prog))
    while not task.done():
        msg = ('lendo áudio dos clipes' if state['stage'].startswith('audio') else 'alinhando') + f" {int(state['f'] * 100)}%" \
              + (f" · {state['stage'][6:]}" if state['stage'].startswith('audio ') else '')
        try:
            await ctx.report_progress(state['f'] * (60 if state['stage'].startswith('audio') else 40) + (0 if state['stage'].startswith('audio') else 60), 100, msg)
        except Exception:
            pass
        await asyncio.sleep(2)
    res = task.result()
    first = next(n for n in res['nodes'] if n.get('t0') is not None)
    base = os.path.dirname(os.path.commonpath([n['path'] for n in res['nodes']]))
    out = core.local_path(out_xml) if out_xml else os.path.join(base, 'claudio_sync.xml')
    x = multicam.export_xml(res, out, labels=labels)
    summ = multicam.summary(res)
    _j.dump({'fps': res['fps'], 'drift': res['drift'], 'clips': [{**r, 'path': n['path']} for r, n in
             zip(summ['clips'], sorted(res['nodes'], key=lambda n: (n['cam'], n.get('t0') if n.get('t0') is not None else 1e12)))]},
            open(os.path.splitext(out)[0] + '.json', 'w'), ensure_ascii=False, indent=1)
    return {'xml': x, 'sync_json': core.nle_path(os.path.splitext(out)[0] + '.json'), **summ}


@mcp.tool()
def multicam_monitor(sync_json: str, start: float, end: float, columns: int = 8) -> list:
    """LOOK at every synced angle side by side: one row per camera clip rolling in [start, end] (seconds of the synced
    timeline from sync_multicam), one column per moment. Choose the angle for each bar from this."""
    mc = __import__('json').load(open(core.local_path(sync_json)))
    rows = [{'clip': c['clip'], 'path': c['path'], 'nle_path': core.nle_path(c['path']), 'offset': c['start_s'],
             'master_in': c['start_s'], 'master_out': c['start_s'] + c['duration'], 'duration': c['duration'], 'reliable': True}
            for c in mc['clips'] if c.get('start_s') is not None and os.path.splitext(c['path'])[1].lower() in core.VIDEO_EXT]
    img, ts = montage.multicam_grid(rows, start, end, max(2, min(columns, 12)), 260)
    return [Image(data=core.to_jpeg(img), format='jpeg'), __import__('json').dumps({'columns_at_timeline_seconds': ts})]


@mcp.tool()
def read_premiere_project(prproj: str, sequence: str = None) -> dict:
    """Read the editor's own Premiere project (read-only) to learn how they cut. Without `sequence`: list sequences.
    With it: the timeline items (track, timeline in/out, source clip + in/out) and the editing patterns — spine shot
    length, b-roll over speech (count, length, % covered), caption block length, end card. Use these numbers as the
    house style when building a new edit for the same client/format."""
    from claudio import nle_read
    if not sequence:
        return {'sequences': nle_read.premiere_sequences(prproj)}
    return {'patterns': nle_read.edit_patterns(prproj, sequence), 'timeline': nle_read.premiere_timeline(prproj, sequence)[:300]}


# ───────────────────────────── ingest · selects · reading the NLE back ─────────────────────────────
@mcp.tool()
async def ingest_card(card: str, job_folder: str, ctx: Context, camera: str = None, index_after: bool = True,
                      vocabulary: str = None, dry_run: bool = False) -> dict:
    """Offload a memory card into the job folder, VERIFIED (xxHash64 while copying, destination read back and hashed
    again), in the usual layout: BRUTOS/<camera>/ (Sony XML sidecars travel with their clips), BRUTOS/00 AUDIO/,
    BRUTOS/03 FOTOS/, #projeto/, #trilhas/. Never overwrites (same name + other content → BRUTOS/<camera>/CARD_n/),
    never touches the card. Writes _ingest.md/_ingest.json. Then starts indexing (which writes the .md files).
    camera: folder name for this card ("CAM A", "01 HRCS"); default = camera model from the metadata.
    dry_run=True shows the plan without copying. Shows progress."""
    import asyncio
    from claudio import ingest
    st = {'f': 0.0}
    loop = asyncio.get_running_loop()
    task = loop.run_in_executor(None, lambda: ingest.ingest(card, job_folder, camera, lambda s, f: st.update(f=f), dry_run))
    while not task.done():
        try:
            await ctx.report_progress(st['f'] * 100, 100, f"copiando e verificando {int(st['f'] * 100)}%")
        except Exception:
            pass
        await asyncio.sleep(2)
    res = task.result()
    if not dry_run and index_after and res.get('video_files'):
        for f in res['video_files']:
            _enqueue(f, 'pt', vocabulary=vocabulary)
        res['indexing'] = f"{len(res['video_files'])} clipes na fila — index_status mostra o progresso"
    res.pop('video_files', None)
    return res


@mcp.tool()
def make_selects(out_xml: str, folder: str = None, paths: list[str] = None, include_single: str = 'auto') -> dict:
    """What an assistant editor hands over before cutting, for the editor to check in the NLE: SELECTS — falas (best
    take of every repeated line, slate/recording order, alternates in the marker), SELECTS — falas únicas (lines said
    once by whoever is on the mic; automatic only for interview-like material), SELECTS — b-roll (usable stretches of
    no-speech clips), and a bin of every source clip with markers (✅ best / ✗ criticised / alternates / 👁 notes /
    b-roll). Premiere: do NOT import the .xml through the Premiere MCP (importFiles on an FCP XML crashes Premiere) — give the editor the path and ask them to import it (File › Import / Ctrl+I); Resolve: import_timeline_from_file for each of resolve_xmls.
    Links point to the ORIGINAL media (the NLE handles proxies)."""
    from claudio import selects
    files = [core.local_path(p) for p in (paths or [])]
    if folder:
        root = core.local_path(folder)
        for dp, dn, fn in os.walk(root):
            dn[:] = [x for x in dn if x.lower() not in core.PROXY_DIRS and not x.startswith(('_claudio', '_decupa', '.'))]
            files += [os.path.join(dp, f) for f in sorted(fn) if os.path.splitext(f)[1].lower() in core.VIDEO_EXT
                      and not f.startswith('._') and not core.is_proxy(os.path.join(dp, f))]
    files = [f for f in sorted(set(files)) if _is_indexed(f)]
    inc = {'auto': 'auto', 'true': True, 'false': False}.get(str(include_single).lower(), 'auto')
    sel = selects.build_selects(files, include_single=inc)
    out = core.local_path(out_xml)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    return selects.export_selects_xml(sel, out)


async def _people(folder, ctx):
    import asyncio
    from claudio import people as P
    st = {'f': 0.0, 'clip': ''}
    loop = asyncio.get_running_loop()
    task = loop.run_in_executor(None, lambda: P.people(folder, progress=lambda f, c: st.update(f=f, clip=c)))
    while not task.done():
        try:
            await ctx.report_progress(st['f'] * 100, 100, f"rostos e vozes: {st['clip']}")
        except Exception:
            pass
        await asyncio.sleep(2)
    return task.result()


@mcp.tool()
async def people(folder: str, ctx: Context) -> list:
    """WHO is in the footage: every face and every voice of the folder's clips, clustered so the same person gets one id
    in all clips — P1, P2… (faces, by screen time) and V1, V2… (voices, by talk time) — with clips, times and sample
    quotes. Returns a board of the face clusters to LOOK at. Show it to the editor and ask who is who (use the names the
    overview found as hints), then save with name_people. Likeness, not identity: never assert a name the editor
    didn't confirm. The first run on a folder takes a few seconds per clip (cached afterwards)."""
    from claudio import people as P
    res = await _people(folder, ctx)
    img = P.board(res)
    out = [__import__('json').dumps(P.summary(res), ensure_ascii=False)]
    return ([Image(data=core.to_jpeg(img), format='jpeg')] if img else []) + out


@mcp.tool()
async def name_people(folder: str, names: dict, ctx: Context) -> dict:
    """Save who is who: {"P1": "Caio", "V2": "Caio", "P3": "Tata"} (ids from people). Names stick to the person (face or
    voice print), so they survive new clips and later runs, and show up in find_person and people."""
    from claudio import people as P
    res = await _people(folder, ctx)
    return {'named': P.name(folder, names, res)}


@mcp.tool()
async def find_person(folder: str, who: str, ctx: Context) -> dict:
    """Where a person appears (face: clips and times) and what they say (voice: utterances with times), by name saved
    with name_people or by id (P2 / V1). Use it for "only Caio's lines", "shots with Tata", a person's best take."""
    from claudio import people as P
    res = await _people(folder, ctx)
    w = who.strip().lower()
    out = {'who': who, 'faces': [], 'voices': []}
    for kind in ('faces', 'voices'):
        for r in res[kind]:
            if r['id'].lower() == w or (r['name'] or '').lower() == w:
                item = {'id': r['id'], 'name': r['name'], 'clips': r['clips']}
                if kind == 'voices':
                    item['lines'] = [{'clip': os.path.basename(f), 'nle_path': core.nle_path(f), 's': x['s'], 'e': x['e'], 'text': x['text']}
                                     for f, x in sorted(r['members'], key=lambda m: (m[0], m[1]['s']))]
                out[kind].append(item)
    if not out['faces'] and not out['voices']:
        out['hint'] = 'not found: run people and name_people first, or pass an id like P1 / V2'
    return out


@mcp.tool()
async def find_shot(query: str, ctx: Context, folder: str = None, paths: list[str] = None, top: int = 8) -> list:
    """Find shots by DESCRIBING them — "hands on the guitar neck", "crowd with phones up", "road at sunset", "a man
    talking to the camera". Write the query in ENGLISH (CLIP). Searches every clip of `folder` (or `paths`) and returns
    a contact sheet of the best moments (clip · time · score) to LOOK at, plus the windows (start/end/peak, seconds of
    the source). Scores compare candidates (~0.20 unrelated, ~0.28+ a good match). Clips indexed with index_folder are
    ready; others are embedded on first search (a few seconds per minute of footage). Confirm with frames/contact_sheet
    before cutting."""
    import asyncio
    from claudio import visual, indexer
    files = [core.local_path(p) for p in (paths or [])]
    if folder:
        files += indexer.list_videos(folder)
    if not files:
        return ['no clips: pass folder or paths']
    todo = [f for f in files if not visual.is_embedded(f)]
    loop = asyncio.get_running_loop()
    for k, f in enumerate(todo):
        try:
            await ctx.report_progress(k, len(todo), f'preparando a busca visual: {os.path.basename(f)} ({k + 1}/{len(todo)})')
        except Exception:
            pass
        await loop.run_in_executor(None, visual.embed_clip, f)
    hits = await loop.run_in_executor(None, lambda: visual.search(files, query, top=max(1, min(top, 16))))
    img = await loop.run_in_executor(None, visual.board, hits)
    out = [__import__('json').dumps({'query': query, 'clips_searched': len(files), 'hits': hits}, ensure_ascii=False)]
    return ([Image(data=core.to_jpeg(img), format='jpeg')] if img else []) + out


@mcp.tool()
def stereo_fix(nle_xml: str, normalize: bool = False, out_xml: str = None) -> dict:
    """Audio that plays on ONE side only (a lav or a single mic recorded on L or R) → plays on BOTH sides. Reads the
    editor's sequence (FCP7 XML), measures L and R of every audio clip over the part used, and where one side is
    (near) silent makes both tracks play the live channel (dual mono); two live channels (two mics) are kept.
    normalize=True also levels every clip with speech to the same dialogue loudness (−18 LUFS per clip, Audio Levels,
    max +12 dB; muted or keyframed clips untouched). Nested sequences are flattened. Writes a NEW sequence — hand the
    editor the XML path (panel import button)."""
    from claudio import stereo
    return stereo.stereo_fix(nle_xml, out_xml, normalize=normalize)


@mcp.tool()
def flatten_timeline(nle_xml: str, out_xml: str = None, name: str = None) -> dict:
    """Clips straight on the timeline instead of nested sequences. Reads an FCP7 XML of the editor's sequence (Premiere
    MCP export_as_fcp_xml) and writes a NEW sequence where every nested sequence is replaced by its own clips — each
    trimmed to the part that is used and placed where it plays, keeping speed, effects, audio levels, labels and
    links. The editor grades take by take under the adjustment layer without opening nests. Items with no media file
    (adjustment layers, generated slugs) can't round-trip through XML: they are listed in `dropped` — tell the editor
    to copy them from the old sequence onto the top track. Hand the editor the XML path (the panel shows the import
    button); never import XML through the Premiere MCP."""
    from claudio import nle_read
    src = core.local_path(nle_xml)
    out = core.local_path(out_xml) if out_xml else os.path.splitext(src)[0] + '_sem_aninhadas.xml'
    return nle_read.flatten_fcp7(src, out, name)


@mcp.tool()
def compare_with_nle(project: str, nle_xml: str, version: int = None) -> dict:
    """Before changing anything: read the timeline AS IT IS NOW in the NLE (export it first — Premiere:
    export_as_fcp_xml; Resolve: export_timeline(file, 'FCP_7_XML')) and compare with the version Cláudio delivered:
    per shot same / trimmed / moved / removed, clips the editor added, and the editor's markers (notes) with the shot
    each falls on. The editor's manual changes win — build the next version on top of them (adopt_nle_timeline)."""
    from claudio import nle_read
    p = montage.project_dir(project)
    v, fit = montage.load_fit(p, version)
    return {'compared_with_version': v, **nle_read.diff_with_edit(nle_read.read_fcp7(nle_xml), fit)}


@mcp.tool()
def adopt_nle_timeline(project: str, nle_xml: str, version: int = None) -> dict:
    """Make the editor's current NLE timeline the new base version, exactly as it is (positions, trims, removals, added
    clips; no beat re-fitting), keeping Cláudio's settings (format, music, captions). Apply the editor's notes on top
    of THIS version, then export_edit → import as a NEW sequence (never overwrite the editor's)."""
    from claudio import nle_read
    import json as _j
    p = montage.project_dir(project)
    v, fit = montage.load_fit(p, version)
    tl = nle_read.read_fcp7(nle_xml)
    new = montage.fit_edit(nle_read.edit_from_timeline(tl, fit))
    nv = montage.next_version(p)
    new['version'] = nv
    new['adopted_from'] = core.nle_path(core.local_path(nle_xml))
    _j.dump(new, open(os.path.join(p, f'edit_v{nv}.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    check = nle_read.diff_with_edit(tl, new)['summary']
    return {'version': nv, 'based_on_nle_timeline': tl['name'], 'fidelity_check': check, 'notes': tl['markers'],
            'timeline': montage.timeline_table(new)}


# ───────────────────────────── client profiles · QC ─────────────────────────────
@mcp.tool()
def client_profile(name: str) -> dict:
    """Read a client's profile (vocabulary, formats, end card, music, captions, structure, house style measured from
    finished edits, notes, past jobs). READ IT FIRST on a new job for a known client: it replaces most intake
    questions — ask only what is different this time. Returns null profile if the client is new."""
    from claudio import clients
    p = clients.load(name)
    return {'profile': p, 'folder': core.nle_path(clients.clients_dir()),
            'known_clients': sorted(os.path.splitext(f)[0] for f in os.listdir(clients.clients_dir()) if f.endswith('.md'))}


@mcp.tool()
def save_client_profile(name: str, updates: dict, learn_from_prproj: str = None) -> dict:
    """Create/update a client profile (<clients>/<client>.md — readable by people and agents). updates may hold:
    vocabulary (str), formats (list), end_card {path,in,out}, music {tracks,style}, captions, structure, notes,
    jobs [{date,name,folder}] (appended). learn_from_prproj: a finished Premiere project of this client — measures
    the house style (shot lengths, b-roll coverage, captions, end card) from its finished sequences."""
    from claudio import clients
    p = clients.save(name, updates)
    if learn_from_prproj:
        p = clients.learn_style(name, learn_from_prproj)
    return p


@mcp.tool()
def qc_timeline(nle_xml: str, platform: str = None, sequence_width: int = None, sequence_height: int = None) -> dict:
    """Check the timeline AS IT IS in the NLE before the editor exports (export it first — Premiere export_as_fcp_xml,
    Resolve export_timeline FCP_7_XML). Reports only: flash frames, black gaps, overlaps, offline media, disabled
    clips, clips past their media, same take twice, jump cuts, speech level jumps, clipped source audio, and
    size/duration vs platform (reels · tiktok · shorts · feed · youtube · youtube4k) — plus the AUDIO of the mix:
    loudness (LUFS) vs the platform target (−14 social/YouTube, −23 tv, −16 podcast), true peak, and music too loud
    under speech (`ducking`: where and how many dB to lower the music). Offer to drop each issue as a marker in the
    NLE; the editor decides what to fix."""
    from claudio import qc
    size = (sequence_width, sequence_height) if sequence_width and sequence_height else None
    r = qc.qc_timeline(nle_xml, platform, size)
    try:
        r['audio'] = qc.audio_qc(nle_xml, platform)
    except Exception as e:
        r['audio'] = {'error': str(e)}
    return r


# ───────────────────────────── project overview · motion-designer on top of the edit ─────────────────────────────
@mcp.tool()
def project_digest(folder: str) -> dict:
    """AFTER indexing the whole job: everything the logs know, condensed (totals, cameras, colour, minutes of speech,
    most frequent words, what is said / seen per clip). Read it, understand what the project IS, write the overview
    with save_project_overview, then ask the user the basics (approximate final duration; soundtrack — a specific
    track or a mood/genre) and propose 2–4 concrete directions grounded in the material. They pick one or explain their
    own idea, and the edit starts from there."""
    from claudio import sidecar as _s
    return _s.project_digest(folder)


@mcp.tool()
def save_project_overview(job_folder: str, markdown: str) -> dict:
    """Write <job>/_VISAO_GERAL.md — what the project is about, the material (cameras, hours, colour, sound, people,
    places, moments), what is strong / missing, the open questions (duration, soundtrack) and the proposed directions.
    Any agent reads it next time. Text outside the auto markers is kept."""
    from claudio import sidecar as _s
    return {'written': core.nle_path(_s.write_overview(job_folder, markdown))}


@mcp.tool()
def motion_reference(topic: str = None) -> str:
    """The motion-designer skill's craft rules (readability, safe zones, captions, narration, composition, fit…).
    Read the relevant section BEFORE designing an overlay or writing narration."""
    from claudio import motion
    return motion.reference(topic)


@mcp.tool()
async def render_overlay(code: str, name: str, out_folder: str, ctx: Context, aspect: str = '9x16', fps: str = '30000/1001',
                         codec: str = 'png') -> dict:
    """An overlay ASSET for the edit (lower third, title, animated callout, end card…) made with the motion-designer
    skill's core on a TRANSPARENT background → .mov with alpha (codec='png': PNG in MOV, seconds and small — the
    default; codec='prores': ProRes 4444, many minutes at 4K, only if the editor asks for ProRes), ready to import and lay on the top video
    track at the time it should appear. aspect: 9x16 · 16x9 · 1x1 · 4x5 or WxH (match the sequence). fps: the
    sequence rate. This is an asset for the timeline, not a render of the video.
    """
    import asyncio
    from claudio import motion
    st = {'f': 0.0}
    loop = asyncio.get_running_loop()
    task = loop.run_in_executor(None, lambda: motion.render_overlay(code, name, out_folder, aspect, fps, progress=lambda f: st.update(f=f), codec=codec))
    while not task.done():
        try:
            await ctx.report_progress(st['f'] * 100, 100, f'renderizando overlay {name} {int(st["f"] * 100)}%')
        except Exception:
            pass
        await asyncio.sleep(2)
    return task.result()


@mcp.tool()
def caption_sentences(nle_xml: str) -> dict:
    """STEP 1 of captions (always): the edit's speech as sentences {id, text, clip, remembered_correction}. Correct ONLY
    grammar — agreement (concordância), regência, crase, punctuation — keeping the speaker's words, meaning, register
    and brand names (e.g. "Você sabe quantos já gastou" → "Você sabe quanto já gastou"). Show the user the diff
    (spoken → caption) and pass the approved {id: text} as `corrected` to the caption tools. Approved corrections are
    remembered per source clip and come back by themselves next time."""
    from claudio import motion
    return motion.caption_sentences(nle_xml)


@mcp.tool()
def caption_style_preview(nle_xml: str, aspect: str = '9x16', accent: str = '#4ea1ff', corrected: dict = None,
                          fix: dict = None, client: str = None) -> list:
    """STEP 2 of captions: the same moment of the edit (real footage) with each caption style on it — social, bold,
    clean, karaoke, box, minimal — and the recommended one starred (from the client profile's caption preference, else
    the format: vertical → social, horizontal → minimal). Show it and let the user choose (or tweak accent/position)."""
    from claudio import motion, clients
    pref = None
    if client:
        p = clients.load(client)
        pref = (p or {}).get('captions')
    img, meta = motion.caption_style_preview(nle_xml, aspect, accent, corrected, fix, pref)
    return [Image(data=core.to_jpeg(img), format='jpeg'), __import__('json').dumps(meta, ensure_ascii=False)]


@mcp.tool()
async def caption_overlay(nle_xml: str, out_folder: str, ctx: Context, style: str = 'social', aspect: str = '9x16',
                          accent: str = '#4ea1ff', corrected: dict = None, uppercase: bool = None, max_words: int = None,
                          position: str = 'bottom', fix: dict = None, codec: str = 'png') -> dict:
    """STEP 3 of captions: render the chosen style over the whole edit as it is NOW (export the sequence as FCP7 XML
    first), with the approved grammar corrections (`corrected` from caption_sentences, timings kept word by word) →
    one transparent .mov the length of the edit (PNG in MOV; codec='prores' for ProRes 4444), to lay on the top video
    track at 0.
    style: social · bold · clean · karaoke · box · minimal. position: bottom · center · top. fix: spelling {"Enki": "INC"}."""
    import asyncio
    from claudio import motion
    st = {'f': 0.0}
    loop = asyncio.get_running_loop()
    task = loop.run_in_executor(None, lambda: motion.caption_overlay(nle_xml, out_folder, aspect, accent, uppercase, max_words, fix,
                                                                     progress=lambda f: st.update(f=f), style=style,
                                                                     corrected=corrected, position=position, codec=codec))
    while not task.done():
        try:
            await ctx.report_progress(st['f'] * 100, 100, f'renderizando legendas ({style}) {int(st["f"] * 100)}%')
        except Exception:
            pass
        await asyncio.sleep(2)
    return task.result()


@mcp.tool()
def captions_srt(nle_xml: str, out_srt: str, corrected: dict = None, max_words: int = 4, max_chars: int = 24, fix: dict = None) -> dict:
    """Editable captions (.srt) of the edit with the approved grammar corrections: Premiere — import as a caption track;
    Resolve — import subtitle. Same chunking as the styled overlay."""
    from claudio import motion
    return motion.captions_srt(nle_xml, out_srt, max_words, max_chars, fix, corrected)


@mcp.tool()
def narration(text: str, out_folder: str, engine: str = 'elevenlabs', voice: str = None, budget: int = None) -> dict:
    """Narration for the edit with the motion-designer skill: engine='elevenlabs' (one continuous take, word timings,
    cached; voice = ElevenLabs voice_id; hard credit budget, default = text length + 10 % — tell the user the cost
    BEFORE running) or engine='edge' (free edge-tts pt-BR/en voices). Returns narration.wav (+ words.json) to import
    on an audio track. Follow motion_reference('Narration') for how to write the script."""
    from claudio import motion
    return motion.narration(text, out_folder, engine, voice, budget)


EDIT_DOC = """edit = {
  "name": "INC — Guilherme v1", "aspect": "9:16" | "16:9" | "1:1" | "4:5", "fps": optional "30000/1001",
  "music": {"path": "D:\\x\\song.wav", "start": 63.32 (a downbeat, from music_windows), "gain_db": -4, "duck_db": -14,
            "fade_out": 1.5},
  "cut_on": "beat" | "downbeat" | "half" | "none",   "pace": "calm" | "medium" | "fast" | "hype",
  "pace_follows_music": true (faster in high-energy sections, slower in low),
  "shots": [                                   # V1 spine, in order
    {"path": "...C0025.MP4", "in": 9.2, "out": 22.3, "audio": "dialogue", "snap": "speech", "label": "fala 1",
     "reframe_x": 0.42},                       # dialogue keeps its length; tail stretched to the next beat if the source allows
    {"path": "...C0031.MP4", "in": 3.0, "out": 9.0, "audio": "ambient", "beats": 4},   # or "len": seconds
    {"path": "...ASSINATURA.mp4", "in": 5.37, "out": 10.78, "audio": "mute", "label": "end card"}],
  multicam shot: {"path": "...C0015.MP4", "offset": <multicam_sync offset>, "beats": 8, "reframe_x": "auto", "grade": "auto"}
  "overlays": [                                # V2 b-roll runs over the spine (speech keeps playing)
    {"from": 5.2, "to": 13.0, "clips": [{"path": "...FACHADA.mp4", "in": 0, "out": 6}, {"path": "...", "in": 2, "out": 7}]}],
  "captions": {"enabled": true, "max_words": 4, "uppercase": true, "fix": {"Enki": "INC"}},
  "texts": [{"text": "Guilherme\nCorretor INC", "at": 0.5, "dur": 2.5, "style": "lower" | "title"}],
  "ambient_db": -16, "fade_in": 0.3, "fade_out": 0.8 }
Optional per shot/clip: "anchor": "start"|"center"|"end", "zoom", "reframe_y", "gain_db",
"ext_audio": {"path": "...wav", "offset": <sync_audio offset_sec>}."""


def build_edit(project: str, edit: dict) -> dict:
    """Fit an edit to the beat grid and save it as the next version (<project>/edit_vN.json). Returns the timeline
    (V1 spine + V2 b-roll with timeline/source times and length in beats) and warnings. Then edit_storyboard to look
    at it, and export_edit to put it in Premiere/Resolve for the user to review."""
    p = montage.project_dir(project)
    fit = montage.fit_edit(edit)
    v = montage.next_version(p)
    fit['version'] = v
    _json.dump(fit, open(os.path.join(p, f'edit_v{v}.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    return {'version': v, 'duration': fit['duration'], 'bpm': fit.get('bpm'), 'warnings': fit['warnings'],
            'timeline': montage.timeline_table(fit)}


def _render_worker(key, p, v, quality):
    job = _render_jobs[key]
    try:
        _, fit = montage.load_fit(p, v)
        out = os.path.join(p, 'renders', f'edit_v{v}_{quality}.mp4')
        job['state'] = 'rendering'
        res = montage.render_edit(fit, out, quality)
        sb = montage.storyboard(fit, out)
        sbp = os.path.join(p, 'renders', f'edit_v{v}_storyboard.jpg')
        open(sbp, 'wb').write(core.to_jpeg(sb, max_side=3000))
        job.update(state='done', result={**res, 'storyboard': core.nle_path(sbp)}, storyboard=sbp)
    except Exception as e:
        job.update(state='error', error=str(e)[-1500:])


@mcp.tool()
def render_edit(project: str, authorized_by_user: bool = False, version: int = None, quality: str = 'preview',
                wait_seconds: int = 50) -> list:
    """ONLY WHEN THE USER EXPLICITLY ASKED FOR A RENDER (set authorized_by_user=True only then). The normal delivery is
    the timeline in Premiere/Resolve (export_edit), where the user grades, adds effects and exports.
    Renders an edit version to MP4: spine + b-roll (reframed), music ducked under speech, captions/titles, fades,
    −14 LUFS. quality='preview' (small) or 'final' (full res). Returns the path + a storyboard of the rendered file."""
    if not authorized_by_user:
        return ['{"refused": "render só com autorização explícita do usuário — entregue a timeline com export_edit e '
                'pergunte antes de renderizar"}']
    p = montage.project_dir(project)
    v, _ = montage.load_fit(p, version)
    key = f'{p}|{v}|{quality}'
    j = _render_jobs.get(key)
    if not j or j['state'] in ('done', 'error') and not j.get('seen_pending'):
        if not j or j['state'] == 'error':
            _render_jobs[key] = j = {'state': 'queued'}
            threading.Thread(target=_render_worker, args=(key, p, v, quality), daemon=True).start()
    import time
    t0 = time.time()
    while time.time() - t0 < wait_seconds and j['state'] in ('queued', 'rendering'):
        time.sleep(0.5)
    if j['state'] == 'done':
        out = [_json.dumps(j['result'])]
        out.append(Image(data=open(j['storyboard'], 'rb').read(), format='jpeg'))
        _render_jobs.pop(key, None)          # next call re-renders (after edits)
        return out
    return [_json.dumps({k: v_ for k, v_ in j.items() if k != 'storyboard'})]


@mcp.tool()
def edit_storyboard(project: str, version: int = None) -> list:
    """Review a cut WITHOUT rendering: one tile per shot and b-roll from the SOURCE frames (rotation + crop applied) and
    a timeline bar (beats, music sections, b-roll layer). LOOK at it after every build_edit: back-to-back same angle,
    soft shots, crops cutting heads, b-roll that doesn't match what is said. Log footage looks flat here — expected."""
    p = montage.project_dir(project)
    v, fit = montage.load_fit(p, version)
    return [Image(data=core.to_jpeg(montage.source_storyboard(fit), max_side=3000), format='jpeg'),
            __import__('json').dumps({'version': v, 'duration': fit['duration'], 'warnings': fit['warnings']})]


def export_edit(project: str, out_path: str = None, version: int = None) -> dict:
    """Export an edit version as FCP7 XML for finishing: V1 spine, V2 b-roll (with Basic Motion scale/centre for the
    reframe), A1/A2 production audio with per-shot levels, A3/A4 music. Premiere: do NOT import the .xml through the Premiere MCP (importFiles on an FCP XML crashes Premiere) — give the editor the path and ask them to import it (File › Import / Ctrl+I);
    Resolve: import_timeline_from_file(xml). Captions/titles are not in the XML (re-create them in the NLE or burn
    from the render)."""
    p = montage.project_dir(project)
    v, fit = montage.load_fit(p, version)
    out = core.local_path(out_path) if out_path else os.path.join(p, f'edit_v{v}.xml')
    return montage.export_edit_fcp7(fit, out)


for _f in (build_edit, export_edit):
    _f.__doc__ += '\n' + EDIT_DOC
    mcp.tool()(_f)


@mcp.prompt()
def montar(pasta: str = '', pedido: str = '') -> str:
    """Montar uma timeline no Premiere/Resolve a partir do material bruto: entrevista guiada → trilha primeiro →
    decupagem com imagens → corte no beat → storyboard → timeline no NLE pra você revisar e finalizar."""
    return MONTAR.format(pasta=pasta or '(ask the user for the footage folder)', pedido=pedido or '(none yet)')


MONTAR = """You are assembling a real edit with the claudio MCP (Cláudio, o Editor), TOGETHER with the Premiere Pro / DaVinci Resolve MCP.
Footage: {pasta}. User request: {pedido}.

Your deliverable is a TIMELINE inside Premiere/Resolve that the user reviews and adjusts with you; they finish it
themselves (colour/LUT, effects, export). Never render a video unless the user explicitly asks for it.
Decide from evidence (transcripts, measured quality, images you LOOK at), never from file names.
Ask in the user's language, ONE step at a time, with AskUserQuestion (max 4 questions per call, best option first
marked "(Recomendado)" with a one-line reason). Skip what the request already answers.

0. Start right away: index_folder(<footage>, vocabulary=<brand/people/places if known>) — it shows progress and writes a
   <clip>.md next to every video. If the folder already has _CLAUDIO.md / <clip>.md files, READ them first: the footage
   is already logged. Several cameras of one event (+ recorder audio)? → sync_multicam first.
1. Client: client_profile(<client>) — if it exists, it answers most of the questions below (vocabulary, format, end
   card, music, captions, structure, house style): confirm it in ONE question ("igual da última vez?") and ask only
   what changed. New client with a finished project → save_client_profile(..., learn_from_prproj=...).
2. TYPE: "Depoimento / entrevista com b-roll" · "Aftermovie / resumo de evento" · "Beat edit / hype sem fala" ·
   "Show / clipe musical multicâmera" · "Cena / curta narrativo".
3. IDEA (plain message): what it is about, the one message, must-have moments/people/lines, who watches.
   "Você decide" → finish the logging first, then propose 3 ideas grounded in what the footage holds.
4. SETTINGS (one call): Format (9:16 · 16:9 · 4:5 · 1:1) · Duration (15 · 30 · 60 · 90+ s) · Captions (yes, social
   style · discreet · no — captions may be better made in the NLE) · Which NLE (Premiere · Resolve).
5. MUSIC FIRST: user's track · one from the folder · a licensed track from Storyblocks · no music (direct sound / the
   show itself). For Storyblocks (when its connector is available): search_audio by the mood/genre/BPM the editor gave,
   show 3–5 options (title, BPM, duration, link), and only after they pick one get_download_url — they download it into
   the job's music folder; never claim a track is licensed for a use you didn't check. music_map → one line:
   BPM (+ half-time reading), sections, drop, silences. music_windows(duration) → offer the 2–3 best stretches.
6. PACE & SOUND (one call): pace (follow the music · calm · medium · fast) · cuts on beat or bar · original sound
   (speech in front, music ducked · low ambience · music only) · opening/closing (hook in 3 s, end card file, CTA).
6a. OVERVIEW (after everything is indexed): project_digest → understand what the project is → save_project_overview
   (<job>/_VISAO_GERAL.md) → tell the user in 4–6 lines → ask: approximate final duration; soundtrack (a specific
   track, or a mood/genre — with a mood and no track, offer Storyblocks options as in step 5) → propose 2–4 concrete
   directions grounded in the material; they pick one or explain theirs.
6b. SELECTS first (the editor checks your logging in the NLE before any cut): make_selects → import → ask if the
   choices of takes / b-roll look right.
7. LOGGING with your eyes: scenes / takes (best take — usually the last complete one; a take the speaker criticised is
   out) · broll · find_phrase · find_shot ("hands on the guitar") · contact_sheet on every candidate · timeline_strip for tight speech cuts ·
   reframe_preview + set_rotation for vertical / sideways cameras · annotate what you saw (it goes into the .md).
   Colour: footage in log (S-Log, C-Log, BMD Film…) looks flat and washed out — that is NOT a defect, never reject or
   "fix" it; the user applies LUT/grade in the NLE. Judge focus, framing, action, expression instead.
8. build_edit(project, edit) → fix every warning → edit_storyboard → LOOK: back-to-back same angle, soft shots, crops
   cutting heads, b-roll that does not match the words, dead air. Fix and rebuild.
9. Put it in the NLE as ONE sequence with the clips straight on its tracks — never nest sequences (the editor grades
   take by take; to join parts, build one edit or run flatten_timeline on the joined sequence): export_edit → Premiere: hand the editor the .xml path to import (Ctrl+I) — never import_media on an .xml, it crashes Premiere · Resolve: import_timeline_from_file(xml) (or
   nle_cut_list + insert tools). Tell the user in 3–5 lines what you built and why, and ask for notes.
10. Each round of notes: export the CURRENT timeline from the NLE → compare_with_nle (what the editor changed + their
    marker notes) → adopt_nle_timeline (their changes become the base) → apply the notes → export_edit → import as a
    NEW sequence. Never overwrite the editor's sequence; never undo their manual changes.
10b. Overlays / captions / narration when the piece needs them (motion-designer skill): motion_reference first;
    captions ALWAYS in 3 steps: caption_sentences → fix grammar only (concordância, regência, crase, pontuação; keep
    the speaker's words) and show the diff → caption_style_preview (user picks the style; recommended starred) →
    captions_srt (editable) and/or caption_overlay(style, corrected); render_overlay for lower thirds / titles / end cards;
    narration (ElevenLabs — say the cost first — or edge). Save into <job>/#projeto/claudio_overlays and import them on
    a NEW empty video track above every track that has clips (create it); never overwrite or insert onto a track that already has anything (adjustment layers, titles, B-roll) — list the tracks with the Premiere MCP first (audio: a new audio track), at the right time. These are ASSETS for the timeline, not a final render.
11. Before they finalise: qc_timeline(current XML, platform) → offer the issues as markers; they decide.
12. Render (render_edit with authorized_by_user=True) ONLY if the user asks for a rendered file. At the end,
    save_client_profile(jobs=[...], plus anything learned) so the next job starts from here.
"""


def main():
    mcp.run()


if __name__ == '__main__':
    main()
