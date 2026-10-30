"""Cláudio, o Editor (MCP server "claudio") — lets Claude actually SEE and HEAR footage before cutting in Premiere / Resolve.

Run:  python3 server.py            (stdio MCP)
Tools, in the order an edit uses them:
  1. index_media / index_folder   → probe, transcript (word timestamps), cuts, focus/exposure/motion
  2. overview                     → what is in the clip (speech? b-roll? shots, takes, usable windows)
  3. contact_sheet / timeline_strip / frames  → LOOK at the picture (and words + waveform) at exact times
  4. takes (dialogue) · broll (no speech) · find_phrase · find_shot (describe a shot) · people / find_person → evidence-ranked candidates
  5. annotate                     → write the logging (what you saw) so it persists
  6. snap_cut → export_timeline / nle_cut_list   → frame-accurate handoff to the Premiere / Resolve MCPs
  7. build_edit → edit_storyboard → export_edit    → a reviewable timeline in Premiere / Resolve (no render)
  Multicam: sync_multicam (PluralEyes-style, from audio) → XML. Every indexed clip gets a <clip>.md next to it.
Kind of project (depoimento, recap, beat edit, show multicam, curta…): recipe() — the steps and taste rules are there.
Rules for every tool:
  - A tool that writes a sequence writes a NEW one, never the editor's.
  - Overlays, captions, music: a NEW track above every used one, never onto a track that has anything.
  - Log footage (S-Log/C-Log/BMD) looks flat on purpose — the editor grades it. Render only when asked.
"""
import os, queue, sys, threading, traceback

from mcp.server.fastmcp import FastMCP, Image
from claudio import core, montage

def _folders_line():
    from claudio import profile
    f = profile.get('pastas')
    return ('Job folders (this editor\'s profile, relative to the job): footage ' + f['brutos'] + '/<camera>/ · recorders '
            + f['audio'] + ' · photos ' + f['fotos'] + ' · NLE project/XMLs ' + f['projeto'] + ' · music ' + f['trilhas']
            + ' · captions ' + f['legendas'] + ' · overlays ' + f['overlays'] + '. Use these names, never others.')


def _nle_lines():
    from claudio import targets
    return '\n'.join('  - ' + r for r in targets.rules(targets.active()))


mcp = FastMCP('claudio', instructions=__doc__ + _nle_lines() + '\n' + _folders_line())

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
            idx = core.build_index(path, lang, lambda stage, f: job.update(stage=stage, progress=round(min(f, 1), 3)), force, vocab,
                                   core.stt_engine())
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
    """Analyse one video (Windows or Linux path): probe, camera + colour space (log vs Rec.709), audio, picture (cuts,
    sharpness, motion), word-timed transcript; writes <clip>.md next to it. Returns when done, or state=running after
    wait_seconds (then index_status)."""
    p = _enqueue(path, language, force, vocabulary) if force or vocabulary or not _is_indexed(path) or core.needs_stt(path) else core.local_path(path)
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
    """Clip shot with the camera on its side and no rotation metadata (people sideways on the contact sheet): set 90
    (clockwise) / 180 / 270. Applies to sheets, crops, renders, XML and the clip's .md."""
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
    """Index every video and audio file of a folder, in a background process that outlives this chat; skips clips already
    indexed; writes <clip>.md + contact sheet per clip and _CLAUDIO.md with the progress. Shows progress up to
    wait_minutes, then index_status. vocabulary: names the transcript must get right (brand, people, places); spellings
    fixed in captions are learned per folder and come back from caption tools as learned_vocabulary — offer
    save_client_profile. Proxies are used when present; links point to the originals."""
    import asyncio, subprocess as sp
    from claudio import indexer
    root = core.local_path(folder)
    files = indexer.list_media(root)
    from claudio import motion
    new_terms = motion.unapplied_vocabulary(root)  # spellings the editor fixed in captions, not yet applied here
    if new_terms:
        have = {x.strip().lower() for x in (vocabulary or '').split(',') if x.strip()}
        extra = [t for t in motion.folder_vocabulary(root) if t.lower() not in have]
        vocabulary = ', '.join(([vocabulary] if vocabulary else []) + extra)   # re-applied to stored words: no re-transcription
        motion.mark_vocabulary_applied(root)
    pending = [f for f in files if not _is_indexed(f) or vocabulary or core.needs_stt(f)]
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
    """Indexing progress (%, clips done/total, current step, minutes left) of a folder or every recent job. Tell the user
    the `progress` line."""
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
    """(Re)write <clip>.md + contact sheets for every indexed video/audio in a folder, and the folder's _CLAUDIO.md."""
    root = core.local_path(folder)
    out = []
    for dp, dn, fn in os.walk(root):
        dn[:] = [x for x in dn if x.lower() not in core.PROXY_DIRS and not x.startswith(('_claudio', '_decupa', '.'))]
        for f in sorted(fn):
            p = os.path.join(dp, f)
            if os.path.splitext(f)[1].lower() in core.MEDIA_EXT and _is_indexed(p):
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
            if os.path.splitext(f)[1].lower() in core.MEDIA_EXT and _is_indexed(p):
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
    """Cross-clip logging of a shoot with spoken slates ("cena 3, take 2 … ação … corta"): groups clips by scene/part/shot,
    keeps the performance between 'ação' and 'corta', flags aborted takes ("peraí", "de novo"), compares each take's lines
    with the consensus and ranks them (prefer='last' favours later takes). mode: judge by lines (dialogue), performance
    (improvised) or picture (no_dialogue). Clips without slate are listed apart. Index first."""
    files = [core.local_path(p) for p in (paths or [])]
    if folder:
        root = core.local_path(folder)
        files += [os.path.join(dp, f) for dp, _, fn in os.walk(root) for f in sorted(fn)
                  if os.path.splitext(f)[1].lower() in core.VIDEO_EXT and not f.startswith('._')]
    return core.scenes_report([_idx(f) for f in sorted(set(files))], prefer)


@mcp.tool()
def broll(path: str, min_len: float = 1.5) -> dict:
    """No-speech logging: the picture split at hard cuts, then into runs of one camera behaviour (static / pan / tilt) that
    are in focus, exposed and steady. Returns usable windows (best first) and rejected stretches with the reason
    (soft/blown/dark/shaky/chaotic). Sharpness is relative to the clip's sharpest 10 % (1.0)."""
    return core.broll_windows(_idx(path), min_len)


@mcp.tool()
def quality(path: str, start: float, end: float) -> dict:
    """Measured picture quality for a range: relative sharpness, luma, clipped/crushed %, camera motion mix, shake."""
    return core.range_quality(_idx(path), start, end)


@mcp.tool()
def annotate(path: str, start: float, end: float, text: str, tags: list[str] = [], rating: int = None, replace: bool = False) -> list:
    """Write what you SAW into the clip's log (persists across sessions and re-indexing, exported as XML markers, the .md
    is regenerated). e.g. text='close das mãos amassando massa', tags=['broll','close'], rating=4. replace=True first
    removes the lines overlapping [start, end] — use it to correct. Names across many clips: relabel_logs."""
    idx = _idx(path)
    log = core.annotate(idx, start, end, text, tags, rating, replace=replace)
    try:
        sidecar.write_clip_md(idx)
        sidecar.write_folder_md(os.path.dirname(idx['info']['path']))
    except Exception:
        pass
    return log


@mcp.tool()
def relabel_logs(mapping: dict, folder: str = None, paths: list[str] = None) -> dict:
    """Replace phrases in the logs of every clip of a folder (texts and tags, case-insensitive), e.g. the confirmed names:
    {"violonista de jaqueta vermelha": "Rayo"}. Longest first; .md files regenerated; no model calls."""
    from claudio import indexer
    files = [core.local_path(p) for p in (paths or [])] or indexer.list_videos(folder)
    changed = {}
    for f in files:
        try:
            idx = core.build_index(f)
        except Exception:
            continue
        n = core.relabel_log(idx, mapping)
        if n:
            changed[os.path.basename(f)] = n
            try:
                sidecar.write_clip_md(idx)
            except Exception:
                pass
    for d in {os.path.dirname(f) for f in files}:
        try:
            sidecar.write_folder_md(d)
        except Exception:
            pass
    return {'clips_changed': len(changed), 'lines_changed': sum(changed.values()), 'by_clip': changed}


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
    """Frame-accurate numbers for each planned clip, for the Resolve MCP (insert_to_timeline: start_frame=in_frame,
    end_frame=end_frame_inclusive, record_frame) or the Premiere MCP (insert/overwrite with in/out seconds)."""
    return core.plan_for_nle(plan)


def export_timeline(plan: dict, out_path: str, format: str = 'fcp7xml') -> dict:
    """Write a cut plan (the `plan` of nle_cut_list) as a timeline file: format 'fcp7xml' (Premiere/Resolve; annotate
    markers included) or 'edl' (CMX3600). Returns the path as the NLE sees it."""
    out = core.local_path(out_path)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    return core.export_edl(plan, out) if format == 'edl' else core.export_fcp7(plan, out)


nle_cut_list.__doc__ += '\n' + PLAN_DOC
for _f in (nle_cut_list, export_timeline):
    mcp.tool()(_f)

# ───────────────────────────── montage: from logged footage to a reviewable cut ─────────────────────────────
import json as _json

_render_jobs = {}


@mcp.tool()
def music_map(path: str, start: float = 0.0, end: float = None) -> dict:
    """Map a song (or any audio, e.g. a concert's board mix): tempo (with half/double-time readings), beat grid, bar
    downbeats, energy sections, the drop, quiet bars. Cached for build_edit."""
    return montage.music_summary(montage.music_map(path, start, end))


@mcp.tool()
def music_windows(path: str, duration: float, n: int = 3) -> list:
    """Best stretches of a song for a cut of `duration` s: start and end on downbeats, ending on a section change, the drop
    in the first 20–60 %."""
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
    """Store the answers of the intake (the recipe's brief) in <project>/brief.json so every version follows them —
    with "receita": <nome> so a later conversation reloads the same recipe."""
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
                        fps: str = None, correct_drift: bool = True, labels: dict = None, music: bool = False) -> dict:
    """PluralEyes-style sync from the clips' own audio, no timecode. cameras: {"CAM A": folder or [files], …};
    external_audio: recorder files/folders (board mix, lavs) — the reference clock when given. Measures and corrects the
    recorder's drift (copy in claudio_drift_corrected/) and exports an FCP7 "Synced Sequence": V1..Vn = cameras, recorder
    on the last audio pair, one bin per camera, each camera in its own label colour (labels={'CAM A': 'Mango', 'AUDIO':
    'Caribbean'} overrides). music=True (music video): external_audio is the guide track, takes are aligned to it and
    stacked; a take that fits two places (a repeated chorus) comes with also_fits_at_s — ask the editor. Shows progress."""
    import asyncio, json as _j
    from claudio import multicam
    state = {'stage': 'audio', 'f': 0.0}
    def prog(stage, f):
        state.update(stage=stage, f=f)
    loop = asyncio.get_running_loop()
    task = loop.run_in_executor(None, lambda: multicam.sync_cameras(cameras, external_audio, fps, correct_drift, prog, music=music))
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
    """Read the editor's Premiere project (read-only). Without `sequence`: the sequences. With it: the timeline items and
    the editing patterns (spine shot length, b-roll over speech, caption block length, end card) — the house style for a
    new edit of the same client/format."""
    from claudio import nle_read
    if not sequence:
        return {'sequences': nle_read.premiere_sequences(prproj)}
    return {'patterns': nle_read.edit_patterns(prproj, sequence), 'timeline': nle_read.premiere_timeline(prproj, sequence)[:300]}


# ───────────────────────────── ingest · selects · reading the NLE back ─────────────────────────────
@mcp.tool()
async def ingest_card(card: str, job_folder: str, ctx: Context, camera: str = None, index_after: bool = True,
                      vocabulary: str = None, dry_run: bool = False) -> dict:
    """Offload a memory card into the job's folders (profile), VERIFIED: xxHash64 while copying, destination read back and
    hashed again; Sony XML sidecars travel with their clips. Never overwrites (same name, other content → <camera>/CARD_n/),
    never touches the card; writes _ingest.md/.json, then starts indexing. camera: folder name for this card (default:
    the camera model). dry_run=True shows the plan. Shows progress."""
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
    """Selects for the editor to check before cutting (FCP7 XML): SELECTS — falas (best take of each repeated line,
    alternates in the marker), SELECTS — falas únicas (lines said once; interview-like material), SELECTS — b-roll
    (usable no-speech stretches), and a bin of every source clip with markers (✅ best / ✗ criticised / alternates /
    👁 notes). Resolve: import each of resolve_xmls. Links point to the original media."""
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
    """WHO is in the footage: faces (P1, P2… by screen time) and voices (V1, V2… by talk time) clustered across the
    folder's clips, with clips, times and sample quotes, plus a board of the faces to LOOK at. suggested_name comes from
    the spoken slate ("vídeo Robson, take 1"): show it, let the editor confirm, then name_people. Likeness, not identity:
    never assert a name the editor didn't confirm. First run: a few seconds per clip (cached)."""
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
    """Find shots by DESCRIBING them in ENGLISH (CLIP): "hands on the guitar neck", "crowd with phones up". Searches every
    clip of `folder` (or `paths`); returns a contact sheet of the best moments (clip · time · score) and the windows
    (start/end/peak, source seconds). Scores compare candidates (~0.20 unrelated, ~0.28+ good). Unindexed clips are
    embedded on first search. Confirm with frames before cutting."""
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
def describe_shots(folder: str = None, paths: list[str] = None) -> dict:
    """First-pass description of every stretch of the clips WITHOUT model calls: framing, place, objects, day/night (CLIP),
    who appears (confirmed names) and what is said, written into each clip's log tagged 'auto' (your annotate entries
    are kept). Look and annotate only where it matters."""
    from claudio import describe, indexer
    files = [core.local_path(p) for p in (paths or [])] or indexer.list_videos(folder)
    res = describe.describe(files)
    return {'clips': len(res), 'stretches': sum(r.get('stretches', 0) for r in res), 'results': res[:30]}


@mcp.tool()
def stereo_fix(nle_xml: str, normalize: bool = False, out_xml: str = None) -> dict:
    """One-sided audio (a lav on L or R) → both sides: measures L/R of every audio clip over the part used; a (near)
    silent side plays the live channel (dual mono), two live mics stay. normalize=True also levels speech clips to −18
    LUFS (max +12 dB; muted or keyframed clips untouched). Nested sequences are flattened."""
    from claudio import stereo
    return stereo.stereo_fix(nle_xml, out_xml, normalize=normalize)


@mcp.tool()
def flatten_timeline(nle_xml: str, out_xml: str = None, name: str = None) -> dict:
    """Nested sequences → their clips straight on the tracks, trimmed to the part used and placed where they play, keeping
    speed, effects, audio levels, labels and links. Items without media (adjustment layers, slugs) can't round-trip
    through XML: they are listed in `dropped` — tell the editor to copy them onto the top track."""
    from claudio import nle_read
    src = core.local_path(nle_xml)
    out = core.local_path(out_xml) if out_xml else os.path.splitext(src)[0] + '_sem_aninhadas.xml'
    return nle_read.flatten_fcp7(src, out, name)


@mcp.tool()
def compare_with_nle(project: str, nle_xml: str, version: int = None) -> dict:
    """The NLE timeline AS IT IS NOW vs the version Cláudio delivered: per shot same / trimmed / moved / removed, clips the
    editor added, and their markers (notes) with the shot each falls on. The editor's changes win: adopt_nle_timeline."""
    from claudio import nle_read
    p = montage.project_dir(project)
    v, fit = montage.load_fit(p, version)
    return {'compared_with_version': v, **nle_read.diff_with_edit(nle_read.read_fcp7(nle_xml), fit)}


@mcp.tool()
def adopt_nle_timeline(project: str, nle_xml: str, version: int = None) -> dict:
    """Make the editor's current NLE timeline the new base version, exactly as it is (no beat re-fitting), keeping
    Cláudio's settings (format, music, captions). Apply notes on top of THIS version, then export_edit."""
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
    """A client's profile (vocabulary, receita, formats, end card, music, captions, structure, house style, notes, past
    jobs). Read it first for a known client: ask only what is different this time. Null profile = new client."""
    from claudio import clients
    p = clients.load(name)
    return {'profile': p, 'folder': core.nle_path(clients.clients_dir()),
            'known_clients': sorted(os.path.splitext(f)[0] for f in os.listdir(clients.clients_dir()) if f.endswith('.md') and f != 'README.md')}


@mcp.tool()
def save_client_profile(name: str, updates: dict, learn_from_prproj: str = None) -> dict:
    """Create/update a client profile (<clients>/<client>.md). updates: vocabulary (str), receita (recipe name), formats
    (list), end_card {path,in,out}, music {tracks,style}, captions, structure, notes, jobs [{date,name,folder}] (appended).
    learn_from_prproj: a finished Premiere project of the client — measures its house style."""
    from claudio import clients
    p = clients.save(name, updates)
    if learn_from_prproj:
        p = clients.learn_style(name, learn_from_prproj)
    return p


@mcp.tool()
def qc_timeline(nle_xml: str, platform: str = None, sequence_width: int = None, sequence_height: int = None) -> dict:
    """Check the timeline as it is before the editor exports. Reports only: flash frames, black gaps, overlaps, offline
    media, disabled clips, clips past their media, same take twice, jump cuts, speech level jumps, clipped audio,
    size/duration vs platform (reels · tiktok · shorts · feed · youtube · youtube4k), mix loudness vs target (−14
    social/YouTube, −23 tv, −16 podcast), true peak, and music too loud under speech (`ducking`). Offer each issue as a
    marker; the editor decides."""
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
    """After indexing the whole job: everything the logs know, condensed (totals, cameras, colour, minutes of speech,
    frequent words, what is said / seen per clip) — to understand what the project is and write save_project_overview."""
    from claudio import sidecar as _s
    return _s.project_digest(folder)


@mcp.tool()
def save_project_overview(job_folder: str, markdown: str) -> dict:
    """Write <job>/_VISAO_GERAL.md: what the project is, the material, what is strong / missing, open questions and
    proposed directions. Text outside the auto markers is kept."""
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
    """An overlay ASSET (lower third, title, callout, end card…) from the motion-designer core on a TRANSPARENT background
    → .mov with alpha (codec='png' default, small; 'prores' = ProRes 4444, only if asked). aspect: 9x16 · 16x9 · 1x1 ·
    4x5 or WxH, and fps: the sequence's."""
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
    """Captions STEP 1: the edit's speech as sentences {id, text, clip, remembered_correction}. Correct ONLY grammar
    (concordância, regência, crase, punctuation), keeping the speaker's words, register and brand names; show the diff
    (spoken → caption) and pass the approved {id: text} as `corrected`. Approved corrections are remembered per clip."""
    from claudio import motion
    return motion.caption_sentences(nle_xml)


@mcp.tool()
def caption_style_preview(nle_xml: str, aspect: str = '9x16', accent: str = '#4ea1ff', corrected: dict = None,
                          fix: dict = None, client: str = None) -> list:
    """Captions STEP 2: the same moment of the edit with each style on it — social, bold, clean, karaoke, box, minimal —
    the recommended one starred (client profile, else vertical → social, horizontal → minimal). Let the user choose."""
    from claudio import motion, clients
    pref = None
    if client:
        p = clients.load(client)
        pref = (p or {}).get('captions')
    img, meta = motion.caption_style_preview(nle_xml, aspect, accent, corrected, fix, pref)
    return [Image(data=core.to_jpeg(img), format='jpeg'), __import__('json').dumps(meta, ensure_ascii=False)]


@mcp.tool()
async def caption_overlay(nle_xml: str, out_folder: str, ctx: Context, style: str = 'social', aspect: str = None,
                          accent: str = '#4ea1ff', corrected: dict = None, uppercase: bool = None, max_words: int = None,
                          position: str = 'bottom', fix: dict = None, codec: str = 'png', client: str = None,
                          dynamic: bool = None, srt: str = None) -> dict:
    """Captions STEP 4: the chosen style rendered over the whole sequence as it is now → one transparent .mov (PNG in MOV;
    codec='prores' for ProRes 4444) at the sequence's resolution (4K is drawn at 2×): at 0 s, 100 %, never scaled.
    srt = the caption the editor reviewed (premiere_captions writes it): its blocks, text and timings are the truth and
    `corrected` is ignored; without srt, `corrected` + transcript timings. style: social · bold · clean · karaoke · box ·
    minimal · marca (the client's caption_look; `client`, or found from the path); dynamic=True = its social cut.
    position: bottom · center · top. fix: spellings {"wrong": "right"}. aspect: only for another delivery format."""
    import asyncio
    from claudio import motion
    look = None
    if style == 'marca' or client:
        from claudio import clients, run
        prof = clients.load(client) if client else run.client_for(nle_xml)
        look = dict((prof or {}).get('caption_look') or {})
        if look:
            style = 'marca'
            if dynamic is not None:
                look['dynamic'] = bool(dynamic)
        elif style == 'marca':
            return {'error': 'perfil do cliente sem caption_look: salve com save_client_profile(caption_look={...})'}
    st = {'f': 0.0}
    loop = asyncio.get_running_loop()
    task = loop.run_in_executor(None, lambda: motion.caption_overlay(nle_xml, out_folder, aspect, accent, uppercase, max_words, fix,
                                                                     progress=lambda f: st.update(f=f), style=style,
                                                                     corrected=corrected, position=position, codec=codec,
                                                                     look=look, srt=srt))
    while not task.done():
        try:
            await ctx.report_progress(st['f'] * 100, 100, f'renderizando legendas ({style}) {int(st["f"] * 100)}%')
        except Exception:
            pass
        await asyncio.sleep(2)
    return task.result()


@mcp.tool()
def captions_srt(nle_xml: str, out_srt: str, corrected: dict = None, style: str = None, client: str = None,
                 max_chars: int = None, lines: int = None, max_words: int = None, fix: dict = None) -> dict:
    """Captions STEP 3, the review round: editable .srt with the approved corrections, cut by phrase like a subtitler
    (never across a sentence end, never ending on a weak word, never splitting a number or a name; a breath ends a block),
    timed on the sequence's frames (in on the voice, held to read, chained 2 frames apart, snapped to picture cuts).
    style (or client) sets the line length; max_chars/lines override (default 1×26 vertical, 2×42 horizontal). Put it in
    the NLE as a caption track (Premiere MCP create_caption_track or drag; Resolve: import subtitle) and STOP: the editor
    fixes words and timing there."""
    from claudio import motion
    look = None
    if style == 'marca' or client:
        from claudio import clients, run
        prof = clients.load(client) if client else run.client_for(nle_xml)
        look = (prof or {}).get('caption_look') or None
        style = 'marca' if look else (None if style == 'marca' else style)
    return motion.captions_srt(nle_xml, out_srt, max_words, max_chars, fix, corrected, lines, style, look)


@mcp.tool()
def caption_mogrt(nle_xml: str, style: str = 'social', srt: str = None, corrected: dict = None, fix: dict = None,
                  client: str = None, accent: str = '#4ea1ff', position: str = 'bottom', aspect: str = None,
                  out_dir: str = None) -> dict:
    """Captions as EDITABLE Motion Graphics Templates: the caption_overlay styles (incl. marca and its dynamic cut), one
    .mogrt clip per block on a new top track — the editor fixes words or trims in Premiere, no re-render. Builds the
    template in After Effects once per style × format (≈1 min, cached), then writes a placement plan
    (<name>.claudio-mogrt.jsx). srt = the reviewed .srt (preferred); else `corrected`. Do it LAST: an XML round trip
    (clean_speech, add_sfx, flatten, stereo…) drops MOGRT text (listed in `dropped`)."""
    from claudio import clients, mogrt, run
    look = None
    if style == 'marca' or client:
        prof = clients.load(client) if client else run.client_for(nle_xml)
        look = (prof or {}).get('caption_look')
    return mogrt.caption_mogrt(nle_xml, style, aspect, accent, srt, corrected, fix, look, position, out_dir)


@mcp.tool()
def premiere_captions(prproj: str, sequence: str, nle_xml: str = None, track: str = 'C1', out_srt: str = None) -> dict:
    """The caption track as the editor fixed it in Premiere (text + timing), read from the saved .prproj. Once edited it is
    THE caption: use the .srt this writes (<captions folder>/<sequence>_revisada.srt) as `srt`, never regenerate it.
    With nle_xml, lyric fixes are learned into the song. The project must be saved first."""
    import re
    from claudio import motion, nle_read, profile
    cues = nle_read.premiere_captions(prproj, sequence, track)
    if not cues:
        return {'cues': 0, 'note': f'no caption items on {track} of "{sequence}" (saved project?)'}
    out = out_srt or os.path.join(profile.folder('legendas', profile.job_of(prproj)), re.sub(r'[\\/:*?"<>|]', '-', sequence) + '_revisada.srt')
    p = nle_read.write_srt(cues, out)
    learned = motion.learn_caption_edits(nle_xml, cues) if nle_xml else []
    return {'srt': core.nle_path(p), 'cues': len(cues), 'first': cues[:3], 'learned_lyric_fixes': learned}


@mcp.tool()
def clean_speech(nle_xml: str, out_xml: str = None, pace: str = 'apertado', max_pause: float = None, fillers: bool = True,
                 notes: bool = True, crossfade: bool = True, zoom: bool = False, punch: list = None, preview: bool = False,
                 extra_fillers: list = None, keep_tracks: list = None, dialogue_track: str = 'A1') -> dict:
    """Tighten a sequence the editor already cut: long pauses and hesitations ("ãh", "hum", a drawn-out "é…") out,
    rippled across every track; music beds keep playing. NEW sequence "<name> (limpo)" + every cut listed.
    pace: apertado (pause > 0.45 s → 0.25 s, default) · medio (0.7 → 0.4) · solto (1.0 → 0.6); preview=True only reports
    each pace. notes: cuts what was said for the editor — set commands ("Ação.", "três, dois, um, valendo"), "editor, …",
    announced restarts ("vou de novo", "errei", with the abandoned attempt) — each leaves a marker; "aqui entra…" without
    "editor" only gets a yellow marker. crossfade: 2 frames on every join. zoom=True: pieces of one shot alternate
    100 / 107.5 %. punch = [[from, to], …] seconds of THIS sequence: that line at 115 % — propose them (price, payoff,
    strong claim; ~one per 20–30 s) and pass only the ones the editor approved."""
    from claudio import cleanup
    return cleanup.clean_speech(nle_xml, out_xml, max_pause, fillers, extra_fillers or (), keep_tracks, dialogue_track,
                                pace, notes, crossfade, zoom, punch, preview)


@mcp.tool()
def auto_cut_multicam(nle_xml: str, cameras: dict, mics: dict, out_xml: str = None, min_hold: float = 2.0,
                      max_hold: float = 12.0, margin_db: float = 6.0) -> dict:
    """Podcast / interview / panel: cut a SYNCED multicam sequence by who is speaking, from each person's mic.
    cameras = {"V1": ["Ana"], "V2": ["Robson"], "V3": []} (nobody or several people = the wide); mics = {"Ana": "A3"}
    (ask when unsure). Wide on crosstalk and long pauses, a cutaway every max_hold s, no shot under min_hold. NEW sequence:
    the cut on a new top track, camera tracks below disabled, audio untouched."""
    from claudio import autocut
    return autocut.auto_cut(nle_xml, cameras, mics, out_xml, min_hold, max_hold, margin_db)


@mcp.tool()
def add_sfx(nle_xml: str, out_xml: str = None, whoosh: bool = True, pops: bool = True, sfx_dir: str = None,
            srt: str = None) -> dict:
    """Sound design for social cuts: a whoosh on every b-roll entry and a light pop on caption keywords (price, number,
    brand), on a NEW audio track of a copy ("<name> (com SFX)") at −20 / −24 dB. sfx_dir: the editor's sounds
    (whoosh*.wav, pop*.wav), else built-in. srt: the reviewed captions."""
    from claudio import motion, sfx
    words = motion.srt_timeline_words(nle_xml, srt, remember=False)['words'] if srt else None
    return sfx.add_sfx(nle_xml, out_xml, whoosh, pops, sfx_dir, words)


@mcp.tool()
def music_brief(nle_xmls: list) -> list:
    """Soundtrack step 1, per sequence: exact length, share of speech (→ instrumental bed), cut pace, music already there,
    and search constraints (instrumental, BPM range, energy, minimum duration) for a music-store connector such as
    Storyblocks. Then ask in ONE message: mood/genre (propose one), one track for all or one each, whether existing music
    stays; show 3–5 options per sequence and recommend one."""
    from claudio import music
    return [music.brief(x) for x in nle_xmls]


@mcp.tool()
def fetch_music(url: str, job: str, title: str = None) -> dict:
    """Soundtrack step 2: put the chosen track in the job's music folder — a download link (the store's, after the editor
    picked it) or a local file. Named by `title`."""
    from claudio import music
    return music.fetch(url, job, title)


@mcp.tool()
def music_plan(job: str, sequences: list) -> dict:
    """Soundtrack step 3, all sequences in one plan: sequences = [{nle_xml, music (fetch_music path), sequence_id?,
    mode? ('auto' · 'fim' = the song's real ending on the end of the cut · 'inicio' · 'janela' = best stretch), start?
    (song seconds), duck? (default true), gain_db?}]. Levels from measured loudness: ~18 LU under speech, ~6 LU under in
    pauses ≥ 2.5 s, ~2 LU under on a card without speech, −14 LUFS with no speech; Volume keyframes ramp before each line.
    Writes <music folder>/trilha <stamp>.claudio-trilha.jsx, which the panel applies (new audio track per sequence)."""
    from claudio import music
    return music.plan(job, sequences)


@mcp.tool()
def event_recap(project: str, footage: str, music: str, duration: float = 45, aspect: str = '9:16', pace: str = 'fast',
                music_start: float = None, name: str = None, end_card: str = None, audio: str = 'mute',
                faces: bool = True) -> dict:
    """Aftermovie / event recap from indexed event footage and a song: the best moments (steady, sharp, loud — crowd,
    chorus, applause — with people, little talking), the strongest as the hook, the rest in the order they happened, no
    angle twice in a row, cut on the beat, fitted to `duration`. Saved as the next edit version of `project`.
    music_start: a downbeat (default: the best stretch). audio: 'mute' (music only) or 'ambient'."""
    from claudio import recap
    edit, info, fit = recap.recap_edit(footage, music, duration, aspect, pace, music_start, name, end_card, audio, faces)
    if fit is None:
        return edit
    p = montage.project_dir(project)
    v = montage.next_version(p)
    fit['version'] = v
    _json.dump(fit, open(os.path.join(p, f'edit_v{v}.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    return {'version': v, 'duration': fit['duration'], 'bpm': fit.get('bpm'), 'warnings': fit['warnings'],
            'timeline': montage.timeline_table(fit), **info, 'edit': edit}


@mcp.tool()
def narration(text: str, out_folder: str, engine: str = 'elevenlabs', voice: str = None, budget: int = None) -> dict:
    """Narration for the edit with the motion-designer skill: engine='elevenlabs' (one continuous take, word timings,
    cached; voice = ElevenLabs voice_id; hard credit budget, default = text length + 10 % — tell the user the cost
    BEFORE running) or engine='edge' (free edge-tts pt-BR/en voices). Returns narration.wav (+ words.json) to import
    on an audio track. Follow motion_reference('Narration') for how to write the script."""
    from claudio import motion
    return motion.narration(text, out_folder, engine, voice, budget)


EDIT_DOC = """edit = {
  "name": "Cliente — Pessoa v1", "aspect": "9:16" | "16:9" | "1:1" | "4:5", "fps": optional "30000/1001",
  "music": {"path": "D:\\x\\song.wav", "start": 63.32 (a downbeat, music_windows), "gain_db": -4, "duck_db": -14, "fade_out": 1.5},
  "cut_on": "beat" | "downbeat" | "half" | "none",   "pace": "calm" | "medium" | "fast" | "hype",
  "pace_follows_music": true (faster in high-energy sections, slower in low),
  "shots": [                                   # V1 spine, in order
    {"path": "...C0025.MP4", "in": 9.2, "out": 22.3, "audio": "dialogue", "snap": "speech", "label": "fala 1", "reframe_x": 0.42},
                                               # dialogue keeps its length; tail stretched to the next beat if the source allows
    {"path": "...C0031.MP4", "in": 3.0, "out": 9.0, "audio": "ambient", "beats": 4},   # or "len": seconds
    {"path": "...CARTELA.mp4", "in": 5.37, "out": 10.78, "audio": "mute", "label": "end card"}],
  multicam shot: {"path": "...C0015.MP4", "offset": <sync_multicam offset>, "beats": 8, "reframe_x": "auto", "grade": "auto"}
  "overlays": [                                # V2 b-roll over the spine (speech keeps playing)
    {"from": 5.2, "to": 13.0, "clips": [{"path": "...FACHADA.mp4", "in": 0, "out": 6}]}],
  "captions": {"enabled": true, "max_words": 4, "uppercase": true, "fix": {"wrong": "right"}},   # render_edit only
  "texts": [{"text": "Nome\\nCargo", "at": 0.5, "dur": 2.5, "style": "lower" | "title"}],        # render_edit only
  "ambient_db": -16, "fade_in": 0.3, "fade_out": 0.8 }
Optional per shot/clip: "anchor": "start"|"center"|"end", "zoom", "reframe_y", "gain_db",
"ext_audio": {"path": "...wav", "offset": <sync_audio offset_sec>}."""


def build_edit(project: str, edit: dict) -> dict:
    """Fit an edit to the beat grid and save it as the next version (<project>/edit_vN.json). Returns the timeline (V1
    spine + V2 b-roll, timeline/source times, length in beats) and warnings."""
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
    """Review a cut WITHOUT rendering: one tile per shot and b-roll from the SOURCE frames (rotation + crop applied) and a
    timeline bar (beats, sections, b-roll layer). Look for: same angle back to back, soft shots, crops cutting heads,
    b-roll that doesn't match the words."""
    p = montage.project_dir(project)
    v, fit = montage.load_fit(p, version)
    return [Image(data=core.to_jpeg(montage.source_storyboard(fit), max_side=3000), format='jpeg'),
            __import__('json').dumps({'version': v, 'duration': fit['duration'], 'warnings': fit['warnings']})]


def export_edit(project: str, out_path: str = None, version: int = None) -> dict:
    """An edit version as FCP7 XML for finishing: V1 spine, V2 b-roll (Basic Motion for the reframe), A1 production audio as
    ONE stereo track with per-shot levels (a one-sided lav or two different mics go dual mono on the next pair), then
    music. Captions/titles are not in the XML. The sequence and the file are named after edit["name"] (+ " vN"): give
    every edit a name that tells it apart in the bin — client — person/subject. Edit format: see build_edit."""
    p = montage.project_dir(project)
    v, fit = montage.load_fit(p, version)
    fit = {**fit, 'name': montage.sequence_name(fit, v)}
    out = core.local_path(out_path) if out_path else os.path.join(p, montage.safe_filename(fit['name']) + '.xml')
    return montage.export_edit_fcp7(fit, out)


build_edit.__doc__ += '\n' + EDIT_DOC
for _f in (build_edit, export_edit):
    mcp.tool()(_f)


@mcp.tool()
def recipe(name: str = None, section: str = None) -> dict:
    """Kinds of project ("receitas"). Without name: the list (nome, titulo, quando it fits, padroes). With name: the
    recipe to FOLLOW for that edit — brief questions, steps, taste rules, defaults — plus the rules common to every edit.
    The client profile, the brief and the editor's request override a recipe's defaults. section: one part only
    ("brief", "etapas", "legendas", "entregar-no-nle"…). The editor's own recipes (~/.config/claudio/receitas) add new
    kinds or replace a built-in one by name."""
    from claudio import recipes
    if not name:
        return {'receitas': recipes.listing(), 'pasta_do_editor': core.nle_path(recipes.user_dir())}
    try:
        r = recipes.load(name, section)
    except KeyError as e:
        return {'error': str(e.args[0])}
    return {'nome': r['nome'], 'padroes': r['meta'].get('padroes') or {}, 'entregas': r['meta'].get('entregas') or [],
            'origem': r['origem'], 'text': r['text']}


@mcp.prompt()
def montar(pasta: str = '', pedido: str = '') -> str:
    """Montar uma timeline no Premiere/Resolve a partir do material bruto: escolhe a receita do tipo de projeto
    (depoimento, recap, beat edit, show multicâmera, curta…) e segue as etapas dela até a timeline no NLE."""
    return MONTAR.format(pasta=pasta or '(ask the user for the footage folder)', pedido=pedido or '(none yet)')


MONTAR = """You are assembling a real edit with the claudio MCP (Cláudio, o Editor), TOGETHER with the Premiere Pro / DaVinci Resolve MCP.
Footage: {pasta}. User request: {pedido}.

1. Start right away: index_folder(<footage>) — logging runs in the background while you talk.
2. client_profile(<client>) if there is a client. A profile with "receita" names the kind of project they usually order.
3. KIND OF PROJECT: recipe() lists the recipes (nome, titulo, quando). Pick the one that fits the request and the
   footage; when it is not obvious, ask with AskUserQuestion (best guess first, marked "(Recomendado)"), with the
   editor's own kind as the free answer.
4. recipe(<nome>) and FOLLOW it: its brief, steps and taste rules, and the common rules that come with it. Its defaults
   apply only where the client profile, the brief or the request say nothing. save_brief with "receita": <nome>.
5. No recipe fits: recipe("comum") gives the rules every edit follows; work from them and tell the editor this kind
   could become a recipe of their own in the recipes folder recipe() shows.
"""


def _drop_schema_titles(o):
    """pydantic's "title": "Nle Xml" on every argument repeats its name and goes out with every message (~7k chars)."""
    if isinstance(o, dict):
        for k in [k for k, v in o.items() if k == 'title' and isinstance(v, str)]:
            del o[k]                      # a schema's title; an ARGUMENT named title (fetch_music) is a dict and stays
        for v in o.values():
            _drop_schema_titles(v)
    elif isinstance(o, list):
        for v in o:
            _drop_schema_titles(v)


for _t in mcp._tool_manager.list_tools():
    _drop_schema_titles(_t.parameters)


def main():
    mcp.run()


if __name__ == '__main__':
    main()
