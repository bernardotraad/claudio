# decupa — MCP que enxerga e ouve o material antes de cortar

Os MCPs do Premiere e do Resolve **executam** cortes, mas não veem o vídeo. O `decupa` é a camada de
"olhos e ouvidos": analisa a mídia de verdade e devolve evidência — transcrição com tempo por palavra, claquete
falada, planos, foco/exposição/movimento e **imagens** (contact sheets, frames, timeline com waveform) com
timecode gravado. A decisão de corte sai daí, em frames, e vai para o Premiere/Resolve.

## Instalação

Precisa de `ffmpeg`/`ffprobe` no PATH. Whisper: CUDA (NVIDIA) → `faster-whisper`; Apple Silicon → `mlx-whisper`
(instalado automaticamente no Mac); senão CPU.

### Windows (GPU via WSL — recomendado)
Na WSL (Ubuntu, com driver NVIDIA do Windows):
```bash
sudo apt install ffmpeg
pip install --user --break-system-packages -e "/mnt/d/Documentos/GitHub/decupa[cuda]"
```
No Claude Code do Windows (PowerShell):
```powershell
claude mcp add -s user decupa -- C:\Windows\System32\wsl.exe -d Ubuntu -- /home/bernardo/.local/bin/decupa
```
Caminhos `D:\...` são traduzidos automaticamente; as saídas voltam em caminho Windows (o que o Premiere/Resolve veem).

### Mac (Apple Silicon)
```bash
brew install ffmpeg uv
uv tool install --python 3.12 "git+ssh://git@github.com/bernardotraad/decupa.git"
claude mcp add -s user decupa -- decupa
```
Atualizar: `uv tool upgrade decupa`. A primeira transcrição baixa o modelo `whisper-large-v3-turbo` (~1,6 GB).

### Claude Desktop
`claude_desktop_config.json` → `"decupa": {"command": "<mesmo comando de cima>", "args": [...]}`.

Cache dos índices: `~/.cache/decupa/<arquivo>-<hash>/` (index.json, words.json, q.npy, rms_db.npy, log.json).

## Fluxo
1. `index_folder("D:\\Gravacao\\08.08")` → fila em background (≈ 4 min por 15 min de material na RTX 3060); `index_status`
2. `scenes(folder)` → agrupa por claquete falada (cena/take/parte/plano), corta entre **"ação"** e **"corta"**,
   marca takes abortados ("peraí", "de novo"), compara as falas com a versão consenso e ranqueia
   (`prefer="last"`: o último take completo tende a ganhar). `mode` diz se julga por fala, atuação (improviso)
   ou imagem (cena sem diálogo).
3. Material sem fala / b-roll: `broll(path)` → janelas utilizáveis por movimento de câmera
   (static / pan / tilt), foco relativo ao próprio clipe, exposição e tremido; trechos rejeitados com motivo.
4. **Olhar**: `contact_sheet` (grade com TC + fala do instante), `timeline_strip` (thumbs + waveform + palavras
   no mesmo eixo, pra in/out fino), `frames` (frame cheio em tempos exatos).
5. `annotate` → o que foi visto vira log de decupagem persistente (sai como marker no XML).
6. `snap_cut` → in/out por frame sem cortar palavra (ou `tighten=True` pra jump-cut), sem frame de outro plano.
7. Entrega:
   - `export_timeline(plan, "D:\\...\\corte.xml")` → FCP7 XML: Premiere importa direto; Resolve via
     `import_timeline_from_file` do MCP do Resolve. `format="edl"` gera CMX3600.
   - `nle_cut_list(plan)` → números prontos pros MCPs: Resolve `insert_to_timeline(start_frame=in_frame,
     end_frame=end_frame_inclusive, record_frame)`; Premiere insert/overwrite com in/out em segundos.

## Exemplo de pedido (com Premiere/Resolve abertos)
> Decupa a pasta D:\…\08.08: melhor take de cada cena (normalmente o último), confere olhando os frames, e
> separa os b-rolls bons sem fala com descrição. Depois monta uma timeline "assembly v1" no Resolve com os
> melhores takes na ordem das cenas.

## Limites conhecidos
- Sem timecode na câmera (ex.: Canon MVI) o TC de origem começa em 00:00:00:00 — in/out em frames continuam exatos.
- Mistura de fps num mesmo plano de exportação é recusada (gerar uma timeline por fps).
- O ranking é evidência, não veredito: em atuação, olhar `contact_sheet` / `timeline_strip` do melhor e do 2º.
- Importação do XML ainda não validada dentro do Premiere/Resolve.
