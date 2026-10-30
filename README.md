# Cláudio, o Editor

> Antes se chamava **decupa**. O nome mudou na v0.7, quando ele passou a montar, legendar e sincronizar, além de
> decupar. Trabalhos já indexados continuam valendo (veja [Vindo do decupa](#vindo-do-decupa-antes-da-v07)).

**Um servidor MCP que faz o Claude _ver_ e _ouvir_ o seu material bruto antes de montar — e entregar a montagem como
timeline no Premiere Pro ou no DaVinci Resolve, com você no controle.**

[English](README.en.md)

Os MCPs do Premiere e do Resolve deixam uma IA *operar* o editor (importar, colocar clipes, aparar, exportar). O que
falta é olhar o material: sem isso, "o melhor take" ou "um b-roll bom das mãos" vira chute pelo nome do arquivo.

O Cláudio é esse par de olhos e ouvidos:

- **O que é dito** — transcrição por palavra (Whisper, ou ElevenLabs Scribe se você escolher), incluindo a claquete falada
  ("cena 3, take 2… ação… corta"). Início e fim de cada palavra são acertados pela voz medida no áudio: a legenda não
  entra no silêncio antes da fala e o corte não come o fim da palavra.
- **O que se vê** — contact sheets, frames e timelines com waveform devolvidos como **imagens**, com timecode.
- **O que serve** — foco relativo, exposição, movimento de câmera, takes repetidos, falas criticadas pelo próprio
  falante, cor (log × Rec.709), silêncios.

A decisão sai disso, o Cláudio transforma em **in/out exatos por frame**, e a timeline entra no Premiere/Resolve como
uma **sequência nova** para você revisar, ajustar, colorir e exportar. Ele **nunca renderiza o vídeo final** sem você
pedir.

```
cartão ─► ingest ─► decupagem (.md ao lado de cada clipe) ─► visão geral + perguntas ─► selects ─► timeline
                                                                                                    │
              você revisa no NLE (marcadores = notas) ◄── nova versão ◄── Cláudio lê o que você mudou ◄┘
                                │
                                └─► QC ─► você finaliza (cor, efeitos) e exporta
```

## O que ele faz

| Etapa | Ferramentas | |
| --- | --- | --- |
| Descarregar | `ingest_card` | Cópia verificada (xxHash64 copiando e relendo o destino) para as pastas do [perfil](#perfil-do-editor) (`brutos/<câmera>`, `brutos/audio`, `brutos/fotos`, `projeto`, `trilhas`). Nunca sobrescreve (outro cartão com os mesmos nomes vai pra `CARD_n`) e nunca mexe no cartão. Depois indexa. |
| Indexar | `index_folder`, `index_status` | Em processo próprio (segue mesmo com o chat ou o Premiere fechados), com barra de progresso. Usa proxies (`Proxy/` ou `Proxies/`) quando existem, e GPU (NVDEC) ou só keyframes no 4K pesado. Grava **`<clipe>.md`** + contact sheet ao lado de cada vídeo e **`_CLAUDIO.md`** por pasta. |
| Entender | `project_digest`, `save_project_overview` | Lê todos os `.md` e escreve **`_VISAO_GERAL.md`**. Pergunta a duração do vídeo e a trilha (música específica ou mood/gênero) e propõe 2–4 direcionamentos. |
| Olhar | `contact_sheet`, `timeline_strip`, `frames`, `reframe_preview`, `multicam_monitor` | Imagens reais do material, com TC e a fala do instante. |
| Escolher | `scenes`, `takes`, `broll`, `find_phrase`, `annotate` | Melhor take de cada fala (geralmente o último completo; o take que o falante criticou fica de fora) e trechos de b-roll aproveitáveis. As anotações vão pro `.md`. |
| Busca visual | `find_shot` | Acha planos pela descrição ("mãos no braço da guitarra", "estrada ao pôr do sol"), com CLIP rodando na CPU. As miniaturas saem da própria indexação. |
| Pessoas | `people`, `name_people`, `find_person` | Quem aparece (rostos) e quem fala (vozes), a mesma pessoa com o mesmo nome em todos os clipes. Você confirma os nomes e eles ficam guardados. Ex.: "só as falas do Caio". |
| Descrever planos | `describe_shots` | O que se vê em cada trecho (tipo de plano, lugar, objetos, dia/noite, quem aparece, o que é falado), escrito no `.md` de cada clipe sem IA. |
| Áudio estéreo | `stereo_fix` | Áudio que sai de um lado só (lapela ou um microfone em L ou R) passa a sair dos dois; dois microfones de verdade ficam como estão. Opcional: nivela as falas. |
| Desaninhar | `flatten_timeline` | Troca as sequências aninhadas pelos próprios clipes, direto nas trilhas, para corrigir a cor take a take. |
| Multicâmera | `sync_multicam` | Sincroniza câmeras e gravadores pelo áudio, no lugar do PluralEyes: corrige o drift do gravador e gera o XML no mesmo layout, com uma **cor de label por câmera** (vídeo, áudio e bin) e outra para o áudio externo. |
| Selects | `make_selects` | Sequências de melhores takes e de b-roll + marcadores em todos os clipes (✅ melhor / ✗ criticado / 👁 notas). |
| Montar | `music_map`, `music_windows`, `build_edit`, `edit_storyboard`, `export_edit` | Trilha primeiro (BPM, compassos, seções), corte no beat, b-roll sobre a fala, storyboard **sem render**, FCP7 XML pro NLE. |
| Revisar | `compare_with_nle`, `adopt_nle_timeline` | Lê a timeline como está no NLE: o que você mudou e seus marcadores como notas. As suas mudanças viram a nova base. |
| Legendas e overlays | `caption_sentences`, `caption_style_preview`, `captions_srt`, `caption_overlay`, `render_overlay`, `narration` | Gramática corrigida sem mudar a fala; blocos **por frase** como legendador (nunca termina em "de"/"que", não parte número nem nome, tempos no quadro, presos ao corte de câmera); sai da leitura o que não é fala ("Ação.", "ãh", "Eu Eu"); 6 estilos com preview, SRT editável ou overlay com alpha (ProRes 4444), lower thirds e títulos, narração ElevenLabs/edge-tts. **Música:** a letra sai sincronizada pela voz isolada (UVR MDX-Net em ONNX, modelo de 67 MB baixado no primeiro uso), um bloco por verso e o refrão corrigindo linhas mal ouvidas. Usa a skill motion-designer. |
| Limpar falas | `clean_speech` | Tira pausas longas (ritmo apertado · médio · solto, com prévia do quanto cada um corta), hesitações, comandos de set ("Ação", "valendo"), notas faladas pro editor e recomeços avisados (com marcador onde estavam). Crossfade de 2 quadros nas emendas (sem clique); opcional: zoom 100/107,5 % nos jump cuts e punch-in de 115 % na frase de efeito. |
| Finalizar | `qc_timeline` | Aponta flash frame, buraco preto, mídia offline, take repetido, jump cut, salto de volume e formato errado pra plataforma; mede a voz (abafada / lapela coberta, dois microfones diferentes em L/R, clipe mudo com ganho alto). Não corrige nada sozinho. |
| Lembrar | `client_profile`, `save_client_profile`, `read_premiere_project` | Perfil por cliente com vocabulário, assinatura, trilhas e o estilo da casa medido nos seus projetos finalizados. |

E também um **painel dentro do Premiere** (Janela › Extensões › Cláudio, o Editor): chat com o Claude Code com o contexto do
projeto (sequência ativa, playhead, clipes selecionados), botões para cada etapa e barra de progresso.

Os botões ficam agrupados pela etapa do trabalho, cada grupo com uma cor (● = roda sem IA, sem crédito):

| Etapa | Botões |
|---|---|
| **Preparar** | 01 Analisar material ● · 02 Status ● · 03 Áudio estéreo ● · 04 Multicam ● |
| **Entender** | 05 Visão geral · 06 Descrever planos ● · 07 Ler minhas notas · 08 Aprender com vídeos prontos |
| **Montar** | 09 Montar com receita · 10 Selects · 11 Corte por quem fala · 12 Recap de evento · 13 Limpar falas ● · 14 SFX nos cortes ● · 15 Trilha sonora · 16 Desaninhar ● |
| **Legendar** | 17 Transcrição Scribe ● · 18 Legendas · 19 Overlay da legenda revisada · 20 Legenda MOGRT ● |
| **Entregar** | 21 QC antes de exportar ● · 22 Registrar no vault |

**Montar com receita** lista as [receitas](#receitas) no chat (a do cliente já marcada, se o perfil dele disser
`"receita"`) e monta seguindo a escolhida. Uma receita com `botao: {"rotulo": "…", "dica": "…"}` no frontmatter vira um
botão próprio no grupo Montar, como o **Recap de evento**; os números seguem a ordem na tela.

---

## Instalação

O Cláudio trabalha junto com três peças: o **Claude Code** (onde você conversa) e o **MCP do Premiere** e/ou o **MCP
do Resolve** (que operam o editor). Abaixo, o passo a passo completo para cada sistema.

| | Windows (recomendado com placa NVIDIA) | macOS (Apple Silicon) |
| --- | --- | --- |
| Cláudio | dentro do **WSL** (Ubuntu), usando a GPU | nativo |
| Transcrição | faster-whisper na GPU (CUDA) | mlx-whisper |
| Decodificação | NVDEC (opcional) ou CPU; proxies | VideoToolbox; proxies |
| Premiere / Resolve / Claude Code | no Windows | no Mac |

> Os passos marcados como **opcional** podem ser pulados. O resto é necessário.

### Chat do Claude Desktop ou painel no Premiere?

O Cláudio funciona nos dois. A diferença é só **onde você conversa**:

| | Chat do Claude Desktop | Painel do Cláudio no Premiere |
| --- | --- | --- |
| Quem roda por baixo | o próprio Claude Desktop | o **Claude Code** (`claude -p`) |
| Onde registrar o Cláudio, o Premiere e o Resolve | `claude_desktop_config.json` (ou bundle `.mcpb`) | `claude mcp add -s user ...` |
| Conectores do claude.ai | sim | sim |
| Vantagem | conversa mais confortável, colar imagens | ao lado da timeline, botões rápidos, barra de progresso |

O painel precisa do Claude Code porque uma extensão do Premiere não consegue abrir uma conversa no Claude Desktop: o
Desktop não aceita mensagens de outros programas. O Claude Code tem um modo para isso (`claude -p`), e o painel é,
na prática, um Claude Code dentro do Premiere. Por isso ele enxerga os MCPs registrados **no Claude Code**, não os do
Desktop. Se você só usa o chat do Desktop, registre os MCPs lá e pule o painel.

As duas conversas são separadas. O que liga uma à outra é o que fica no disco: os `.md` ao lado dos clipes, o
`_CLAUDIO.md`, o `_VISAO_GERAL.md` e o índice em `_claudio/`.

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

#### 4. Cláudio (dentro do Ubuntu)

```bash
sudo apt update && sudo apt install -y ffmpeg git curl
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc
uv tool install --python 3.12 "claudio[cuda,motion] @ git+https://github.com/bernardotraad/claudio.git"
claudio-doctor --fix-browser
```

- `[cuda]` traz as bibliotecas da transcrição na GPU; `[motion]` traz o navegador usado nas legendas e overlays.
- O `claudio-doctor` confere tudo e diz o que falta. Rode de novo no fim da instalação.
- Para atualizar depois: `uv tool upgrade claudio`.

**Opcional — decodificação na GPU (NVDEC):** cerca de 2× mais rápido em 4K H.264. HEVC 4:2:2 (ex.: Canon 10 bits)
não roda na NVDEC; para esses arquivos, use proxies.

```bash
mkdir -p ~/.local/opt && cd ~/.local/opt
curl -L -o ff.tar.xz https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz
tar xf ff.tar.xz && rm ff.tar.xz && cd ~
```

**Opcional — mais núcleos e memória para o WSL:** crie `C:\Users\<você>\.wslconfig` com uma seção `[wsl2]` contendo
`processors=<núcleos>` e `memory=<GB>GB`, e rode `wsl --shutdown`.

#### 5. Registrar o Cláudio no Claude Code (PowerShell)

Troque `<usuario>` pelo seu usuário do Ubuntu:

```powershell
claude mcp add -s user claudio -- C:\Windows\System32\wsl.exe -d Ubuntu -- /home/<usuario>/.local/bin/claudio
```

Caminhos `D:\...` funcionam direto: o Cláudio traduz para o WSL e devolve tudo (XML, `.md`) com o caminho do Windows.

#### 6. MCP do Premiere Pro — [leancoderkavy/premiere-pro-mcp](https://github.com/leancoderkavy/premiere-pro-mcp)

Premiere 2020–2026. Com o **Premiere fechado**, no PowerShell:

```powershell
npm install -g premiere-pro-mcp
premiere-pro-mcp --install-cep
claude mcp add -s user premiere-pro -- cmd /c premiere-pro-mcp
```

Abra o Premiere com um projeto → **Janela › Extensões › MCP for Adobe Premiere Pro** → status *Running*. Para conferir:
`premiere-pro-mcp --doctor`. O nome `premiere-pro` importa: é o que o painel do Cláudio espera.

- Se aparecer *server disconnected*, registre com o modo de protocolo legado:
  `claude mcp add -s user -e PREMIERE_MCP_PROTOCOL_MODE=legacy premiere-pro -- cmd /c premiere-pro-mcp`.
- Já usa o MCP do Premiere pelo Claude Desktop (bundle `.mcpb`)? Ele continua lá. O painel do Cláudio usa o **Claude
  Code**, que precisa do registro acima (é outro cliente, então não duplica nada).
- **Ponte UXP** (Premiere 25.6+): com `PREMIERE_UXP_TOKEN` no `env` do registro `premiere-pro` (o mesmo token no
  plugin, em Janela › Plugins UXP), o painel do Cláudio mantém **um** MCP do Premiere sempre ligado
  (`premiere_bridge.js`, em `http://127.0.0.1:7788/mcp`), e cada mensagem conversa com ele: a UXP fica conectada entre
  as mensagens, em vez de só durante cada uma. A ponte sai sozinha 10 min depois de o Premiere fechar; o log fica em
  `%TEMP%\claudio-premiere-bridge.log`. Só um processo segura a porta 7777: deixe o token fora do Claude Desktop.
- **Instalando de dentro do Claude Desktop (Windows)?** Ele é um app MSIX e virtualiza o `%APPDATA%`: um `npm i -g`
  rodado de lá vai para `%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\npm`, que o Premiere e o painel não
  enxergam — rode no PowerShell normal (ou copie de lá para `%APPDATA%\npm`).

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

#### 8. Painel do Cláudio no Premiere (dentro do Ubuntu)

```bash
claudio-panel
```

Instala o painel no Premiere do Windows e libera extensões não assinadas. Reinicie o Premiere → **Janela › Extensões ›
Cláudio, o Editor**.

#### 9. Opcionais

- **Skill motion-designer** (legendas estilizadas, overlays, narração): copie a pasta da skill para
  `~/.claude/skills/motion-designer` **dentro do Ubuntu**.
- **ElevenLabs** (narração mais natural): salve a chave em `~/.config/elevenlabs/api_key` dentro do Ubuntu. Sem ela, a
  narração usa o edge-tts gratuito. A mesma chave (com a permissão *Speech to Text*) liga a transcrição pelo
  **Scribe** — veja `stt` em [Configuração](#configuração).
- **Claude Desktop**: em `claude_desktop_config.json`, use o mesmo comando do passo 5:
  `"command": "C:\\Windows\\System32\\wsl.exe", "args": ["-d", "Ubuntu", "--", "/home/<usuario>/.local/bin/claudio"]`.

#### 10. Conferir

```bash
claudio-doctor
```

No PowerShell, `claude mcp list` deve mostrar `claudio`, `premiere-pro` e `davinci-resolve` como *Connected*.

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

#### 3. Cláudio

```bash
uv tool install --python 3.12 "claudio[motion] @ git+https://github.com/bernardotraad/claudio.git"
claudio-doctor --fix-browser
claude mcp add -s user claudio -- "$HOME/.local/bin/claudio"
```

No Mac a transcrição usa o **mlx-whisper** (instalado junto) e a decodificação usa o **VideoToolbox**. A primeira
transcrição baixa o modelo `whisper-large-v3-turbo` (~1,6 GB).

> Registre os MCPs no Mac sempre com **caminho absoluto**: o Premiere aberto pelo Dock não enxerga o PATH do Terminal,
> e o painel do Cláudio roda a partir dele.

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

#### 6. Painel do Cláudio no Premiere

```bash
claudio-panel
```

Reinicie o Premiere → **Janela › Extensões › Cláudio, o Editor**.

#### 7. Opcionais e conferência

- **Skill motion-designer**: copie a pasta para `~/.claude/skills/motion-designer`.
- **ElevenLabs**: salve a chave em `~/.config/elevenlabs/api_key`.
- **Claude Desktop**: `"command": "/Users/<você>/.local/bin/claudio"`.
- Para conferir: `claudio-doctor` e `claude mcp list`.

---

## Como usar

1. Monte o projeto no Premiere/Resolve e **salve o `.prproj` dentro da pasta de projeto do trabalho** (`<trabalho>/projeto/`, ou o nome do seu [perfil](#perfil-do-editor)): o painel acha a pasta
   do material a partir dele.
2. Abra o painel **Cláudio** e clique em **Analisar material**. A barra mostra o progresso. Pode fechar o painel, o
   Claude Desktop ou o Premiere: a indexação continua.
3. Quando terminar, o painel oferece a **Visão geral**: o que é o projeto, as perguntas (duração, trilha) e os
   direcionamentos possíveis. Escolha um ou explique a sua ideia.
4. **Selects** → confira no NLE as escolhas de take e b-roll.
   **Multicam** (se houver mais de uma câmera ou gravador externo) → o Cláudio propõe o mapeamento pasta → câmera e as
   cores, pergunta, sincroniza pelo áudio, confere as câmeras lado a lado e entrega a "Synced Sequence" com cada câmera
   na sua cor.
5. Peça a montagem. Ela entra como sequência nova. Revise, mexa à vontade e deixe **marcadores** com notas.
6. **Ler minhas notas** → o Cláudio compara com o que entregou, adota as suas mudanças e aplica as notas numa nova
   versão.
7. **Legendas** → frases corrigidas (tabela fala → legenda), preview dos 6 estilos com o recomendado marcado, SRT
   editável e/ou overlay com alpha.
8. **QC antes de exportar** → lista de problemas com timecode. Você finaliza (cor, efeitos) e exporta.

A conversa do painel fica salva ao lado do projeto (`<projeto>.claudio-chat.json`) e volta quando você reabre o
Premiere: as mensagens aparecem de novo e o Claude continua de onde parou. O **＋** começa uma conversa nova.

Também dá pra fazer tudo pelo terminal do Claude Code, com o prompt guiado `/mcp__claudio__montar`: ele escolhe a
[receita](#receitas) do tipo de projeto e segue as etapas dela.

**Regras do Cláudio:** você está sempre no controle. Ele propõe antes de mudar, nunca sobrescreve a sua sequência (cria
uma nova), não desfaz suas mudanças manuais e só renderiza o vídeo final se você pedir. Material em log (S-Log, C-Log,
BMD Film) parece lavado de propósito: a correção de cor é sua, no NLE.

### Arquivos que ele cria

| Onde | O quê |
| --- | --- |
| ao lado de cada vídeo | `<clipe>.md` (técnico, cor, cenas, takes, transcrição) e `_claudio/<clipe>.jpg` |
| em cada pasta de material | `_CLAUDIO.md` (tabela da pasta + progresso) e `_claudio/progress.json` |
| na raiz do trabalho | `_VISAO_GERAL.md`, `_ingest.md` / `_ingest.json` |
| `projeto/` (nome do perfil) | XMLs (selects, sync, versões da montagem), `claudio_overlays/` (legendas, overlays, narração) e `<projeto>.claudio-chat.json` (conversa do painel) |
| `clientes` do perfil, senão `<vault>/trabalho/clientes/`, senão `~/Documents/Claudio/clientes` | perfis de cliente (`<cliente>.md`, com frontmatter do vault) |
| `~/.cache/claudio/` | índices (transcrição, métricas, áudio). Apagar só força uma nova análise. |

O que você escrever nos `.md` fora dos marcadores `<!-- claudio:auto -->` nunca é apagado.

## Validações

- **Multicâmera:** num show de 1h53 (2 câmeras a 29,97 e 23,976 fps, mais o gravador de mesa), os 31 clipes com áudio em
  comum ficaram **a até 1 frame** do sync do PluralEyes, com o mesmo drift (`+0.079` × `+0.078` s). Os clipes sem áudio
  em comum o PluralEyes só estaciona no início/fim da timeline; o Cláudio posiciona pelo relógio da câmera e marca como
  aproximado.
- **Takes:** em reels de corretor, escolheu os mesmos takes de gancho e CTA que o editor (entrada do CTA a 20 ms).
- **Batida:** erro mediano de 7 ms num click de 128 BPM.
- **Multicâmera sintética:** drift medido de 20,1 ppm (real 20) e posições a 0,1–3 ms.

## Configuração

| Variável | Padrão | |
| --- | --- | --- |
| `CLAUDIO_CACHE` | `~/.cache/claudio` | índices |
| `CLAUDIO_PROFILE` | `~/.config/claudio/perfil.json` | [perfil do editor](#perfil-do-editor) |
| `CLAUDIO_CLIENTS` | `clientes` do perfil | perfis de cliente |
| `CLAUDIO_VAULT` | `vault` do perfil | vault Obsidian onde ficam os perfis |
| `CLAUDIO_MOTION_SKILL` | `~/.claude/skills/motion-designer` | skill de motion |
| `CLAUDIO_WHISPER` / `CLAUDIO_MLX_MODEL` | `large-v3-turbo` | modelo de transcrição |
| `CLAUDIO_FFMPEG_GPU` | `~/.local/opt/ffmpeg-*/bin/ffmpeg` | ffmpeg com CUDA |
| `CLAUDIO_GPU` / `CLAUDIO_FAST` | `1` | `0` desliga a NVDEC / o modo keyframes |
| `CLAUDIO_STT` | `whisper` | `elevenlabs` transcreve pelo ElevenLabs Scribe (pago, tempos de palavra a 1–2 frames da voz) |
| `CLAUDIO_SCRIBE_MODEL` | `scribe_v2` | modelo do Scribe |

As variáveis antigas `DECUPA_*` continuam valendo. Qualquer uma também pode ir em `~/.config/claudio/settings.json`,
em minúsculas e sem o prefixo — mais prático, porque o MCP e o painel rodam pelo `wsl.exe`:

```json
{"stt": "elevenlabs"}
```

**Scribe (opcional).** Com `stt` = `elevenlabs`, "Analisar material" transcreve pela API (resposta guardada no cache
do clipe: reanalisar não paga de novo). Clipes já analisados pelo Whisper refazem **só a transcrição** — a imagem não é
reanalisada. Música continua no Whisper + voz isolada. Qualquer erro da API (sem crédito, sem internet, chave sem
permissão) cai no Whisper e fica anotado em `stt_error` no índice. Voltar para `whisper` + analisar refaz pelo Whisper.

No painel, o **14 Transcrição Scribe** liga/desliga o Scribe e lista as sequências do projeto com fala em A1, **lidas
como estão agora na timeline** (não de XML salvo), com as legendadas que ainda usam o Whisper já marcadas. "Refazer com
o Scribe" retranscreve só esses clipes (sem IA), exporta cada sequência marcada naquele momento e pede à IA a revisão de
gramática e um `<sequência> (scribe).srt` na pasta de legendas para cada uma — sem mexer nas sequências.

### Perfil do editor

O Cláudio não tem nome de pasta fixo: ele lê `~/.config/claudio/perfil.json`. Sem esse arquivo, usa os nomes neutros
abaixo. Escreva só o que muda; o `claudio-doctor` mostra o perfil ativo.

```json
{
 "pastas": {
  "brutos": "BRUTOS", "audio": "BRUTOS/00 AUDIO", "fotos": "BRUTOS/03 FOTOS",
  "projeto": "#projeto", "trilhas": "#trilhas", "legendas": "#legendas",
  "overlays": "#projeto/claudio_overlays", "backups": "claudio_backups"
 },
 "nle": "premiere",
 "vault": "D:\\vault",
 "clientes": "D:\\vault\\trabalho\\clientes"
}
```

| Chave | Padrão | |
| --- | --- | --- |
| `pastas.brutos` | `brutos` | material de câmera (`<brutos>/<câmera>/`) |
| `pastas.audio` / `pastas.fotos` | `brutos/audio` / `brutos/fotos` | gravadores externos / fotos |
| `pastas.projeto` | `projeto` | projeto do NLE, XMLs, selects, sync |
| `pastas.trilhas` | `trilhas` | música |
| `pastas.legendas` | `legendas` | `.srt`, overlay de legenda, planos de MOGRT |
| `pastas.overlays` | `projeto/claudio_overlays` | lower thirds, títulos, cartelas |
| `pastas.backups` | `claudio_backups` | backups de sequência do painel, ao lado do projeto do NLE |
| `nle` | — (pergunta uma vez) | `premiere` ou `resolve`: o Claude recebe só as regras desse NLE (como importar o XML, como ler a timeline atual, o que nunca fazer) |
| `vault` | — | vault Obsidian: perfis de cliente seguem as regras dele |
| `clientes` | `<vault>/trabalho/clientes` ou `~/Documents/Claudio/clientes` | perfis de cliente |

As pastas são relativas à pasta do trabalho (`/` separa níveis). Pastas que começam com `#` e as pastas de trabalho do
perfil nunca são indexadas como material. O painel lê o perfil ao abrir: depois de mudar o arquivo, reabra o painel.

### Receitas

Cada tipo de projeto é uma receita: um arquivo Markdown com as perguntas do brief, as etapas, as regras de gosto e os
padrões (formato, duração, ritmo). O Claude lê a receita pela ferramenta `recipe` só quando vai montar; as regras
comuns a toda montagem (`_comum.md`: entregar no NLE, rodadas de notas, legendas, QC) vêm junto.

| Receita | Para |
| --- | --- |
| `depoimento` | uma ou mais pessoas falando para a câmera, com b-roll, para redes (vertical) |
| `depoimento-horizontal` | depoimento ou entrevista em 16:9 para YouTube, site ou institucional, com lower thirds |
| `recap-evento` | aftermovie: melhores momentos no tempo da música |
| `beat-edit` | só imagem e música, sem fala |
| `show-multicam` | show ou clipe musical com várias câmeras |
| `curta-narrativo` | cena roteirizada com claquete falada |
| `podcast` | conversa com uma câmera e um microfone por pessoa: corte por quem fala, limpeza, capítulos |
| `tutorial` | aula, gravação de tela ou bancada: melhor take de cada passo, zoom na região que importa |
| `cortes-de-video-longo` | vários shorts verticais tirados de um podcast, palestra ou live |

Para criar uma receita sua (ou trocar uma das de cima), ponha um `.md` em `~/.config/claudio/receitas/`
(`CLAUDIO_RECIPES` aponta outra pasta) com o mesmo formato:

```markdown
---
nome: podcast
titulo: Podcast em vídeo
quando: conversa gravada com uma câmera por pessoa e um microfone cada
padroes: {"aspect": "16:9", "pace": "medium"}
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

Valores que começam com `[` ou `{` são JSON. `botao` (opcional) põe a receita como botão no painel; `verificar`
(opcional) diz o que o benchmark mede no resultado (`plataforma`, `gancho_s`). Cada `## ` vira uma seção que dá para ler sozinha
(`recipe("depoimento", "legendas")`). Um cliente pode dizer qual receita costuma pedir (`"receita"` no perfil do cliente).

### Aprender um cliente com vídeos prontos

O primeiro passo com um cliente novo: aponte uma pasta de vídeos que você já entregou (de qualquer origem, até de outro
editor). **Sem IA** (botão **08 Aprender com vídeos prontos** › *Medir*, ou `claudio-run learn`), o Cláudio mede os mais
novos — formato, duração, corte a cada quantos segundos, quanto é fala, primeira fala, vinheta final, quanto tempo o
rosto de quem fala aparece, trilha sob a fala e legenda queimada (essas duas são estimativas, sem OCR), loudness — e
escreve a receita do cliente em `~/.config/claudio/receitas/<cliente>.md`: os números no topo e as etapas da receita
embutida mais parecida embaixo, para ajustar. A transcrição para medir é sempre a do Whisper local (grátis), mesmo
com o Scribe ligado. **Com IA** (*Medir e refinar com IA*, ou o prompt `/mcp__claudio__aprender`), o Claude também olha
os vídeos e reescreve a receita com a estrutura, o tom e o que o cliente espera receber, e pergunta só o que não dá
para ver. Se a receita do cliente já existe, a nova vira um rascunho datado ao lado; a escolha do perfil não muda sem
você.

### Benchmark das receitas

Para saber se uma receita (ou uma mudança nela) entrega certo, aponte cada receita para um trabalho seu já montado em
`~/.config/claudio/benchmark.json` (fica fora do repositório):

```json
{"casos": [{"receita": "depoimento", "nota": "INC 9 Robson", "xml": "D:\\job\\#projeto\\Robson.xml", "duracao": [25, 45]}]}
```

```bash
claudio-run bench '{}'
```

Sem IA e sem crédito, ele confere no XML: formato da receita, duração, QC sem erros (flash frames, buracos pretos,
mídia offline…), loudness a ±3 dB do alvo da plataforma, nada aninhado e, nas receitas com fala, a primeira palavra
dentro do limite do gancho. Cada rodada vai para `benchmark-results.jsonl`, ao lado, para comparar com as anteriores.
`xml` pode ser uma pasta (vale o .xml mais novo); `duracao`, `plataforma` e `gancho_s` no caso valem acima da receita
(`null` desliga a medida).

## Limitações

- Câmeras sem timecode (ex.: Canon `MVI_*`) começam em `00:00:00:00`, mas os frames continuam exatos.
- A NVDEC não decodifica HEVC/H.264 4:2:2. Nesses casos o Cláudio analisa só os keyframes, ou você pode usar proxies.
- Uma taxa de quadros por timeline exportada (com fps misturado, gere uma timeline por fps).
- No Premiere, **importe os XMLs à mão** (Arquivo › Importar / Ctrl+I). Importar XML pelo `import_media` do MCP do Premiere
  derruba o Premiere; a importação manual funciona. O Cláudio entrega o caminho do XML, e no painel aparece
  o botão **Importar no Premiere**, que salva o projeto e importa um XML por vez com as janelas do Premiere visíveis.
  Mídia comum (overlays `.mov`, `.wav`, `.srt`) pode ir pelo MCP.
- O ranking de takes é evidência, não veredito. Em atuação, olhe o vencedor e o segundo colocado.

## Gastar pouca IA

O Cláudio roda **na sua máquina**: transcrição, imagem, sync, busca visual, rostos, vozes, legendas e QC não usam
crédito de IA. O que consome é a conversa com o modelo (Claude), e cada mensagem reenvia o histórico.

- **Botões "sem IA"** (ponto verde no painel, ● na tabela acima): Analisar material, Status, Áudio estéreo, Multicam,
  Descrever planos, Limpar falas, SFX nos cortes, Desaninhar, Legenda MOGRT e QC rodam direto no Cláudio pelo
  `claudio-run`, sem modelo nenhum.
- **Seletor de modelo** no painel: Econômico (Haiku) para perguntas simples, Padrão (Sonnet) para o dia a dia,
  Máximo (Opus) só para decisões difíceis de montagem.
- **＋ nova conversa** quando mudar de assunto: conversa longa custa mais a cada mensagem.
- Pelo terminal: `claudio-run <status|index|stereo|flatten|qc|sync> '{json}'`.

## Testes

`pytest -q` roda em segundos, sem GPU, sem Whisper e sem Premiere, com material sintético: sync multicâmera e cores,
desaninhar sequências, grade de batida, caminhos com `#`/espaço/apóstrofo, overlay com alpha e o painel (layout em
300 px, nada sobreposto, HTML de resposta escapado, detector de mudanças). Roda também a cada push no GitHub Actions.

## Licença

[MIT](LICENSE)

### Vindo do decupa (antes da v0.7)

- Pastas `_decupa/` viram `_claudio/` na próxima vez que o trabalho for aberto, sem reindexar. Os links dos `.md` são
  atualizados juntos, e o `_DECUPA.md` dá lugar ao `_CLAUDIO.md`.
- O cache `~/.cache/decupa` é movido para `~/.cache/claudio` (fica um atalho no lugar antigo).
- Pastas já entregues ao Premiere (`decupa_overlays/`, `decupa_drift_corrected/`) não são mexidas, para não quebrar os
  links dos projetos.
- Para trocar a instalação: `uv tool uninstall decupa`, instale `claudio` como acima e registre de novo no Claude Code
  (`claude mcp remove decupa -s user` e depois `claude mcp add ... claudio ...`). Rode `claudio-panel`: ele remove o
  painel antigo.
