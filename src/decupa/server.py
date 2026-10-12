"""decupa — MCP server that lets Claude actually SEE and HEAR footage before cutting in Premiere / Resolve.

Run:  python3 server.py            (stdio MCP)
Workflow the tools are designed for:
  1. index_media / index_folder   → probe, transcript (word timestamps), cuts, focus/exposure/motion
  2. overview                     → what is in the clip (speech? b-roll? shots, takes, usable windows)
  3. contact_sheet / timeline_strip / frames  → LOOK at the picture (and words + waveform) at exact times
  4. takes (dialogue) · broll (no speech) · find_phrase   → evidence-ranked candidates
  5. annotate                     → write the decupagem log (what you saw) so it persists
  6. snap_cut → export_timeline / nle_cut_list   → frame-accurate handoff to the Premiere / Resolve MCPs
"""
import os, queue, sys, threading, traceback

from mcp.server.fastmcp import FastMCP, Image
from decupa import core

mcp = FastMCP('decupa', instructions=__doc__)

# ── background indexing (GPU work is serialized) ──
_jobs, _q = {}, queue.Queue()


def _worker():
    while True:
        path, lang, force = _q.get()
        job = _jobs[path]
        try:
            job['state'] = 'running'
            core.build_index(path, lang, lambda stage, f: job.update(stage=stage, progress=round(min(f, 1), 2)), force)
            job['state'] = 'done'
        except Exception as e:
            job.update(state='error', error=f'{e}', trace=traceback.format_exc()[-1500:])
        finally:
            _q.task_done()


threading.Thread(target=_worker, daemon=True).start()


def _enqueue(path, lang, force=False):
    path = core.local_path(path)
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    j = _jobs.get(path)
    if j and j['state'] in ('queued', 'running'):
        return path
    _jobs[path] = {'state': 'queued', 'stage': None, 'progress': 0}
    _q.put((path, lang, force))
    return path


def _idx(path, lang='pt'):
    """Return the index, or raise a clear 'still indexing' error the model can act on."""
    p = core.local_path(path)
    j = _jobs.get(p)
    if j and j['state'] in ('queued', 'running'):
        raise RuntimeError(f'still indexing ({j["stage"]} {int(j["progress"] * 100)}%) — call index_status, then retry')
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
    if idx.get('audio'):
        a = idx['audio']
        out['audio'] = {k: a[k] for k in ('speech_ratio', 'noise_floor_db', 'peak_db', 'clipped_ms')}
    if idx.get('picture'):
        out['hard_cuts_sec'] = idx['picture']['cuts_sec'][:60]
    sp = idx.get('speech')
    out['kind'] = 'dialogue' if sp and sp['n_words'] > max(8, i['duration'] / 6) else 'mostly_no_speech'
    if sp:
        out['language'] = sp['language']
        out['words'] = sp['n_words']
    return out


# ───────────────────────────── indexing ─────────────────────────────
@mcp.tool()
def index_media(path: str, language: str = 'pt', wait_seconds: int = 40, force: bool = False) -> dict:
    """Analyse one video (Windows path like D:\\Footage\\A001.mov or Linux path). Runs in background on the GPU:
    probe + audio + picture (cuts, sharpness, exposure, camera motion) + transcript with word timestamps.
    Waits up to wait_seconds; if not finished returns state=running — poll index_status. Cached afterwards."""
    import time
    p = _enqueue(path, language, force) if force or not _is_indexed(path) else core.local_path(path)
    t0 = time.time()
    while time.time() - t0 < wait_seconds and _jobs.get(p, {}).get('state') in ('queued', 'running'):
        time.sleep(0.5)
    j = _jobs.get(p, {'state': 'done'})
    if j['state'] == 'done':
        return {'state': 'done', **_summary(core.build_index(p, language))}
    return {'path': p, **j}


def _is_indexed(path):
    try:
        idx_p = os.path.join(core.cache_dir(core.local_path(path)), 'index.json')
        return os.path.exists(idx_p)
    except FileNotFoundError:
        return False


@mcp.tool()
def index_folder(folder: str, language: str = 'pt', recursive: bool = True) -> dict:
    """Queue every video in a folder (e.g. a card dump) for indexing. Returns the list; poll index_status."""
    root = core.local_path(folder)
    files = []
    for dp, dn, fn in os.walk(root):
        files += [os.path.join(dp, f) for f in sorted(fn) if os.path.splitext(f)[1].lower() in core.VIDEO_EXT
                  and not f.startswith('._')]
        if not recursive:
            break
    queued = [f for f in files if not _is_indexed(f)]
    for f in queued:
        _enqueue(f, language)
    return {'videos': len(files), 'queued': len(queued), 'already_indexed': len(files) - len(queued),
            'files': [core.nle_path(f) for f in files]}


@mcp.tool()
def index_status() -> dict:
    """Progress of background indexing jobs."""
    return {'pending': _q.qsize(),
            'jobs': {core.nle_path(p): {k: v for k, v in j.items() if k != 'trace'} for p, j in _jobs.items()}}


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
    return core.annotate(_idx(path), start, end, text, tags, rating)


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
    """Write the cut as a timeline file. format='fcp7xml' (Premiere: import the .xml; Resolve: import_timeline_from_file)
    or 'edl' (CMX3600). Clip markers from annotate() are included in XML. Returns the path to import (as the NLE sees it). """
    out = core.local_path(out_path)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    return core.export_edl(plan, out) if format == 'edl' else core.export_fcp7(plan, out)


for _f in (nle_cut_list, export_timeline):
    _f.__doc__ += '\n' + PLAN_DOC
    mcp.tool()(_f)

def main():
    mcp.run()


if __name__ == '__main__':
    main()
