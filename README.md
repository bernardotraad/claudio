# decupa

**Um servidor MCP que faz o Claude _ver_ e _ouvir_ o seu material bruto antes de montar — e entregar a montagem como
timeline no Premiere Pro ou no DaVinci Resolve, com você no controle.**

[English](README.en.md)

Os MCPs do Premiere e do Resolve deixam uma IA *operar* o editor (importar, colocar clipes, aparar, exportar). O que
falta é olhar o material: sem isso, "o melhor take" ou "um b-roll bom das mãos" vira chute pelo nome do arquivo.

O `decupa` (de *decupagem*) é esse par de olhos e ouvidos:

- **O que é dito** — transcrição por palavra (Whisper), incluindo a claquete falada ("cena 3, take 2… ação… corta").
- **O que se vê** — contact sheets, frames e timelines com waveform devolvidos como **imagens**, com timecode.
- **O que serve** — foco relativo, exposição, movimento de câmera, takes repetidos, falas criticadas pelo próprio
  falante, cor (log × Rec.709), silêncios.

A decisão sai disso, o `decupa` transforma em **in/out exatos por frame**, e a timeline entra no Premiere/Resolve como
uma **sequência nova** para você revisar, ajustar, colorir e exportar. Ele **nunca renderiza o vídeo final** sem você
pedir.

```
cartão ─► ingest ─► decupagem (.md ao lado de cada clipe) ─► visão geral + perguntas ─► selects ─► timeline
                                                                                                    │
              você revisa no NLE (marcadores = notas) ◄── nova versão ◄── decupa lê o que você mudou ◄┘
                                │
                                └─► QC ─► você finaliza (cor, efeitos) e exporta
```

## O que ele faz

| Etapa | Ferramentas | |
| --- | --- | --- |
| Descarregar | `ingest_card` | Cópia verificada (xxHash64 copiando e relendo o destino) para `BRUTOS/<câmera>`, `00 AUDIO`, `03 FOTOS`, `#projeto`, `#trilhas`. Nunca sobrescreve (outro cartão com os mesmos nomes vai pra `CARD_n`) e nunca mexe no cartão. Depois indexa. |
| Indexar | `index_folder`, `index_status` | Em processo próprio (segue mesmo com o chat ou o Premiere fechados), com barra de progresso. Usa proxies (`Proxy/` ou `Proxies/`) quando existem, e GPU (NVDEC) ou só keyframes no 4K pesado. Grava **`<clipe>.md`** + contact sheet ao lado de cada vídeo e **`_DECUPA.md`** por pasta. |
| Entender | `project_digest`, `save_project_overview` | Lê todos os `.md` e escreve **`_VISAO_GERAL.md`**. Pergunta a duração do vídeo e a trilha (música específica ou mood/gênero) e propõe 2–4 direcionamentos. |
| Olhar | `contact_sheet`, `timeline_strip`, `frames`, `reframe_preview`, `multicam_monitor` | Imagens reais do material, com TC e a fala do instante. |
| Escolher | `scenes`, `takes`, `broll`, `find_phrase`, `annotate` | Melhor take de cada fala (geralmente o último completo; o take que o falante criticou fica de fora) e trechos de b-roll aproveitáveis. As anotações vão pro `.md`. |
| Multicâmera | `sync_multicam` | Sincroniza câmeras e gravadores pelo áudio, no lugar do PluralEyes: corrige o drift do gravador e gera o XML no mesmo layout. |
| Selects | `make_selects` | Sequências de melhores takes e de b-roll + marcadores em todos os clipes (✅ melhor / ✗ criticado / 👁 notas). |
| Montar | `music_map`, `music_windows`, `build_edit`, `edit_storyboard`, `export_edit` | Trilha primeiro (BPM, compassos, seções), corte no beat, b-roll sobre a fala, storyboard **sem render**, FCP7 XML pro NLE. |
| Revisar | `compare_with_nle`, `adopt_nle_timeline` | Lê a timeline como está no NLE: o que você mudou e seus marcadores como notas. As suas mudanças viram a nova base. |
| Legendas e overlays | `caption_sentences`, `caption_style_preview`, `captions_srt`, `caption_overlay`, `render_overlay`, `narration` | Gramática corrigida sem mudar a fala, 6 estilos com preview, SRT editável ou overlay com alpha (ProRes 4444), lower thirds e títulos, narração ElevenLabs/edge-tts. Usa a skill motion-designer. |
| Finalizar | `qc_timeline` | Aponta flash frame, buraco preto, mídia offline, take repetido, jump cut, salto de volume e formato errado pra plataforma. Não corrige nada sozinho. |
| Lembrar | `client_profile`, `save_client_profile`, `read_premiere_project` | Perfil por cliente com vocabulário, assinatura, trilhas e o estilo da casa medido nos seus projetos finalizados. |

E também um **painel dentro do Premiere** (Janela › Extensões › decupa): chat com o Claude Code com o contexto do
projeto (sequência ativa, playhead, clipes selecionados), botões para cada etapa e barra de progresso.

---

## Instalação

O decupa trabalha junto com três peças: o **Claude Code** (onde você conversa) e o **MCP do Premiere** e/ou o **MCP
do Resolve** (que operam o editor). Abaixo, o passo a passo completo para cada sistema.

| | Windows (recomendado com placa NVIDIA) | macOS (Apple Silicon) |
| --- | --- | --- |
| decupa | dentro do **WSL** (Ubuntu), usando a GPU | nativo |
| Transcrição | faster-whisper na GPU (CUDA) | mlx-whisper |
| Decodificação | NVDEC (opcional) ou CPU; proxies | VideoToolbox; proxies |
| Premiere / Resolve / Claude Code | no Windows | no Mac |

> Os passos marcados como **opcional** podem ser pulados. O resto é necessário.

### Chat do Claude Desktop ou painel no Premiere?

O decupa funciona nos dois. A diferença é só **onde você conversa**:

| | Chat do Claude Desktop | Painel do decupa no Premiere |
| --- | --- | --- |
| Quem roda por baixo | o próprio Claude Desktop | o **Claude Code** (`claude -p`) |
| Onde registrar decupa, Premiere e Resolve | `claude_desktop_config.json` (ou bundle `.mcpb`) | `claude mcp add -s user ...` |
| Conectores do claude.ai | sim | sim |
| Vantagem | conversa mais confortável, colar imagens | ao lado da timeline, botões rápidos, barra de progresso |

O painel precisa do Claude Code porque uma extensão do Premiere não consegue abrir uma conversa no Claude Desktop: o
Desktop não aceita mensagens de outros programas. O Claude Code tem um modo para isso (`claude -p`), e o painel é,
na prática, um Claude Code dentro do Premiere. Por isso ele enxerga os MCPs registrados **no Claude Code**, não os do
Desktop. Se você só usa o chat do Desktop, registre os MCPs lá e pule o painel.

As duas conversas são separadas. O que liga uma à outra é o que fica no disco: os `.md` ao lado dos clipes, o
`_DECUPA.md`, o `_VISAO_GERAL.md` e o índice em `_decupa/`.

### Windows

#### 1. Programas no Windows

No **PowerShell**:

```powershell
winget install Git.Git
winget install OpenJS.NodeJS.LTS
winget install Gyan.FFmpeg
```

- **Node.js 20.19+**: usado pelo MCP do Premiere.
- **Python 3.12** do instalador oficial ([python.org](https://www.python.org/downloads/windows/)), com *Add python.exe
  to PATH* marcado: usado pelo MCP do Resolve.
- Driver **NVIDIA** atualizado (o driver do Windows também atende o WSL).

#### 2. Claude Code

```powershell
irm https://claude.ai/install.ps1 | iex
```

Rode `claude` uma vez e faça login com a sua conta Anthropic (`/login`). Documentação:
[docs.claude.com/claude-code](https://docs.claude.com/en/docs/claude-code/overview).

#### 3. WSL com Ubuntu

No PowerShell **como administrador**:

```powershell
wsl --install -d Ubuntu
```

Reinicie se pedir, abra o **Ubuntu** pelo menu Iniciar e crie o seu usuário.

#### 4. decupa (dentro do Ubuntu)

```bash
sudo apt update && sudo apt install -y ffmpeg git curl
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc
uv tool install --python 3.12 "decupa[cuda,motion] @ git+https://github.com/bernardotraad/decupa.git"
decupa-doctor --fix-browser
```

- `[cuda]` traz as bibliotecas da transcrição na GPU; `[motion]` traz o navegador usado nas legendas e overlays.
- O `decupa-doctor` confere tudo e diz o que falta. Rode de novo no fim da instalação.
- Para atualizar depois: `uv tool upgrade decupa`.

**Opcional — decodificação na GPU (NVDEC):** cerca de 2× mais rápido em 4K H.264. HEVC 4:2:2 (ex.: Canon 10 bits)
não roda na NVDEC; para esses arquivos, use proxies.

```bash
mkdir -p ~/.local/opt && cd ~/.local/opt
curl -L -o ff.tar.xz https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz
tar xf ff.tar.xz && rm ff.tar.xz && cd ~
```

**Opcional — mais núcleos e memória para o WSL:** crie `C:\Users\<você>\.wslconfig` com uma seção `[wsl2]` contendo
`processors=<núcleos>` e `memory=<GB>GB`, e rode `wsl --shutdown`.

#### 5. Registrar o decupa no Claude Code (PowerShell)

Troque `<usuario>` pelo seu usuário do Ubuntu:

```powershell
claude mcp add -s user decupa -- C:\Windows\System32\wsl.exe -d Ubuntu -- /home/<usuario>/.local/bin/decupa
```

Caminhos `D:\...` funcionam direto: o decupa traduz para o WSL e devolve tudo (XML, `.md`) com o caminho do Windows.

#### 6. MCP do Premiere Pro — [leancoderkavy/premiere-pro-mcp](https://github.com/leancoderkavy/premiere-pro-mcp)

Premiere 2020–2026. Com o **Premiere fechado**, no PowerShell:

```powershell
npm install -g premiere-pro-mcp
premiere-pro-mcp --install-cep
claude mcp add -s user premiere-pro -- cmd /c premiere-pro-mcp
```

Abra o Premiere com um projeto → **Janela › Extensões › MCP for Adobe Premiere Pro** → status *Running*. Para conferir:
`premiere-pro-mcp --doctor`. O nome `premiere-pro` importa: é o que o painel do decupa espera.

- Se aparecer *server disconnected*, registre com o modo de protocolo legado:
  `claude mcp add -s user -e PREMIERE_MCP_PROTOCOL_MODE=legacy premiere-pro -- cmd /c premiere-pro-mcp`.
- Já usa o MCP do Premiere pelo Claude Desktop (bundle `.mcpb`)? Ele continua lá. O painel do decupa usa o **Claude
  Code**, que precisa do registro acima (é outro cliente, então não duplica nada).

#### 7. MCP do DaVinci Resolve — [hiteshK03/davinci-resolve-mcp](https://github.com/hiteshK03/davinci-resolve-mcp)

Resolve 18+ (funciona no Free). No PowerShell, na pasta onde você guarda projetos:

```powershell
git clone https://github.com/hiteshK03/davinci-resolve-mcp.git
cd davinci-resolve-mcp
python -m venv venv
venv\Scripts\pip install -r requirements.txt "mcp<2"
copy src\CursorBridge.py "$env:APPDATA\Blackmagic Design\DaVinci Resolve\Support\Fusion\Scripts\Utility\"
claude mcp add -s user davinci-resolve -- "$PWD\venv\Scripts\python.exe" "$PWD\src\resolve_mcp_bridge.py"
```

O `mcp<2` é necessário porque o servidor quebra com o pacote `mcp` 2.x. Toda vez que abrir o Resolve: **Workspace ›
Scripts › Utility › CursorBridge** (o console mostra *Bridge is running*).

#### 8. Painel do decupa no Premiere (dentro do Ubuntu)

```bash
decupa-panel
```

Instala o painel no Premiere do Windows e libera extensões não assinadas. Reinicie o Premiere → **Janela › Extensões ›
decupa**.

#### 9. Opcionais

- **Skill motion-designer** (legendas estilizadas, overlays, narração): copie a pasta da skill para
  `~/.claude/skills/motion-designer` **dentro do Ubuntu**.
- **ElevenLabs** (narração mais natural): salve a chave em `~/.config/elevenlabs/api_key` dentro do Ubuntu. Sem ela, a
  narração usa o edge-tts gratuito.
- **Claude Desktop**: em `claude_desktop_config.json`, use o mesmo comando do passo 5:
  `"command": "C:\\Windows\\System32\\wsl.exe", "args": ["-d", "Ubuntu", "--", "/home/<usuario>/.local/bin/decupa"]`.

#### 10. Conferir

```bash
decupa-doctor
```

No PowerShell, `claude mcp list` deve mostrar `decupa`, `premiere-pro` e `davinci-resolve` como *Connected*.

---

### macOS (Apple Silicon)

#### 1. Programas

Instale o [Homebrew](https://brew.sh) e, no Terminal:

```bash
brew install ffmpeg uv node git
```

Para o MCP do Resolve, instale o **Python 3.12 do [python.org](https://www.python.org/downloads/macos/)** (não o do
Homebrew).

#### 2. Claude Code

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

Rode `claude` e faça login (`/login`).

#### 3. decupa

```bash
uv tool install --python 3.12 "decupa[motion] @ git+https://github.com/bernardotraad/decupa.git"
decupa-doctor --fix-browser
claude mcp add -s user decupa -- "$HOME/.local/bin/decupa"
```

No Mac a transcrição usa o **mlx-whisper** (instalado junto) e a decodificação usa o **VideoToolbox**. A primeira
transcrição baixa o modelo `whisper-large-v3-turbo` (~1,6 GB).

> Registre os MCPs no Mac sempre com **caminho absoluto**: o Premiere aberto pelo Dock não enxerga o PATH do Terminal,
> e o painel do decupa roda a partir dele.

#### 4. MCP do Premiere Pro — [leancoderkavy/premiere-pro-mcp](https://github.com/leancoderkavy/premiere-pro-mcp)

Com o **Premiere fechado**:

```bash
npm install -g premiere-pro-mcp
premiere-pro-mcp --install-cep
claude mcp add -s user premiere-pro -- "$(which premiere-pro-mcp)"
```

Abra o Premiere → **Janela › Extensões › MCP for Adobe Premiere Pro** → *Running*. Para conferir:
`premiere-pro-mcp --doctor`. Se aparecer *server disconnected*, registre com `-e PREMIERE_MCP_PROTOCOL_MODE=legacy`. Se
o Premiere e o Resolve já estão no Claude Desktop, registre-os também no Claude Code (é o que o painel usa).

#### 5. MCP do DaVinci Resolve — [hiteshK03/davinci-resolve-mcp](https://github.com/hiteshK03/davinci-resolve-mcp)

```bash
cd ~/Documents
git clone https://github.com/hiteshK03/davinci-resolve-mcp.git
cd davinci-resolve-mcp
/usr/local/bin/python3.12 -m venv venv
venv/bin/pip install -r requirements.txt "mcp<2"
cp src/CursorBridge.py "$HOME/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility/"
claude mcp add -s user davinci-resolve -- "$PWD/venv/bin/python" "$PWD/src/resolve_mcp_bridge.py"
```

Toda vez que abrir o Resolve: **Workspace › Scripts › Utility › CursorBridge**.

#### 6. Painel do decupa no Premiere

```bash
decupa-panel
```

Reinicie o Premiere → **Janela › Extensões › decupa**.

#### 7. Opcionais e conferência

- **Skill motion-designer**: copie a pasta para `~/.claude/skills/motion-designer`.
- **ElevenLabs**: salve a chave em `~/.config/elevenlabs/api_key`.
- **Claude Desktop**: `"command": "/Users/<você>/.local/bin/decupa"`.
- Para conferir: `decupa-doctor` e `claude mcp list`.

---

## Como usar

1. Monte o projeto no Premiere/Resolve e **salve o `.prproj` dentro de `<trabalho>/#projeto/`**: o painel acha a pasta
   do material a partir dele.
2. Abra o painel **decupa** e clique em **Analisar material**. A barra mostra o progresso. Pode fechar o painel, o
   Claude Desktop ou o Premiere: a indexação continua.
3. Quando terminar, o painel oferece a **Visão geral**: o que é o projeto, as perguntas (duração, trilha) e os
   direcionamentos possíveis. Escolha um ou explique a sua ideia.
4. **Selects** → confira no NLE as escolhas de take e b-roll.
5. Peça a montagem. Ela entra como sequência nova. Revise, mexa à vontade e deixe **marcadores** com notas.
6. **Ler minhas notas** → o decupa compara com o que entregou, adota as suas mudanças e aplica as notas numa nova
   versão.
7. **Legendas** → frases corrigidas (tabela fala → legenda), preview dos 6 estilos com o recomendado marcado, SRT
   editável e/ou overlay com alpha.
8. **QC antes de exportar** → lista de problemas com timecode. Você finaliza (cor, efeitos) e exporta.

Também dá pra fazer tudo pelo terminal do Claude Code, com o prompt guiado `/mcp__decupa__montar`.

**Regras do decupa:** você está sempre no controle. Ele propõe antes de mudar, nunca sobrescreve a sua sequência (cria
uma nova), não desfaz suas mudanças manuais e só renderiza o vídeo final se você pedir. Material em log (S-Log, C-Log,
BMD Film) parece lavado de propósito: a correção de cor é sua, no NLE.

### Arquivos que ele cria

| Onde | O quê |
| --- | --- |
| ao lado de cada vídeo | `<clipe>.md` (técnico, cor, cenas, takes, transcrição) e `_decupa/<clipe>.jpg` |
| em cada pasta de material | `_DECUPA.md` (tabela da pasta + progresso) e `_decupa/progress.json` |
| na raiz do trabalho | `_VISAO_GERAL.md`, `_ingest.md` / `_ingest.json` |
| `#projeto/` | XMLs (selects, sync, versões da montagem) e `decupa_overlays/` (legendas, overlays, narração) |
| `D:\Documentos\decupa\clientes` (Mac: `~/decupa/clientes`) | perfis de cliente (`<cliente>.md`) |
| `~/.cache/decupa/` | índices (transcrição, métricas, áudio). Apagar só força uma nova análise. |

O que você escrever nos `.md` fora dos marcadores `<!-- decupa:auto -->` nunca é apagado.

## Validações

- **Multicâmera:** num show de 1h53 (2 câmeras a 29,97 e 23,976 fps, mais o gravador de mesa), os 31 clipes com áudio em
  comum ficaram **a até 1 frame** do sync do PluralEyes, com o mesmo drift (`+0.079` × `+0.078` s). Os clipes sem áudio
  em comum o PluralEyes só estaciona no início/fim da timeline; o decupa posiciona pelo relógio da câmera e marca como
  aproximado.
- **Takes:** em reels de corretor, escolheu os mesmos takes de gancho e CTA que o editor (entrada do CTA a 20 ms).
- **Batida:** erro mediano de 7 ms num click de 128 BPM.
- **Multicâmera sintética:** drift medido de 20,1 ppm (real 20) e posições a 0,1–3 ms.

## Configuração

| Variável | Padrão | |
| --- | --- | --- |
| `DECUPA_CACHE` | `~/.cache/decupa` | índices |
| `DECUPA_CLIENTS` | `D:\Documentos\decupa\clientes` / `~/decupa/clientes` | perfis de cliente |
| `DECUPA_MOTION_SKILL` | `~/.claude/skills/motion-designer` | skill de motion |
| `DECUPA_WHISPER` / `DECUPA_MLX_MODEL` | `large-v3-turbo` | modelo de transcrição |
| `DECUPA_FFMPEG_GPU` | `~/.local/opt/ffmpeg-*/bin/ffmpeg` | ffmpeg com CUDA |
| `DECUPA_GPU` / `DECUPA_FAST` | `1` | `0` desliga a NVDEC / o modo keyframes |

## Limitações

- Câmeras sem timecode (ex.: Canon `MVI_*`) começam em `00:00:00:00`, mas os frames continuam exatos.
- A NVDEC não decodifica HEVC/H.264 4:2:2. Nesses casos o decupa analisa só os keyframes, ou você pode usar proxies.
- Uma taxa de quadros por timeline exportada (com fps misturado, gere uma timeline por fps).
- No Premiere, **importe os XMLs à mão** (Arquivo › Importar / Ctrl+I). Importar XML pelo `import_media` do MCP do Premiere
  derruba o Premiere; a importação manual funciona. O decupa entrega o caminho do XML e pede para você importar.
  Mídia comum (overlays `.mov`, `.wav`, `.srt`) pode ir pelo MCP.
- O ranking de takes é evidência, não veredito. Em atuação, olhe o vencedor e o segundo colocado.

## Licença

[MIT](LICENSE)
