# decupa

**Um servidor MCP que faz o Claude realmente _ver_ e _ouvir_ o material antes de cortar.**

[English](README.md)

Os MCPs do Premiere Pro e do DaVinci Resolve deixam uma IA *operar* o editor — importar mídia, colocar clipes,
aparar, exportar. O que eles não fazem é olhar o material. Peça "o melhor take" ou "um b-roll bom das mãos" e o
modelo chuta pelo nome do arquivo.

O `decupa` é o par de olhos e ouvidos que falta. Ele analisa a mídia de verdade e entrega evidência:

- **O que é dito** — transcrição por palavra (Whisper), inclusive a claquete falada
  (*"cena 3, take 2 … ação … corta"*).
- **O que se vê** — contact sheets, frames e timeline strips devolvidos como **imagens**, cada quadro com timecode
  de origem, segundos e as palavras ditas naquele instante.
- **O que é aproveitável** — cortes, foco relativo, exposição, movimento de câmera (parada / pan / tilt / tremida),
  silêncios, takes abortados.

O modelo decide com base nisso, o `decupa` transforma a decisão em **in/out exatos por frame**, e o MCP do
Premiere ou do Resolve executa.

```
material ──► decupa (indexa, olha, ranqueia, ajusta) ──► plano de corte ──► MCP Premiere / MCP Resolve
                     ▲                                                    (ou import de FCP7 XML / EDL)
          imagens + transcrição + métricas
                     ▼
                   Claude
```

## O que faz

| Preciso de | Ferramenta | Como |
| --- | --- | --- |
| Indexar | `index_media`, `index_folder`, `index_status` | Fila em background (GPU). ~4 min por 15 min de 1080p numa RTX 3060. Cacheado. |
| Melhor take entre arquivos | `scenes` | Lê a claquete falada, usa só o trecho entre **"ação"** e **"corta"**, marca takes abortados ("peraí", "de novo"), compara as falas com a versão consenso e ranqueia (`prefer="last"` por padrão — o último take completo costuma ganhar). Diz se é pra julgar por fala, atuação (improviso) ou imagem (sem diálogo). |
| Falas repetidas num arquivo | `takes` | Separa a fala por pausas, agrupa tentativas e falsos começos, ranqueia. |
| B-roll / sem fala | `broll` | Divide nos cortes e depois em trechos de mesmo comportamento de câmera, com foco, exposição e estabilidade. Devolve janelas aproveitáveis (melhores primeiro) e trechos rejeitados com o motivo (`soft`, `blown`, `dark`, `shaky`, `chaotic`). |
| **Olhar** | `contact_sheet` | Grade de N frames num intervalo, com TC + fala. |
| **Olhar de perto** | `timeline_strip` | Thumbs + waveform + cada palavra num mesmo eixo de tempo — pra in/out exato. |
| **Conferir um frame** | `frames` | Frames em tamanho cheio em segundos exatos (foco, piscada, primeiro/último frame). |
| Achar uma fala | `find_phrase` | Busca aproximada nas transcrições de vários clipes. |
| Guardar o que foi visto | `annotate`, `read_annotations` | Log de decupagem persistente por clipe; vira marker no XML. |
| Corte exato | `snap_cut` | Nunca corta palavra no meio (move o ponto pra fora dela, no frame mais silencioso); `tighten=True` aperta nas palavras pra jump-cut; `mode="picture"` evita frame do plano seguinte. |
| Entregar | `nle_cut_list`, `export_timeline` | Números exatos pros MCPs do Premiere/Resolve, ou FCP7 XML / EDL CMX3600 que os dois importam. |

Também: `overview`, `library`, `transcript`, `quality`.

## Instalação

Requisitos: Python ≥ 3.10, `ffmpeg` e `ffprobe` no `PATH`. O motor de transcrição é escolhido sozinho:
CUDA (NVIDIA, extra `[cuda]`) → `faster-whisper`; Mac Apple Silicon → `mlx-whisper`; senão CPU. A primeira
transcrição baixa o `whisper-large-v3-turbo` (~1,6 GB).

### Mac (Apple Silicon)

```bash
brew install ffmpeg uv
uv tool install --python 3.12 git+https://github.com/bernardotraad/decupa.git
claude mcp add -s user decupa -- decupa
```

Atualizar: `uv tool upgrade decupa`.

### Windows com GPU via WSL (recomendado com placa NVIDIA)

Premiere e Resolve rodam no Windows; a análise roda na WSL, onde o CUDA é simples. O `decupa` traduz os caminhos
nos dois sentidos: você passa `D:\Gravacao\A001.mov`, ele lê `/mnt/d/Gravacao/A001.mov`, e tudo que devolve (e
escreve no XML/EDL) usa o caminho do Windows que o editor enxerga.

Na WSL (Ubuntu):

```bash
sudo apt install ffmpeg python3-pip
pip install --user --break-system-packages "decupa[cuda] @ git+https://github.com/bernardotraad/decupa.git"
which decupa   # ex.: /home/<voce>/.local/bin/decupa
```

No **Windows** (PowerShell), registre no Claude Code pra ficar junto dos MCPs do Premiere/Resolve:

```powershell
claude mcp add -s user decupa -- C:\Windows\System32\wsl.exe -d Ubuntu -- /home/<voce>/.local/bin/decupa
```

### Windows / Linux nativo

```bash
pip install "decupa[cuda] @ git+https://github.com/bernardotraad/decupa.git"   # sem NVIDIA, tire o [cuda]
claude mcp add -s user decupa -- decupa
```

### Claude Desktop

Em `claude_desktop_config.json`: `"decupa": { "command": "decupa" }`. Com WSL:
`"command": "C:\\Windows\\System32\\wsl.exe", "args": ["-d", "Ubuntu", "--", "/home/<voce>/.local/bin/decupa"]`.

Conferir: `claude mcp list` → `decupa: … ✓ Connected`.

## Usando com os MCPs do Premiere e do Resolve

O `decupa` não fala com os editores — ele foi feito pra rodar **na mesma sessão do Claude** que um MCP de editor.
Testado junto com:

- **Premiere Pro** — [leancoderkavy/premiere-pro-mcp](https://github.com/leancoderkavy/premiere-pro-mcp)
  (também como extensão do Claude Desktop)
- **DaVinci Resolve** — [hiteshK03/davinci-resolve-mcp](https://github.com/hiteshK03/davinci-resolve-mcp)

Registre todos no mesmo cliente, abra o editor e peça em linguagem natural. Há dois jeitos de entregar o corte.

### A. Arquivo de timeline (mais robusto, serve pros dois)

`export_timeline(plano, "D:\\Edicao\\assembly_v1.xml")` grava um FCP7 XML (ou `format="edl"`) com in/out exatos,
áudio vinculado e as anotações do `annotate` como markers.

- **Resolve:** `import_timeline_from_file(file_path="D:\\Edicao\\assembly_v1.xml")`
- **Premiere:** `import_media(file_paths=["D:\\Edicao\\assembly_v1.xml"])` — vira uma sequência (ou Arquivo › Importar).

### B. Clipe a clipe pelo MCP do editor

`nle_cut_list(plano)` devolve, por clipe: `in_frame`, `out_frame_exclusive`, `end_frame_inclusive`, `in_sec`,
`out_sec`, timecodes, `record_frame` e o caminho que o editor vê.

- **Resolve:** `import_media([...])` → `insert_to_timeline(clip_name, track_index=1, record_frame, start_frame=in_frame, end_frame=end_frame_inclusive)`
- **Premiere:** `import_media([...])` → `add_to_timeline(item_id, track_index=0, start_seconds=<tempo na timeline>)` →
  `trim_clip(node_id, new_out_seconds=out_sec)` e depois `trim_clip(node_id, new_in_seconds=in_sec)` (um ponto por
  chamada; trilhas começam em 0). Com muitos clipes, o caminho A é mais simples.

### O plano de corte

```json
{
  "name": "assembly v1",
  "fps": "30000/1001",
  "clips": [
    {"path": "D:\\Gravacao\\MVI_7965.MP4", "in": 17.5, "out": 25.0, "snap": "speech", "label": "cena 3 p3 take 3"},
    {"path": "D:\\Gravacao\\MVI_7984.MP4", "in": 3.0,  "out": 9.0,  "snap": "picture", "label": "reação"},
    {"path": "D:\\Gravacao\\MVI_7975.MP4", "in_frame": 300, "out_frame": 481}
  ]
}
```

`in`/`out` em segundos (ajustados a frames, opcionalmente com `snap`); `in_frame`/`out_frame` são frames exatos da
origem (out exclusivo). `fps` padrão = do primeiro clipe.

## Uma sessão típica

> Indexa `D:\Gravacao\Dia1`. Acha o melhor take de cada cena (normalmente o último completo) e confere olhando os
> frames. Depois cataloga os b-rolls bons sem fala com uma descrição curta de cada. Monta uma timeline
> "assembly v1" no Resolve com os melhores takes na ordem das cenas, e um bin com os b-rolls.

Por baixo: `index_folder` → `scenes` → `contact_sheet` do vencedor e do 2º (e `timeline_strip` se estiver
apertado) → `broll` nos clipes sem claquete → `contact_sheet` + `annotate` → `snap_cut` → `export_timeline` ou
`nle_cut_list` → MCP do Resolve importa/insere.

Pedidos curtos que funcionam bem:

- *"Me mostra um contact sheet do A003 de 10 a 40 s."*
- *"Qual take da cena 12 tem a fala mais limpa? Compara os dois últimos com timeline strip."*
- *"Onde ela fala 'hoje eu sou da peça também'? Procura em todos os clipes."*
- *"Acha b-rolls estáveis e nítidos com mais de 3 s nessa pasta e descreve cada um."*

## Da decupagem a um corte de verdade

O `decupa` também **monta**: gera um MP4 para revisão a partir do material decupado, cortado na música, e um FCP7 XML
do mesmo corte para finalizar no Premiere/Resolve.

Comece pelo prompt **`montar`** (no Claude Code: `/mcp__decupa__montar`). Ele faz uma entrevista curta, uma
pergunta por vez: tipo de vídeo, ideia, formato, duração, legendas, **trilha primeiro**, ritmo, som direto,
abertura/fecho. Depois decupa olhando o material e entrega uma primeira versão.

| Etapa | Ferramenta | O que faz |
| --- | --- | --- |
| Trilha primeiro | `music_map`, `music_windows` | BPM (com leitura half/double-time), grade de beats, compassos, seções de energia, drop, compassos vazios. Sugere o trecho da música que cabe na duração. |
| Som externo | `sync_audio` | Offset entre câmera e gravador. Diz `reliable: false` quando os arquivos não têm o mesmo som (ex.: locução). |
| Multicâmera | `multicam_sync` (`montage.py`) | Sincroniza cada câmera num master longo (mesa de som): envelope + refinamento na forma de onda + drift. Usa **transcrição como fallback** quando os microfones soam diferentes demais. Validado contra um sync feito no PluralEyes (show de 1h53): 0–2 frames. |
| Vertical | `reframe_preview`, `set_rotation` | Mostra o crop 9:16 em frames reais. `reframe_x: "auto"` segue o assunto em foco a cada plano. `set_rotation` corrige câmera gravada deitada sem metadado. |
| Montar | `build_edit` | Encaixa no grid. **V1**: fala mantém a duração e o rabo vai até o próximo beat; o resto em beats, mais rápido nas partes de energia alta. **V2**: b-roll por cima da fala. Multicâmera: o plano só diz a câmera e segue o relógio do master. Avisa jump cut, buraco, corte fora do beat. Cada build é uma versão nova. |
| Render | `render_edit` | Crop + grade de prévia, trilha **abaixando sob a fala**, legendas da transcrição (um bloco por vez, pontuação limpa, tabela `fix` pra nomes), títulos, fades, −14 LUFS. Devolve o MP4 e um **storyboard** do arquivo renderizado, que o Claude revisa antes de te mostrar. |
| Finalizar | `export_edit` | FCP7 XML: V1, V2 com Basic Motion (escala/centro/rotação), A1/A2 com nível por plano, A3/A4 trilha. |

Nomes que o Whisper precisa acertar (marca, pessoas, lugares) vão em `vocabulary` no `index_media`/`index_folder`.
Eles são aplicados como correção por similaridade ("Enki Empreendimento" → "INC Empreendimentos"), nunca como dica
pro Whisper: no prompt ele escreve a dica na transcrição, e em hotwords ele perde palavras.

## Como funciona

Um decode do `ffmpeg` por clipe gera dois fluxos: frames minúsculos em taxa cheia (detecção de corte exata por
frame, que ignora objetos passando rápido na frente da lente) e frames de 320 px a 4 fps (nitidez por Laplaciano
relativa ao melhor foco do próprio clipe, altas estouradas/pretos esmagados, movimento global por correlação de
fase → parada / pan / tilt / tremida). O áudio vira um envelope RMS de 20 ms (silêncios, pontos de corte mais
silenciosos). O Whisper roda com timestamps por palavra, VAD e `condition_on_previous_text=False` pra transcrever
takes repetidos em vez de "deduplicar".

Cache em `~/.cache/decupa/<arquivo>-<hash>/` (por caminho + tamanho + data): `index.json`, `words.json`, `q.npy`,
`rms_db.npy`, `log.json`.

## Configuração

| Variável | Padrão | |
| --- | --- | --- |
| `DECUPA_CACHE` | `~/.cache/decupa` | Cache dos índices |
| `DECUPA_WHISPER` | `large-v3-turbo` | Modelo do faster-whisper |
| `DECUPA_MLX_MODEL` | `mlx-community/whisper-large-v3-turbo` | Modelo do mlx-whisper |
| `DECUPA_BACKEND` | auto | Força `mlx` ou `faster-whisper` |

Idioma padrão: português (`language="pt"`); passe `"en"`, `"es"`… ou `""` pra detectar. A claquete é entendida em
português e inglês.

## Limitações

- Câmeras sem timecode embutido (ex.: Canon `MVI_*.MP4`) começam em `00:00:00:00`; os frames continuam exatos.
- Um fps por timeline exportada (planos com fps misturado são recusados — faça uma por fps).
- Ranking é evidência, não veredito. Em atuação, olhe o vencedor e o 2º.
- O XML/EDL foi validado estruturalmente; o comportamento pode variar entre versões do Premiere/Resolve — issues
  são bem-vindas.

## Licença

[MIT](LICENSE)
