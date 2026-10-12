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
