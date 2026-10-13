# decupa

**An MCP server that lets Claude actually _see_ and _hear_ your footage before it cuts.**

[Português](README.pt-BR.md)

The Premiere Pro and DaVinci Resolve MCP servers let an AI *operate* your editor — import media, place clips,
trim, export. What they can't do is look at the footage. Ask for "the best take" or "a good b-roll of the
hands" and the model is guessing from file names.

`decupa` (from Portuguese *decupagem*, footage logging) is the missing eyes and ears. It analyses the real
media and gives the model evidence:

- **What is said** — word-level transcript (Whisper), including the slate called out loud
  (*"scene 3, take 2 … action … cut"*).
- **What is seen** — contact sheets, full frames and timeline strips returned as **images**, every tile stamped
  with source timecode, seconds and the words spoken at that instant.
- **What is usable** — hard cuts, relative focus, exposure, camera motion (static / pan / tilt / shaky),
  silences, aborted takes.

The model decides from that evidence, `decupa` turns the decision into **frame-accurate in/out points**, and the
Premiere or Resolve MCP executes it.

```
footage ──► decupa (index, look, rank, snap) ──► cut plan ──► Premiere MCP / Resolve MCP
                    ▲                                         (or FCP7 XML / EDL import)
            images + transcript + metrics
                    ▼
                  Claude
```

## What it does

| Need | Tool | How |
| --- | --- | --- |
| Index footage | `index_media`, `index_folder`, `index_status` | Background queue (GPU). ~4 min per 15 min of 1080p on an RTX 3060. Cached. |
| Best dialogue take across clips | `scenes` | Reads the spoken slate, keeps only what's between **"action"** and **"cut"**, flags aborted takes ("wait", "again"), compares each take's lines with the consensus version, ranks them (`prefer="last"` by default — the last complete take usually wins). Tells you whether to judge by lines, performance (improvised) or picture (no dialogue). |
| Repeated lines inside one clip | `takes` | Splits speech at pauses, groups repeated attempts and false starts, ranks them. |
| B-roll / no-speech material | `broll` | Splits at hard cuts, then into runs of the same camera behaviour that are in focus, exposed and steady. Returns usable windows (best first) and rejected stretches with the reason (`soft`, `blown`, `dark`, `shaky`, `chaotic`). |
| **Look** | `contact_sheet` | Grid of N frames over a range, stamped with TC + spoken words. |
| **Look closer** | `timeline_strip` | Thumbnails + waveform + every word as a box on one time axis — for exact in/out points. |
| **Check a frame** | `frames` | Full-size frames at exact seconds (focus, blinks, first/last frame of a cut). |
| Find a line | `find_phrase` | Fuzzy search over word-level transcripts of many clips. |
| Remember what was seen | `annotate`, `read_annotations` | Persistent footage log per clip; exported as markers in the XML. |
| Frame-accurate cut | `snap_cut` | Never clips a word (moves the boundary out of it, onto the quietest audio frame); `tighten=True` trims to the words for jump cuts; `mode="picture"` avoids flash frames from the next shot. |
| Hand off | `nle_cut_list`, `export_timeline` | Exact numbers for the Premiere/Resolve MCPs, or an FCP7 XML / CMX3600 EDL both editors import. |

Also: `overview`, `library`, `transcript`, `quality`.

## Install

Requirements: Python ≥ 3.10, `ffmpeg` and `ffprobe` on `PATH`.

Transcription backend is picked automatically:

| Machine | Backend |
| --- | --- |
| NVIDIA GPU (Linux / WSL / Windows) | `faster-whisper` on CUDA — install with the `[cuda]` extra |
| Apple Silicon Mac | `mlx-whisper` (installed automatically on macOS arm64) |
| Anything else | `faster-whisper` on CPU (slower) |

The first transcription downloads `whisper-large-v3-turbo` (~1.6 GB).

### macOS (Apple Silicon)

```bash
brew install ffmpeg uv
uv tool install --python 3.12 git+https://github.com/bernardotraad/decupa.git
claude mcp add -s user decupa -- decupa
```

Update later with `uv tool upgrade decupa`.

### Windows — GPU through WSL (recommended with an NVIDIA card)

Premiere and Resolve run on Windows; the analysis runs in WSL where CUDA is easy. `decupa` translates paths both
ways: you pass `D:\Footage\A001.mov`, it reads `/mnt/d/Footage/A001.mov`, and everything it returns
(and writes into XML/EDL) uses the Windows path the editor sees.

Inside WSL (Ubuntu):

```bash
sudo apt install ffmpeg python3-pip
pip install --user --break-system-packages "decupa[cuda] @ git+https://github.com/bernardotraad/decupa.git"
which decupa   # e.g. /home/<you>/.local/bin/decupa
```

Then in **Windows** (PowerShell), register it with Claude Code so it sits next to the Premiere/Resolve MCPs:

```powershell
claude mcp add -s user decupa -- C:\Windows\System32\wsl.exe -d Ubuntu -- /home/<you>/.local/bin/decupa
```

### Windows / Linux — native

```bash
pip install "decupa[cuda] @ git+https://github.com/bernardotraad/decupa.git"   # drop [cuda] without NVIDIA
claude mcp add -s user decupa -- decupa
```

### Claude Desktop

Add to `claude_desktop_config.json` (same command as above):

```json
{
  "mcpServers": {
    "decupa": { "command": "decupa" }
  }
}
```

On Windows + WSL: `"command": "C:\\Windows\\System32\\wsl.exe", "args": ["-d", "Ubuntu", "--", "/home/<you>/.local/bin/decupa"]`.

### Check

```bash
claude mcp list        # decupa: … ✓ Connected
```

## Use it with the Premiere Pro and DaVinci Resolve MCPs

`decupa` doesn't talk to the editors itself — it is designed to run **in the same Claude session** as an editor
MCP. Tested alongside:

- **Premiere Pro** — [leancoderkavy/premiere-pro-mcp](https://github.com/leancoderkavy/premiere-pro-mcp)
  (also available as a Claude Desktop extension)
- **DaVinci Resolve** — [hiteshK03/davinci-resolve-mcp](https://github.com/hiteshK03/davinci-resolve-mcp)

Register them in the same client (Claude Code user scope or Claude Desktop), open the editor, and ask in plain
language. There are two ways to hand off a cut.

### A. Timeline file (most robust, works for both)

`export_timeline(plan, "D:\\Edits\\assembly_v1.xml")` writes an FCP7 XML (or `format="edl"` for CMX3600) with
frame-accurate in/out, linked audio and your `annotate` notes as clip markers.

- **Resolve:** `import_timeline_from_file(file_path="D:\\Edits\\assembly_v1.xml")`
- **Premiere:** `import_media(file_paths=["D:\\Edits\\assembly_v1.xml"])` — Premiere turns the XML into a sequence
  (or File › Import manually).

### B. Clip by clip through the editor MCP

`nle_cut_list(plan)` returns, per clip: `in_frame`, `out_frame_exclusive`, `end_frame_inclusive`, `in_sec`,
`out_sec`, source timecodes, `record_frame` and the path the editor sees.

- **Resolve:** `import_media([...])` → `insert_to_timeline(clip_name, track_index=1, record_frame, start_frame=in_frame, end_frame=end_frame_inclusive)`
- **Premiere:** `import_media([...])` → `add_to_timeline(item_id, track_index=0, start_seconds=<record time>)` →
  `trim_clip(node_id, new_out_seconds=out_sec)` then `trim_clip(node_id, new_in_seconds=in_sec)` (one edit point
  per call; tracks are 0-based). For many clips, route A is simpler.

### The cut plan

Both hand-offs take the same plan:

```json
{
  "name": "assembly v1",
  "fps": "30000/1001",
  "clips": [
    {"path": "D:\\Shoot\\MVI_7965.MP4", "in": 17.5, "out": 25.0, "snap": "speech", "label": "sc3 pt3 take 3"},
    {"path": "D:\\Shoot\\MVI_7984.MP4", "in": 3.0,  "out": 9.0,  "snap": "picture", "label": "reaction"},
    {"path": "D:\\Shoot\\MVI_7975.MP4", "in_frame": 300, "out_frame": 481}
  ]
}
```

`in`/`out` are seconds (snapped to frames, optionally with `snap`); `in_frame`/`out_frame` are exact source frames
(out is exclusive). `fps` defaults to the first clip's.

## A typical session

> Index `D:\Shoot\Day1`. Find the best take of each scene (usually the last full one) and confirm by looking at
> the frames. Then log the good b-roll without dialogue with a short description of each. Build an
> "assembly v1" timeline in Resolve with the best takes in scene order, plus a bin of the b-roll.

What happens under the hood:

1. `index_folder` → `index_status` until done.
2. `scenes(folder)` → ranked takes per scene; `contact_sheet` on the winner and the runner-up; `timeline_strip`
   on the line delivery when it's close.
3. `broll(path)` on clips without slate/dialogue → `contact_sheet` of each window → `annotate` with what's in it.
4. `snap_cut` on the chosen ranges → `export_timeline` (or `nle_cut_list`) → Resolve MCP imports / inserts.

Short prompts that work well:

- *"Show me a contact sheet of A003 from 10 to 40 s."*
- *"Which take of scene 12 has the cleanest delivery? Compare the last two with timeline strips."*
- *"Where does she say 'I'm from the play too'? Search all clips."*
- *"Find steady, sharp b-roll longer than 3 s in this folder and describe each one."*

## Footage logs on disk (`.md` next to every clip)

Indexing writes, **next to each video**:

- `C0025.md` with:
  - file, camera, timecode, rotation;
  - **colour space**;
  - audio;
  - 👁 scene descriptions (written by Claude after *looking*, via `annotate`);
  - takes with verdicts (✅ best / ✗ the speaker criticised it);
  - slate;
  - usable b-roll stretches;
  - the transcript with timecode.
- `_decupa/C0025.jpg`: a contact sheet linked from the `.md`.
- `_DECUPA.md`: one table of every clip in the folder, with live progress while indexing runs.

Any agent or person can read these files and understand the footage without analysing it again. Text you write
outside the `<!-- decupa:auto -->` markers is never overwritten.

## Progress you can see

Indexing runs in its own process. On Windows + WSL it is launched through a hidden `wsl.exe`, so it keeps going when
Claude Desktop, Premiere or the panel close. WSL shuts down when no Windows process is attached to it.


`index_folder` / `index_media` / `sync_multicam` stream progress to the client: percentage, current clip and step,
speed (× real time) and minutes left. The same line is shown on top of `_DECUPA.md` and in `index_status`.

## Colour space (log vs Rec.709)

Each clip gets a camera and a colour profile:

- **Metadata first**: Sony `CaptureGammaEquation`, HLG/PQ transfer tags.
- **Then the image**: log has a hard raised black floor (S-Log3 ≈ 9.5 %, C-Log3 ≈ 12.5 %), highlights that don't
  clip and low saturation. Rec.709 reaches true black and saturates.

The result looks like *"likely S-Log3 (Sony ILCE-7M3)"* or *"likely C-Log3 (Canon EOS R6)"*. A flat, washed-out
image in log is expected: it is **never** treated as a defect. The grade (LUT + correction) belongs to the editor,
in the NLE.

## Multicam sync without PluralEyes

`sync_multicam(cameras={"CAM A": folder, "CAM B": folder}, external_audio=[recorder files])` aligns everything by
audio, the way PluralEyes does:

1. **Envelope.** Every file is reduced to an 8 kHz onset envelope. The envelope is robust across different mics
   (camera on-board mic vs board feed).
2. **Graph placement.** The anchor is the longest recorder file, else the longest clip. The most confident match is
   placed next, and so on until every clip is on the timeline.
3. **Refinement** on the waveform (sub-millisecond).
4. **Transcript fallback** when two mics sound too different: it aligns what is *said*.
5. **Recorder clock drift.** The offset is measured every 30 s on long clips with a robust slope fit (ppm). A
   corrected copy is written to `decupa_drift_corrected/<name>_drift_corrected±X.wav`, the same convention as
   PluralEyes.
6. **Export.** An FCP7 XML in the PluralEyes layout: **"Synced Sequence"**, one video track per camera, stereo pairs
   per camera, the recorder on the last pair, one bin per camera. Premiere and Resolve both import it.

Then `multicam_monitor` shows every angle side by side over a stretch, to pick angles by looking.

Validated two ways:

- **Synthetic test with known answers**: drift measured at 20.1 ppm (truth 20), clip positions within 0.1–3 ms.
- **A real 1h53 concert** (2 cameras at 29.97 + 23.976, board recording), compared with the PluralEyes sync of the
  same footage:
  - all 31 clips that share audio with the rest land **within one frame** of PluralEyes (median 6.8 ms);
  - drift is −11.6 ppm, corrected file `…+0.079.wav`; PluralEyes wrote `…+0.078.wav`.

  Four clips share no audio with anything (recorded before the board started, or 1.4 s long). PluralEyes parks those
  at the start or end of the timeline. decupa places them by the **camera clock**, calibrated on that camera's synced
  clips, and labels them approximate.

Proxies (`Proxy/` or `Proxies/` next to the clips) are used to read audio and pictures whenever their duration
matches the original. Everything decupa writes still points at the original media.

## From card to selects: ingest, selects, review loop

| Step | Tool | |
| --- | --- | --- |
| Offload | `ingest_card` | Verified copy (xxHash64 while copying, destination read back) into `BRUTOS/<camera>`, `BRUTOS/00 AUDIO`, `BRUTOS/03 FOTOS`, `#projeto`, `#trilhas`. Sony XML sidecars travel with their clips. Never overwrites (a new card with the same names goes to `CARD_n`), never touches the card. Writes `_ingest.md`, then starts indexing. |
| Selects | `make_selects` | Built in the NLE before any cut: **SELECTS — falas** (best take of every repeated line, alternates in the marker), **falas únicas** (interview-like material), **b-roll**, plus a bin of every clip with markers (✅ / ✗ / 👁). |
| Review loop | `compare_with_nle`, `adopt_nle_timeline` | Export the current timeline from the NLE (Premiere `export_as_fcp_xml`, Resolve `export_timeline FCP_7_XML`). decupa lists what you trimmed, moved, removed and added, plus your **markers as notes**. Your timeline becomes the new base version, frame-exact, and the next version is imported as a new sequence. |

## Client profiles

`client_profile` / `save_client_profile` keep one `<client>.md` per client in `$DECUPA_CLIENTS` (default
`D:\Documentos\decupa\clientes`). The file is readable by people and agents, with a JSON block decupa reads. It
holds:

- vocabulary;
- formats;
- end card;
- music;
- captions;
- structure;
- notes and past jobs;
- **house style**, measured from the client's finished Premiere sequences (`learn_from_prproj`): typical duration,
  speech shot length, b-roll length and coverage, caption block length, end card.

The next job for that client starts from the profile, and the intake shrinks to "anything different this time?".

## QC before export

`qc_timeline(nle_xml, platform)` checks the timeline as it is in the NLE. It reports and changes nothing:

- flash frames, black gaps, overlaps;
- offline media, disabled clips, clips past the end of their media;
- the same take used twice, jump cuts;
- speech level jumps between cuts, clipped source audio;
- size and duration vs the platform (reels / tiktok / shorts / feed / youtube).

Each issue comes with a timecode, ready to drop as a marker.

## GPU decoding

If an ffmpeg with CUDA is present (`$DECUPA_FFMPEG_GPU` or `~/.local/opt/ffmpeg-*/bin/ffmpeg`, e.g. a BtbN static
build), picture analysis decodes on NVDEC and downscales on the GPU. On 4K H.264 that is ~2× faster than CPU (4.8×
vs 2.5× real time on an RTX 3060). It falls back to the CPU automatically. NVDEC can't decode HEVC/H.264 4:2:2 (e.g.
Canon 10-bit 4:2:2): for those, proxies are the fast path.

## Panel inside Premiere

`python -m decupa.panel install` adds **Window › Extensions › decupa**, a chat with Claude Code inside Premiere:

- It runs `claude -p` in the job folder with the same MCP servers (decupa, Premiere, Resolve), and the session
  continues between messages.
- Every message carries the Premiere context: project, active sequence, playhead, selected clips.
- One-click actions: analyse BRUTOS · selects · read my notes · QC · status.
- Tools are limited to the MCP servers and file reading (no shell). Claude proposes and creates new sequences; it
  never overwrites yours.

Requires Claude Code installed and logged in on the same machine.

## Overview after indexing

When a whole job is indexed, `project_digest` condenses every log: totals, cameras, colour, minutes of speech,
frequent words, and what is said or seen per clip. Claude reads it, writes `<job>/_VISAO_GERAL.md` with
`save_project_overview`, and asks two basics: approximate final duration, and soundtrack (a specific track or a
mood/genre). It then proposes 2–4 directions grounded in the material. You pick one or explain your own idea. The
Premiere panel offers this by itself when the progress bar completes.

## Motion-designer on top of the edit

The motion-designer Claude Code skill's core (layout, platform safe zones, `Captions()`,
`EndCard()`, easing) renders **overlay assets** for the timeline. The background is transparent; PNG frames with
alpha are encoded as **ProRes 4444** (Premiere and Resolve keep the alpha).

| Tool | |
| --- | --- |
| `caption_sentences` | Step 1: the edit's speech as sentences. Claude fixes **grammar only** (agreement, regência, crase, punctuation) and keeps the speaker's words. Corrected words are laid onto the spoken words' timings, and approved corrections are remembered per clip. |
| `caption_style_preview` | Step 2: one real frame of the edit with each style (social · bold · clean · karaoke · box · minimal). The recommended style is starred: from the client profile, else the format. |
| `captions_srt` | Editable captions (.srt) with the corrections. |
| `caption_overlay` | Step 3: the chosen style over the whole edit, corrected, as ProRes 4444 with alpha. |
| `render_overlay` | Lower thirds, titles, callouts, end cards written against the skill's core. |
| `narration` | ElevenLabs (one take, word timings, credit budget) or edge-tts → `narration.wav`. |
| `motion_reference` | The skill's craft rules (readability, safe zones, captions, narration). |

These are assets you lay on the timeline, not a render of the video. The skill is looked up in
`$DECUPA_MOTION_SKILL` or `~/.claude/skills/motion-designer`.

## Building the timeline (with the Premiere / Resolve MCP)

The deliverable is a **timeline in your NLE**. You review it, adjust it with Claude, then grade, add effects and
export yourself. Start with the **`montar`** prompt (`/mcp__decupa__montar`). It runs a short intake, one question
at a time: type, idea, format, duration, captions, which NLE, **music first**, pace, sound, opening/closing. Then it
logs the footage by looking at it and builds a first version.

| Step | Tool | |
| --- | --- | --- |
| House style | `read_premiere_project` | Reads your finished `.prproj` (read-only): spine shot length, b-roll over speech (count, length, % covered), caption length, end card. |
| Music first | `music_map`, `music_windows` | Tempo (+ half/double time), beats, bars, energy sections, drop, silences, best stretch for the duration. |
| Build | `build_edit` | **V1 spine.** Dialogue keeps its length and its tail stretches to the next beat; other shots are measured in beats. **V2 b-roll runs.** B-roll plays over speech, cut on the beat. **Multicam.** Angles follow the master clock. Each build is a new version, and it warns about jump cuts, gaps and cuts off the beat. |
| Review | `edit_storyboard` | One tile per shot from the **source** frames with crop/rotation applied, plus a bar with beats and the b-roll layer. **No render.** |
| Into the NLE | `export_edit` | FCP7 XML: V1 + V2 (Basic Motion scale/centre/rotation), audio levels per shot, music. Premiere: `import_media`; Resolve: `import_timeline_from_file`. |
| Render | `render_edit` | **Only when the user asks for it** (`authorized_by_user=True`). |

Vertical helpers: `reframe_preview` shows the crop, `reframe_x: "auto"` follows the in-focus subject, and
`set_rotation` fixes cameras shot on their side without metadata.

Names Whisper must get right go in `vocabulary` ("INC Empreendimentos, Juiz de Fora"). They are applied as a
post-correction by similarity, never as a Whisper hint: as a prompt Whisper writes the hint into the transcript, and
as hotwords it drops words.

### What real projects taught it

- **Testimonial reels.** `takes` picks the same hook and CTA takes the editor used (CTA in-point within 20 ms). Two
  rules made that possible:
  - a take the speaker criticises right after ("gaguejei", "desculpa", "mais um") is out;
  - completeness is measured against the consensus line.
- **Multicam concert** (2 cameras at 29.97 + 23.976, a 1h53 board recording). Audio sync lands within one frame of
  PluralEyes for every clip that shares audio, with the same recorder drift correction.

## How it works

One `ffmpeg` decode per clip produces two streams: tiny full-rate frames (frame-accurate hard-cut detection that
ignores objects whipping past the lens) and 4 fps 320 px frames (Laplacian sharpness relative to the clip's own
best focus, clipped highlights/crushed blacks, global motion by phase correlation → static / pan / tilt / shake).
Audio becomes a 20 ms RMS envelope (silences, quietest cut points). Whisper runs with word timestamps, VAD and
`condition_on_previous_text=False` so repeated takes are transcribed instead of "deduplicated".

Everything is cached in `~/.cache/decupa/<file>-<hash>/` (keyed by path + size + mtime): `index.json`,
`words.json`, `q.npy`, `rms_db.npy`, `log.json`.

## Configuration

| Env var | Default | |
| --- | --- | --- |
| `DECUPA_CACHE` | `~/.cache/decupa` | Index cache |
| `DECUPA_WHISPER` | `large-v3-turbo` | faster-whisper model |
| `DECUPA_MLX_MODEL` | `mlx-community/whisper-large-v3-turbo` | mlx-whisper model |
| `DECUPA_BACKEND` | auto | Force `mlx` or `faster-whisper` |

Language defaults to Portuguese (`language="pt"` on `index_media` / `index_folder`); pass `"en"`, `"es"`, … or
`""` to auto-detect. Slate parsing understands Portuguese and English call-outs.

## Limitations

- Cameras without embedded timecode (e.g. Canon `MVI_*.MP4`) start at `00:00:00:00`; frame numbers are still exact.
- One frame rate per exported timeline (mixed-fps plans are refused — make one per fps).
- Rankings are evidence, not verdicts. For performances, look at the winner and the runner-up.
- XML/EDL import has been validated structurally; behaviour may vary between Premiere/Resolve versions —
  issues welcome.

## License

[MIT](LICENSE)
