# Cláudio, o Editor

> Formerly **decupa**. Renamed in v0.7, once it did far more than logging: it cuts, captions and syncs too. Already
> indexed jobs keep working (see [Coming from Cláudio](#coming-from-claudio-before-v07)).

**An MCP server that lets Claude _see_ and _hear_ your raw footage before it cuts — and delivers the edit as a timeline
in Premiere Pro or DaVinci Resolve, with you in control.**

[Português](README.md)

The Premiere and Resolve MCP servers let an AI *operate* the editor (import, place clips, trim, export). What's missing
is looking at the footage: without that, "the best take" or "a good b-roll of the hands" is a guess from file names.

Cláudio is the missing eyes and ears:

- **What is said** — word-level transcript (Whisper), including the spoken slate ("scene 3, take 2… action… cut").
- **What is seen** — contact sheets, frames and waveform timelines returned as **images**, with timecode.
- **What is usable** — relative focus, exposure, camera motion, repeated takes, takes the speaker criticised, colour
  (log vs Rec.709), silences.

The decision comes from that evidence. Cláudio turns it into **frame-accurate in/out points**, and the timeline goes
into Premiere/Resolve as a **new sequence** for you to review, adjust, grade and export. It **never renders the final
video** unless you ask.

```
card ─► ingest ─► logging (.md next to every clip) ─► overview + questions ─► selects ─► timeline
                                                                                           │
          you review in the NLE (markers = notes) ◄── new version ◄── Cláudio reads your changes ◄┘
                              │
                              └─► QC ─► you finish (grade, effects) and export
```

## What it does

| Step | Tools | |
| --- | --- | --- |
| Offload | `ingest_card` | Verified copy (xxHash64 while copying, destination read back) into the folders of your [profile](#editor-profile) (`brutos/<camera>`, `brutos/audio`, `brutos/fotos`, `projeto`, `trilhas`). Never overwrites: another card with the same names goes to `CARD_n`. Never touches the card. Then indexes. |
| Index | `index_folder`, `index_status` | Runs in its own process (keeps going if the chat or Premiere close), with a progress bar. Uses proxies (`Proxy/` or `Proxies/`) when present, and GPU (NVDEC) or keyframes only for heavy 4K. Writes **`<clip>.md`** + contact sheet next to every video and **`_CLAUDIO.md`** per folder. |
| Understand | `project_digest`, `save_project_overview` | Reads every `.md` and writes **`_VISAO_GERAL.md`**. Asks for the final duration and the soundtrack (a specific track or a mood/genre), then proposes 2–4 directions. |
| Look | `contact_sheet`, `timeline_strip`, `frames`, `reframe_preview`, `multicam_monitor` | Real images of the footage, with TC and the words spoken at that instant. |
| Choose | `scenes`, `takes`, `broll`, `find_phrase`, `annotate` | Best take of every line (usually the last complete one; a take the speaker criticised is out) and usable b-roll stretches. Notes go into the `.md`. |
| Visual search | `find_shot` | Finds shots by description ("hands on the guitar neck", "road at sunset") with CLIP on the CPU; thumbnails come from indexing itself. |
| People | `people`, `name_people`, `find_person` | Who appears (faces) and who speaks (voices), the same person under one name across clips; you confirm the names and they stick ("only Caio's lines"). |
| Describe shots | `describe_shots` | What is seen in each stretch (framing, place, objects, day/night, who appears, what is said), written into each clip's `.md` with no AI. |
| Stereo audio | `stereo_fix` | Audio that plays on one side only (a lav or a single mic on L or R) plays on both; two real mics are kept. Optionally levels the speech. |
| Flatten | `flatten_timeline` | Replaces nested sequences with their own clips, straight on the tracks, to grade take by take. |
| Multicam | `sync_multicam` | Syncs cameras and recorders by audio, replacing PluralEyes: corrects recorder drift and exports an XML in the same layout, with **one label colour per camera** (video, audio and bin) and another for external audio. |
| Selects | `make_selects` | Best-take and b-roll sequences + markers on every clip (✅ best / ✗ criticised / 👁 notes). |
| Build | `music_map`, `music_windows`, `build_edit`, `edit_storyboard`, `export_edit` | Music first (BPM, bars, sections), cuts on the beat, b-roll over speech, a storyboard **without rendering**, FCP7 XML for the NLE. |
| Review | `compare_with_nle`, `adopt_nle_timeline` | Reads the timeline as it is in the NLE: what you changed, and your markers as notes. Your changes become the new base. |
| Captions & overlays | `caption_sentences`, `caption_style_preview`, `captions_srt`, `caption_overlay`, `render_overlay`, `narration` | Grammar fixed without changing the speech, 6 styles with a preview, editable SRT or an alpha overlay (ProRes 4444), lower thirds and titles, ElevenLabs/edge-tts narration. **Songs:** lyrics timed to the isolated vocal (UVR MDX-Net in ONNX, 67 MB model downloaded on first use), one block per sung line, the chorus fixing misheard lines. Uses the motion-designer skill. |
| Finish | `qc_timeline` | Flags flash frames, black gaps, offline media, takes used twice, jump cuts, volume jumps and the wrong format for the platform. It fixes nothing on its own. |
| Remember | `client_profile`, `save_client_profile`, `read_premiere_project` | One profile per client: vocabulary, end card, music, and the house style measured from your finished projects. |

Plus a **panel inside Premiere** (Window › Extensions › Cláudio, o Editor): a chat with Claude Code that knows the project (active
sequence, playhead, selected clips), buttons for every step, and a progress bar.

The buttons are grouped by stage of the job, one colour per group (● = runs with no AI, no credits):

| Stage | Buttons |
|---|---|
| **Preparar** (prepare) | 01 Analisar material ● · 02 Status ● · 03 Áudio estéreo ● · 04 Multicam ● |
| **Entender** (understand) | 05 Visão geral · 06 Descrever planos ● · 07 Ler minhas notas · 08 Aprender com vídeos prontos |
| **Montar** (edit) | 09 Montar com receita · 10 Selects · 11 Corte por quem fala · 12 Recap de evento · 13 Limpar falas ● · 14 SFX nos cortes ● · 15 Trilha sonora · 16 Desaninhar ● |
| **Legendar** (caption) | 17 Transcrição Scribe ● · 18 Legendas · 19 Overlay da legenda revisada · 20 Legenda MOGRT ● |
| **Entregar** (deliver) | 21 QC antes de exportar ● · 22 Registrar no vault |

**Montar com receita** (assemble with a recipe) lists the [recipes](#recipes) in the chat (the client's usual one
already ticked, when its profile says `"receita"`) and assembles following the chosen one. A recipe with
`botao: {"rotulo": "…", "dica": "…"}` in its frontmatter becomes a button of its own in the Montar group, like
**Recap de evento**; the numbers follow the order on screen. **Aprender com vídeos prontos** (learn from finished
videos) is the first step with a new client — see [Learning a client](#learning-a-client-from-finished-videos).

---

## Installation

Cláudio works with three other pieces: **Claude Code** (where you talk) and the **Premiere MCP** and/or the **Resolve
MCP** (which operate the editor). Full step-by-step below.

| | Windows (recommended with an NVIDIA card) | macOS (Apple Silicon) |
| --- | --- | --- |
| Cláudio | inside **WSL** (Ubuntu), on the GPU | native |
| Transcription | faster-whisper on the GPU (CUDA) | mlx-whisper |
| Decoding | NVDEC (optional) or CPU; proxies | VideoToolbox; proxies |
| Premiere / Resolve / Claude Code | on Windows | on the Mac |

> Steps marked **optional** can be skipped. Everything else is required.

### Claude Desktop chat or the Premiere panel?

Cláudio works in both. The only difference is **where you talk**:

| | Claude Desktop chat | Cláudio panel in Premiere |
| --- | --- | --- |
| What runs underneath | Claude Desktop itself | **Claude Code** (`claude -p`) |
| Where to register Cláudio, Premiere and Resolve | `claude_desktop_config.json` (or a `.mcpb` bundle) | `claude mcp add -s user ...` |
| claude.ai connectors | yes | yes |
| Advantage | nicer conversation, paste images | next to the timeline, quick buttons, progress bar |

The panel needs Claude Code because a Premiere extension cannot open a conversation in Claude Desktop: Desktop does
not accept messages from other programs. Claude Code has a mode for that (`claude -p`), so the panel is effectively
Claude Code inside Premiere. That is why it sees the MCPs registered **in Claude Code**, not the Desktop ones. If you
only use the Desktop chat, register the MCPs there and skip the panel.

The two conversations are separate. What connects them is what lives on disk: the `.md` next to each clip,
`_CLAUDIO.md`, `_VISAO_GERAL.md` and the index in `_claudio/`.

### Windows

#### 1. Windows programs

In **PowerShell**:

```powershell
winget install Git.Git
winget install OpenJS.NodeJS.LTS
winget install Gyan.FFmpeg
```

- **Node.js 20.19+**: used by the Premiere MCP.
- **Python 3.12** from the official installer ([python.org](https://www.python.org/downloads/windows/)), with *Add
  python.exe to PATH* ticked: used by the Resolve MCP.
- An up-to-date **NVIDIA** driver (the Windows driver also serves WSL).

#### 2. Claude Code

```powershell
irm https://claude.ai/install.ps1 | iex
```

Run `claude` once and log in with your Anthropic account (`/login`). Docs:
[docs.claude.com/claude-code](https://docs.claude.com/en/docs/claude-code/overview).

#### 3. WSL with Ubuntu

In PowerShell **as administrator**:

```powershell
wsl --install -d Ubuntu
```

Restart if asked, open **Ubuntu** from the Start menu and create your user.

#### 4. Cláudio (inside Ubuntu)

```bash
sudo apt update && sudo apt install -y ffmpeg git curl
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc
uv tool install --python 3.12 "claudio[cuda,motion] @ git+https://github.com/bernardotraad/claudio.git"
claudio-doctor --fix-browser
```

- `[cuda]` brings the GPU transcription libraries; `[motion]` brings the browser used for captions and overlays.
- `claudio-doctor` checks everything and tells you what is missing. Run it again at the end.
- To update later: `uv tool upgrade claudio`.

**Optional — GPU decoding (NVDEC):** about 2× faster on 4K H.264. NVDEC can't decode HEVC 4:2:2 (e.g. Canon 10-bit);
use proxies for those files.

```bash
mkdir -p ~/.local/opt && cd ~/.local/opt
curl -L -o ff.tar.xz https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz
tar xf ff.tar.xz && rm ff.tar.xz && cd ~
```

**Optional — more cores and memory for WSL:** create `C:\Users\<you>\.wslconfig` with a `[wsl2]` section containing
`processors=<cores>` and `memory=<GB>GB`, then run `wsl --shutdown`.

#### 5. Register Cláudio with Claude Code (PowerShell)

Replace `<user>` with your Ubuntu user:

```powershell
claude mcp add -s user claudio -- C:\Windows\System32\wsl.exe -d Ubuntu -- /home/<user>/.local/bin/claudio
```

`D:\...` paths work as they are: Cláudio translates them for WSL and returns everything (XML, `.md`) with Windows paths.

#### 6. Premiere Pro MCP — [leancoderkavy/premiere-pro-mcp](https://github.com/leancoderkavy/premiere-pro-mcp)

Premiere 2020–2026. With **Premiere closed**, in PowerShell:

```powershell
npm install -g premiere-pro-mcp
premiere-pro-mcp --install-cep
claude mcp add -s user premiere-pro -- cmd /c premiere-pro-mcp
```

Open Premiere with a project → **Window › Extensions › MCP for Adobe Premiere Pro** → status *Running*. To check:
`premiere-pro-mcp --doctor`. The name `premiere-pro` matters: it is what the Cláudio panel expects.

- If you get *server disconnected*, register it in legacy protocol mode:
  `claude mcp add -s user -e PREMIERE_MCP_PROTOCOL_MODE=legacy premiere-pro -- cmd /c premiere-pro-mcp`.
- Already using the Premiere MCP in Claude Desktop (`.mcpb` bundle)? Keep it. The Cláudio panel uses **Claude Code**,
  which needs the registration above (a different client, so nothing is duplicated).

#### 7. DaVinci Resolve MCP — [hiteshK03/davinci-resolve-mcp](https://github.com/hiteshK03/davinci-resolve-mcp)

Resolve 18+ (works on Free). In PowerShell, in the folder where you keep projects:

```powershell
git clone https://github.com/hiteshK03/davinci-resolve-mcp.git
cd davinci-resolve-mcp
python -m venv venv
venv\Scripts\pip install -r requirements.txt "mcp<2"
copy src\CursorBridge.py "$env:APPDATA\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility\"
claude mcp add -s user davinci-resolve -- "$PWD\venv\Scripts\python.exe" "$PWD\src\resolve_mcp_bridge.py"
```

`mcp<2` is needed because the server breaks with the 2.x `mcp` package. Every time you open Resolve: **Workspace ›
Scripts › Utility › CursorBridge** (the console shows *Bridge is running*).

#### 8. The Cláudio panel in Premiere (inside Ubuntu)

```bash
claudio-panel
```

It installs the panel in Windows Premiere and allows unsigned extensions. Restart Premiere → **Window › Extensions ›
Cláudio, o Editor**.

#### 9. Optional

- **motion-designer skill** (styled captions, overlays, narration): `claudio-doctor --fix-skill` **inside Ubuntu**.
  It is Gabriel Greco's ([gabrielgreco1/skills](https://github.com/gabrielgreco1/skills)) and is downloaded from there,
  at the version Cláudio was tested with; `--fix-skill --force` replaces a hand-installed copy (the old one goes to
  `~/.claude/skills-backup`).
- **ElevenLabs** (more natural narration): save the key in `~/.config/elevenlabs/api_key` inside Ubuntu. Without it,
  narration uses free edge-tts.
- **Claude Desktop**: in `claude_desktop_config.json`, use the same command as step 5:
  `"command": "C:\\Windows\\System32\\wsl.exe", "args": ["-d", "Ubuntu", "--", "/home/<user>/.local/bin/claudio"]`.

#### 10. Check

```bash
claudio-doctor
```

In PowerShell, `claude mcp list` should show `claudio`, `premiere-pro` and `davinci-resolve` as *Connected*.

---

### macOS (Apple Silicon)

#### 1. Programs

Install [Homebrew](https://brew.sh), then in Terminal:

```bash
brew install ffmpeg uv node git
```

For the Resolve MCP, install **Python 3.12 from [python.org](https://www.python.org/downloads/macos/)** (not Homebrew's).

#### 2. Claude Code

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

Run `claude` and log in (`/login`).

#### 3. Cláudio

```bash
uv tool install --python 3.12 "claudio[motion] @ git+https://github.com/bernardotraad/claudio.git"
claudio-doctor --fix-browser
claude mcp add -s user claudio -- "$HOME/.local/bin/claudio"
```

On the Mac, transcription uses **mlx-whisper** (installed with Cláudio) and decoding uses **VideoToolbox**. The first
transcription downloads the `whisper-large-v3-turbo` model (~1.6 GB).

> On the Mac, always register MCP servers with **absolute paths**: Premiere opened from the Dock doesn't see Terminal's
> PATH, and the Cláudio panel runs from it.

#### 4. Premiere Pro MCP — [leancoderkavy/premiere-pro-mcp](https://github.com/leancoderkavy/premiere-pro-mcp)

With **Premiere closed**:

```bash
npm install -g premiere-pro-mcp
premiere-pro-mcp --install-cep
claude mcp add -s user premiere-pro -- "$(which premiere-pro-mcp)"
```

Open Premiere → **Window › Extensions › MCP for Adobe Premiere Pro** → *Running*. To check: `premiere-pro-mcp --doctor`.
If you get *server disconnected*, register it with `-e PREMIERE_MCP_PROTOCOL_MODE=legacy`. If Premiere and Resolve are
already in Claude Desktop, register them in Claude Code too (that is what the panel uses).

#### 5. DaVinci Resolve MCP — [hiteshK03/davinci-resolve-mcp](https://github.com/hiteshK03/davinci-resolve-mcp)

```bash
cd ~/Documents
git clone https://github.com/hiteshK03/davinci-resolve-mcp.git
cd davinci-resolve-mcp
/usr/local/bin/python3.12 -m venv venv
venv/bin/pip install -r requirements.txt "mcp<2"
cp src/CursorBridge.py "$HOME/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/"
claude mcp add -s user davinci-resolve -- "$PWD/venv/bin/python" "$PWD/src/resolve_mcp_bridge.py"
```

Every time you open Resolve: **Workspace › Scripts › Utility › CursorBridge**.

#### 6. The Cláudio panel in Premiere

```bash
claudio-panel
```

Restart Premiere → **Window › Extensions › Cláudio, o Editor**.

#### 7. Optional and check

- **motion-designer skill**: `claudio-doctor --fix-skill` (downloaded from its author's repository; `--force`
  replaces a hand-installed copy).
- **ElevenLabs**: save the key in `~/.config/elevenlabs/api_key`.
- **Claude Desktop**: `"command": "/Users/<you>/.local/bin/claudio"`.
- To check: `claudio-doctor` and `claude mcp list`.

---

## How to use it

1. Set up the project in Premiere/Resolve and **save the `.prproj` inside the job's project folder**
   (`<job>/projeto/`, or the name in your [profile](#editor-profile)): the panel finds the footage folder from there.
2. Open the **Cláudio** panel and click **Analisar material**. The bar shows the progress. You can close the panel,
   Claude Desktop or Premiere: indexing keeps going.
3. When it finishes, the panel offers the **overview**: what the project is, the questions (duration, soundtrack) and
   possible directions. Pick one or explain your own idea.
4. **Selects** → check the take and b-roll choices in the NLE.
   **Multicam** (with more than one camera or an external recorder) → Cláudio proposes the folder → camera mapping and
   the colours, asks, syncs by audio, checks the angles side by side and delivers the "Synced Sequence" with each camera
   in its own colour.
5. Ask for the edit. It comes in as a new sequence. Review it, change anything, and leave **markers** with notes.
6. **Ler minhas notas** (read my notes) → Cláudio compares with what it delivered, adopts your changes and applies your
   notes in a new version.
7. **Legendas** (captions) → corrected sentences (speech → caption table), a preview of the 6 styles with the
   recommended one starred, an editable SRT and/or an alpha overlay.
8. **QC antes de exportar** (QC before export) → a list of issues with timecode. You finish (grade, effects) and export.

The panel conversation is saved next to the project (`<project>.claudio-chat.json`) and comes back when you reopen
Premiere: the messages reappear and Claude picks up where it left off. **＋** starts a new conversation.

You can also do everything from the Claude Code terminal with the guided prompt `/mcp__claudio__montar`: it picks the
[recipe](#recipes) for the kind of project and follows its steps.

**Cláudio's rules:** you are always in control. It proposes before changing anything, never overwrites your sequence (it
makes a new one), never undoes your manual changes, and only renders the final video if you ask. Log footage (S-Log,
C-Log, BMD Film) looks flat on purpose: grading is yours, in the NLE.

### Files it creates

| Where | What |
| --- | --- |
| next to every video | `<clip>.md` (technical, colour, scenes, takes, transcript) and `_claudio/<clip>.jpg` |
| in every footage folder | `_CLAUDIO.md` (folder table + progress) and `_claudio/progress.json` |
| at the job root | `_VISAO_GERAL.md`, `_ingest.md` / `_ingest.json` |
| `projeto/` (profile name) | XMLs (selects, sync, edit versions), `claudio_overlays/` (captions, overlays, narration) and `<project>.claudio-chat.json` (panel chat) |
| the profile's `clientes`, else `<vault>/trabalho/clientes/`, else `~/Documents/Claudio/clientes` | client profiles (`<client>.md`, with the vault frontmatter) |
| `~/.cache/claudio/` | indexes (transcript, metrics, audio). Deleting it only forces a new analysis. |

Text you write in the `.md` files outside the `<!-- claudio:auto -->` markers is never overwritten.

## Validation

- **Multicam:** on a 1h53 concert (2 cameras at 29.97 and 23.976 fps, plus a board recorder), the 31 clips that share
  audio landed **within one frame** of the PluralEyes sync, with the same drift (`+0.079` vs `+0.078` s). PluralEyes
  parks the clips that share no audio at the start or end of the timeline; Cláudio places them by the camera clock and
  marks them approximate.
- **Takes:** on broker reels, it picked the same hook and CTA takes as the editor (CTA in-point within 20 ms).
- **Beat:** 7 ms median error on a 128 BPM click track.
- **Synthetic multicam:** measured drift 20.1 ppm (truth 20), positions within 0.1–3 ms.

## Configuration

| Variable | Default | |
| --- | --- | --- |
| `CLAUDIO_CACHE` | `~/.cache/claudio` | indexes |
| `CLAUDIO_PROFILE` | `~/.config/claudio/perfil.json` | [editor profile](#editor-profile) |
| `CLAUDIO_CLIENTS` | the profile's `clientes` | client profiles |
| `CLAUDIO_VAULT` | the profile's `vault` | Obsidian vault that holds the profiles |
| `CLAUDIO_RECIPES` | `~/.config/claudio/receitas` | your own [recipes](#recipes) |
| `CLAUDIO_BENCHMARK` | `~/.config/claudio/benchmark.json` | the [recipe benchmark](#recipe-benchmark) |
| `CLAUDIO_MOTION_SKILL` | `~/.claude/skills/motion-designer` | motion skill |
| `CLAUDIO_WHISPER` / `CLAUDIO_MLX_MODEL` | `large-v3-turbo` | transcription model |
| `CLAUDIO_FFMPEG_GPU` | `~/.local/opt/ffmpeg-*/bin/ffmpeg` | ffmpeg with CUDA |
| `CLAUDIO_GPU` / `CLAUDIO_FAST` | `1` | `0` turns off NVDEC / keyframe mode |
| `CLAUDIO_STT` | `whisper` | `elevenlabs` transcribes with ElevenLabs Scribe (paid, word timings within 1–2 frames of the voice) |
| `CLAUDIO_SCRIBE_MODEL` | `scribe_v2` | Scribe model |

The old `DECUPA_*` variables still work. Any of them can also go in `~/.config/claudio/settings.json`, lowercase and
without the prefix (`{"stt": "elevenlabs"}`) — handier, since the MCP and the panel run through `wsl.exe`.

Word edges are always put on the voice measured in the audio (free, local): captions don't come in over the silence
before speech and cuts don't clip a word's tail. With Scribe on, "Analisar material" redoes only the transcript of
clips Whisper already did (cached per clip: never paid twice); songs stay on Whisper + the isolated vocal; any API
error falls back to Whisper and is noted as `stt_error` in the index.

### Editor profile

Cláudio spells no folder name: it reads `~/.config/claudio/perfil.json`. Without that file it uses the neutral names
below. Write only what changes; `claudio-doctor` shows the profile in use.

```json
{
 "pastas": {
  "brutos": "BRUTOS", "audio": "BRUTOS/00 AUDIO", "fotos": "BRUTOS/03 FOTOS",
  "projeto": "#projeto", "trilhas": "#trilhas", "legendas": "#legendas",
  "overlays": "#projeto/claudio_overlays", "backups": "claudio_backups"
 },
 "nle": "premiere",
 "vault": "~/vault",
 "clientes": "~/vault/trabalho/clientes"
}
```

| Key | Default | |
| --- | --- | --- |
| `pastas.brutos` | `brutos` | camera footage (`<brutos>/<camera>/`) |
| `pastas.audio` / `pastas.fotos` | `brutos/audio` / `brutos/fotos` | external recorders / photos |
| `pastas.projeto` | `projeto` | NLE project, XMLs, selects, sync |
| `pastas.trilhas` | `trilhas` | music |
| `pastas.legendas` | `legendas` | `.srt`, caption overlays, MOGRT plans |
| `pastas.overlays` | `projeto/claudio_overlays` | lower thirds, titles, end cards |
| `pastas.backups` | `claudio_backups` | the panel's sequence backups, next to the NLE project |
| `nle` | — (asks once) | `premiere` or `resolve`: Claude gets only that NLE's rules (how to import the XML, how to read the current timeline, what never to do) |
| `vault` | — | an Obsidian vault: client profiles follow its rules |
| `clientes` | `<vault>/trabalho/clientes` or `~/Documents/Claudio/clientes` | client profiles |

Folders are relative to the job folder (`/` separates levels; `~` works in `vault` and `clientes`). Folders starting
with `#` and the profile's work folders are never indexed as footage. The panel reads the profile when it opens: after
changing the file, reopen the panel.

### Recipes

Each kind of project is a recipe: a Markdown file with the brief questions, the steps, the taste rules and the
defaults (format, length, pace). Claude reads it through the `recipe` tool only when assembling; the rules common to
every edit (`_comum.md`: delivering to the NLE, rounds of notes, captions, QC) come with it. The recipes are in
Portuguese, like the panel.

| Recipe | For |
| --- | --- |
| `depoimento` | one or more people talking to the camera, with b-roll, for social (vertical) |
| `depoimento-horizontal` | testimonial or interview in 16:9 for YouTube, a site or a corporate video, with lower thirds |
| `recap-evento` | aftermovie: the best moments on the beat of a song |
| `beat-edit` | picture and music only, no speech |
| `show-multicam` | a show or music video shot with several cameras |
| `curta-narrativo` | a scripted scene with spoken slates |
| `podcast` | a conversation with one camera and one mic per person: cut by who speaks, cleanup, chapters |
| `tutorial` | a lesson, screen recording or bench: the best take of each step, zoom on the region that matters |
| `cortes-de-video-longo` | several vertical shorts taken from a podcast, talk or live |

To add a recipe of your own (or replace one above), put a `.md` in `~/.config/claudio/receitas/` with the same format:

```markdown
---
nome: podcast
titulo: Podcast em vídeo
quando: conversa gravada com uma câmera por pessoa e um microfone cada
padroes: {"aspect": "16:9", "pace": "medium"}
verificar: {"plataforma": "youtube"}
entregas: ["timeline"]
ferramentas: ["sync_multicam", "auto_cut_multicam", "clean_speech", "qc_timeline"]
---

## Brief
1. …

## Etapas
1. …

## Regras de gosto
- …
```

Values starting with `[` or `{` are JSON. Each `## ` heading is a section that can be read on its own
(`recipe("depoimento", "legendas")`). `botao` (optional) puts the recipe on a panel button; `verificar` (optional) says
what the benchmark measures on the result (`plataforma`, `gancho_s`). A client profile can name the recipe that client
usually orders (`"receita"`).

### Learning a client from finished videos

The first step with a new client: point at a folder of videos you already delivered (from anywhere, even another
editor's). **Without AI** (button **08 Aprender com vídeos prontos** › *Medir*, or `claudio-run learn`), Cláudio
measures the newest ones — format, length, cut every how many seconds, how much is speech, first word, end card, how
long the speaker's face is on screen, music under the voice and burned-in captions (those two are estimates, no OCR),
loudness — and writes the client's recipe in `~/.config/claudio/receitas/<client>.md`: the numbers on top and the
steps of the closest built-in recipe below, to adjust. The transcript for measuring is always the local Whisper
(free), even with Scribe on. **With AI** (*Medir e refinar com IA*, or the `/mcp__claudio__aprender` prompt), Claude
also looks at the videos and rewrites the recipe with the structure, the tone and what the client expects to receive,
and asks only what can't be seen. If the client's recipe already exists, the new one becomes a dated draft beside it;
the profile's choice doesn't change without you.

### Recipe benchmark

To know whether a recipe (or a change to it) delivers right, point each recipe at a finished job of yours in
`~/.config/claudio/benchmark.json` (outside the repository):

```json
{"casos": [{"receita": "depoimento", "nota": "client X, Ana", "xml": "D:\\job\\#projeto\\Ana.xml", "duracao": [25, 45]}]}
```

```bash
claudio-run bench '{}'
```

With no AI and no credits it checks, on the XML: the recipe's format, length, QC with no errors (flash frames, black
gaps, offline media…), loudness within ±3 dB of the platform target, nothing nested and, for recipes with speech, the
first word inside the hook limit. Each run goes to `benchmark-results.jsonl`, next to it, to compare with earlier ones.
`xml` can be a folder (the newest .xml counts); `duracao`, `plataforma` and `gancho_s` in the case override the recipe
(`null` turns a measure off).

## Limitations

- Cameras without timecode (e.g. Canon `MVI_*`) start at `00:00:00:00`; frames stay exact.
- NVDEC can't decode HEVC/H.264 4:2:2. Cláudio then analyses keyframes only, or you can use proxies.
- One frame rate per exported timeline (with mixed fps, make one timeline per fps).
- In Premiere, **import the XMLs by hand** (File › Import / Ctrl+I). Importing an XML through the Premiere MCP's
  `import_media` crashes Premiere; manual import works. Cláudio hands you the XML path, and the panel shows an
  **Importar no Premiere** button that saves the project and imports one XML at a time with Premiere's dialogs shown.
  Regular media (`.mov` overlays, `.wav`, `.srt`) can go through the MCP.
- Take ranking is evidence, not a verdict. For performances, look at the winner and the runner-up.

## Spending little AI

Cláudio runs **on your machine**: transcription, picture, sync, visual search, faces, voices, captions and QC use no AI
credits. What costs is the conversation with the model (Claude), and each message resends the history.

- **"sem IA" buttons** (green dot in the panel, ● in the table above): Analisar material, Status, Áudio estéreo,
  Multicam, Descrever planos, Limpar falas, SFX nos cortes, Desaninhar, Legenda MOGRT and QC run straight in Cláudio
  through `claudio-run`, with no model at all.
- **Model picker** in the panel: Econômico (Haiku) for simple questions, Padrão (Sonnet) day to day, Máximo (Opus)
  only for hard editing calls.
- **＋ new conversation** when the subject changes: a long conversation costs more on every message.
- From a terminal: `claudio-run <status|index|stereo|flatten|qc|sync|learn|bench> '{json}'` (`claudio-run` alone lists them all).

## Tests

`pytest -q` runs in seconds with no GPU and no Premiere, on synthetic media: multicam sync and colours, nested
sequence flattening, beat grid, paths with `#`/spaces/apostrophes, alpha overlays, the panel (300 px layout, no
overlaps, reply HTML escaped, change detector), recipes, benchmark and learning. The speech tests use
`tests/data/fala_pt.flac` (a synthetic voice, the same line twice) and Whisper `tiny` on the CPU: word timing,
repeated takes, the hook, phrase captions. The tests never use the paid Scribe, even when it is on in
`settings.json`; `CLAUDIO_TEST_WHISPER=0` skips the speech ones. It also runs on every push in GitHub Actions (the
model is cached).

## License

[MIT](LICENSE)

### Coming from decupa (before v0.7)

- `_decupa/` folders become `_claudio/` the next time a job is opened, with no reindexing. The `.md` links are updated
  too, and `_DECUPA.md` is replaced by `_CLAUDIO.md`.
- The `~/.cache/decupa` cache moves to `~/.cache/claudio` (a link stays in the old place).
- Folders already handed to Premiere (`decupa_overlays/`, `decupa_drift_corrected/`) are left alone so project links
  don't break.
- To switch installs: `uv tool uninstall decupa`, install `claudio` as above and register it again in Claude Code
  (`claude mcp remove decupa -s user`, then `claude mcp add ... claudio ...`). Run `claudio-panel`: it removes the old
  panel.
