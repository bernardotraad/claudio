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
from decupa import core, montage

mcp = FastMCP('decupa', instructions=__doc__)

# ── background indexing (GPU work is serialized) ──
_jobs, _q = {}, queue.Queue()


def _worker():
    while True:
        path, lang, force, vocab = _q.get()
        job = _jobs[path]
        try:
            job['state'] = 'running'
            core.build_index(path, lang, lambda stage, f: job.update(stage=stage, progress=round(min(f, 1), 2)), force, vocab)
            job['state'] = 'done'
        except Exception as e:
            job.update(state='error', error=f'{e}', trace=traceback.format_exc()[-1500:])
        finally:
            _q.task_done()


threading.Thread(target=_worker, daemon=True).start()


def _enqueue(path, lang, force=False, vocabulary=None):
    path = core.local_path(path)
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    j = _jobs.get(path)
    if j and j['state'] in ('queued', 'running'):
        return path
    _jobs[path] = {'state': 'queued', 'stage': None, 'progress': 0}
    _q.put((path, lang, force, vocabulary))
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
def index_media(path: str, language: str = 'pt', wait_seconds: int = 40, force: bool = False, vocabulary: str = None) -> dict:
    """Analyse one video (Windows path like D:\\Footage\\A001.mov or Linux path). Runs in background on the GPU:
    probe + audio + picture (cuts, sharpness, exposure, camera motion) + transcript with word timestamps.
    Waits up to wait_seconds; if not finished returns state=running — poll index_status. Cached afterwards."""
    import time
    p = _enqueue(path, language, force, vocabulary) if force or vocabulary or not _is_indexed(path) else core.local_path(path)
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
def set_rotation(degrees: int, paths: list[str] = None, folder: str = None) -> dict:
    """Clips shot with the camera on its side (vertical framing) often carry no rotation metadata: if a contact sheet
    shows people sideways, set 90 (clockwise) / 270 / 180 here. Applies everywhere after: sheets, crops, renders, XML."""
    files = [core.local_path(p) for p in (paths or [])]
    if folder:
        root = core.local_path(folder)
        files += [os.path.join(dp, f) for dp, _, fn in os.walk(root) for f in sorted(fn) if os.path.splitext(f)[1].lower() in core.VIDEO_EXT]
    return {'rotated': [core.nle_path(f) for f in files if core.set_rotation(f, degrees) is not None], 'degrees': degrees}


@mcp.tool()
def index_folder(folder: str, language: str = 'pt', recursive: bool = True, vocabulary: str = None) -> dict:
    """Queue every video in a folder (e.g. a card dump) for indexing. Returns the list; poll index_status.
    vocabulary: names the transcript must get right — brand, people, places ("INC Empreendimentos, Juiz de Fora")."""
    root = core.local_path(folder)
    files = []
    for dp, dn, fn in os.walk(root):
        files += [os.path.join(dp, f) for f in sorted(fn) if os.path.splitext(f)[1].lower() in core.VIDEO_EXT
                  and not f.startswith('._')]
        if not recursive:
            break
    queued = [f for f in files if not _is_indexed(f) or vocabulary]
    for f in queued:
        _enqueue(f, language, vocabulary=vocabulary)
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
def multicam_sync(master: str, project: str, folder: str = None, paths: list[str] = None) -> dict:
    """Sync every camera clip to one long master recording (board mix / recorder). Per clip: offset
    (master_time = clip_time + offset), confidence, method (audio | transcript fallback when mics sound too
    different), drift over the clip. Saved to <project>/multicam.json for multicam_monitor and multicam edits:
    a shot {"path": clip, "offset": offset, "beats": 8} with edit.music = {"path": master, "master": true, "start": t}."""
    files = [core.local_path(p) for p in (paths or [])]
    if folder:
        root = core.local_path(folder)
        files += [os.path.join(dp, f) for dp, _, fn in os.walk(root) for f in sorted(fn)
                  if os.path.splitext(f)[1].lower() in core.VIDEO_EXT and not f.startswith('._')]
    res = montage.multicam_sync(master, sorted(set(files)))
    p = montage.project_dir(project)
    _json.dump({'master': core.local_path(master), 'clips': res}, open(os.path.join(p, 'multicam.json'), 'w'), indent=1)
    return {'master': core.nle_path(core.local_path(master)), 'clips': [{k: r.get(k) for k in
            ('clip', 'offset', 'confidence', 'reliable', 'method', 'master_in', 'master_out', 'fps', 'drift_ms_over_clip')} for r in res]}


@mcp.tool()
def multicam_monitor(project: str, start: float, end: float, columns: int = 8) -> list:
    """LOOK at every synced angle side by side: one row per camera, one column per moment of MASTER time in
    [start, end] (black = that camera wasn't rolling). Choose the angle for each bar from this."""
    p = montage.project_dir(project)
    mc = _json.load(open(os.path.join(p, 'multicam.json')))
    img, ts = montage.multicam_grid(mc['clips'], start, end, max(2, min(columns, 12)), 260)
    return [Image(data=core.to_jpeg(img), format='jpeg'), _json.dumps({'columns_at_master_seconds': ts})]


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
    (V1 spine + V2 b-roll with timeline/source times and length in beats) and warnings. Then render_edit to see it."""
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
def render_edit(project: str, version: int = None, quality: str = 'preview', wait_seconds: int = 50) -> list:
    """Render an edit version to MP4 the user can watch: spine + b-roll overlays (reframed), music ducked under
    speech, captions/titles burned in, fades, loudness −14 LUFS. quality='preview' (fast, small) or 'final' (full
    res). Returns the file path and a STORYBOARD image (one tile per shot taken from the rendered file + timeline bar
    with beats/sections) — check it before showing the user. Long renders continue in background: call again."""
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


def export_edit(project: str, out_path: str = None, version: int = None) -> dict:
    """Export an edit version as FCP7 XML for finishing: V1 spine, V2 b-roll (with Basic Motion scale/centre for the
    reframe), A1/A2 production audio with per-shot levels, A3/A4 music. Premiere: import_media([xml]);
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
    """Montar um vídeo a partir do material bruto: entrevista guiada → trilha primeiro → decupagem com imagens →
    corte no beat → render pra revisão → XML pro Premiere/Resolve."""
    return MONTAR.format(pasta=pasta or '(ask the user for the footage folder)', pedido=pedido or '(none yet)')


MONTAR = """You are editing a real video with the decupa MCP. Footage: {pasta}. User request: {pedido}.

Work like a senior editor: decide from evidence (transcripts, measured quality, images you LOOK at), never from file
names. Ask in the user's language, ONE step at a time, with AskUserQuestion (max 4 questions per call, best option
first marked "(Recomendado)" with a one-line reason). Skip any question the request already answered.

0. Start indexing right away: index_folder(<footage>) — it runs in background while you ask the questions.
1. TYPE (decides the rest): "Depoimento / entrevista com b-roll" · "Aftermovie / resumo de evento" ·
   "Beat edit / hype sem fala" · "Show / clipe musical multicâmera" · "Cena / curta narrativo".
2. IDEA (plain message): what is it about, the one message, must-have moments/people/lines, and who watches.
   If they say "você decide": finish the decupagem first, then propose 3 ideas grounded in what the footage holds.
3. SETTINGS (one call): Format (multiSelect: 9:16 Reels/TikTok/Shorts · 16:9 YouTube · 4:5 feed · 1:1) ·
   Duration (15 s · 30 s · 60 s · 90 s+) · Versions (1 · 2 variations) · Captions (burned social style — recommended
   for vertical / discreet / none).
4. MUSIC FIRST: "Tenho a trilha (arquivo)" · "Escolhe entre as da pasta" · "Sem música — só som direto / do show".
   Then music_map → tell in one line: BPM (and the half-time reading if it feels slow), sections, drop, silences.
   music_windows(duration) → offer the 2–3 best stretches; the cut is built on that grid.
5. PACE & SOUND (one call): Pace ("Segue a música" recommended · calmo · médio · rápido) · Cuts on (beat ·
   compasso/downbeat — recommended for half-time or calm songs) · Original audio (fala em primeiro plano com a
   trilha abaixando — depoimento · ambiente baixo sob a trilha — aftermovie · só trilha) · Opening/closing (hook
   with the strongest line in the first 3 s; end card/logo file?; CTA text?).
6. DECUPAGEM with your eyes: scenes / takes (best take, usually the last complete one) · broll (usable windows) ·
   find_phrase for the hook · contact_sheet on every candidate you plan to use · timeline_strip for tight speech cuts
   · reframe_preview for vertical · annotate what you saw. save_brief(project, answers).
7. STRUCTURE: write a short beat sheet (hook → body → peak on the drop → end card) mapping music sections to
   material; show it to the user in 4–6 lines only if the idea is open, otherwise build straight away.
8. build_edit(project, edit) → fix every warning → render_edit(project) → LOOK at the storyboard (and a
   contact_sheet of the render if in doubt): flash frames, soft shots, repeated angles back-to-back, captions over
   faces, a cut that misses the beat, dead air. Fix and rebuild before showing anything.
9. Hand the user the preview path + a 3–5 line summary (structure, song stretch, what you chose and why) and ask
   for notes. Each round of notes = a new version (build_edit again); never overwrite.
10. When approved: render_edit(quality="final") and/or export_edit → Premiere (import_media) / Resolve
    (import_timeline_from_file) for finishing.
"""


def main():
    mcp.run()


if __name__ == '__main__':
    main()
