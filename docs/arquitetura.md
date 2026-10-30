# Arquitetura: motor, receitas e perfil

> Aprovada em 2026-10-30. Fases 1, 2, 3, 5 e 6 implementadas; da 4 falta só o `export_edit` para HyperFrames (opção de uso raro). Objetivo: o Cláudio servir para qualquer tipo de projeto e de
> editor, sem perder nada do fluxo que existe hoje.

## O problema

Hoje três coisas diferentes estão misturadas no mesmo código:

| O quê | Exemplo atual | Onde está |
|---|---|---|
| **Como medir e montar** (técnica) | transcrição, takes, blocos de legenda por frase, XML FCP7 | `core`, `captions`, `montage`… |
| **Que tipo de vídeo é** (receita) | "trilha primeiro", "melhor take = o último completo", recap no beat | prompt `MONTAR` em `server.py`, prompts dos botões em `panel_premiere/main.js`, docstrings |
| **Como este editor trabalha** (perfil) | `#projeto`, `#trilhas`, `#legendas`, `BRUTOS/00 AUDIO`, material em log, Premiere, Storyblocks, vault | strings fixas em 22 lugares, docstrings com "INC — Estela" |

Consequências:
- Para outra pessoa, ou para um projeto que não é depoimento, o Cláudio "puxa" o seu jeito: pastas com `#`,
  pressupõe Premiere, oferece Storyblocks, fala de LUT.
- Custo: as descrições das 55 ferramentas somam cerca de 26 mil caracteres (≈ 7 mil tokens) e vão em **toda**
  mensagem, com o roteiro inteiro de montagem dentro delas, mesmo quando a tarefa é só "QC".
- Receita nova (podcast, tutorial) hoje exige mexer em `server.py`, no `MONTAR` e no painel.

## A separação

```
            pedido do editor
                  │
        ┌─────────▼──────────┐
        │      RECEITA       │  que vídeo é isso: perguntas, etapas, regras de gosto, padrões, entregas
        │ (Markdown, lida    │  ex.: depoimento · recap · beat edit · show multicam · podcast · tutorial · shorts
        │  sob demanda)      │
        └─────────┬──────────┘
                  │ brief (save_brief)
        ┌─────────▼──────────┐       ┌───────────────────────────┐
        │       MOTOR        │◄──────┤          PERFIL           │
        │ ferramentas puras: │ lê    │ workspace: pastas, NLE,   │
        │ medir, ver, ouvir, │       │ idioma, cor, música, STT  │
        │ cortar, edit JSON  │       │ cliente: vocabulário,     │
        └─────────┬──────────┘       │ formato, estilo, cartela  │
                  │ edit JSON        └───────────────────────────┘
        ┌─────────▼──────────┐
        │      DESTINOS      │  premiere (XML + painel) · resolve · hyperframes (MP4) · edl/json
        └────────────────────┘
```

**Regra de ouro:** o motor não sabe o nome de nenhuma pasta, cliente, NLE ou loja de música. Ele recebe caminhos e
parâmetros. Quem resolve "onde fica a legenda" é o perfil; quem decide "o que fazer agora" é a receita.

Ordem de prioridade dos valores (o de baixo vence):
`padrão do motor` < `receita` < `perfil do workspace` < `perfil do cliente` < `brief do trabalho` < `pedido na conversa`

---

## 1. Motor

Continua sendo o pacote `src/claudio`, sem renomear módulos (nada de reescrever). O que muda:

- **Sem caminhos fixos.** `'#legendas'`, `'#trilhas'`, `'#projeto'`, `'BRUTOS'`, `'00 AUDIO'`, `'03 FOTOS'`,
  `claudio_overlays`, `claudio_backups` passam a vir de `profile.folder('legendas', job)`.
- **Docstrings técnicas e curtas.** Dizem o que a ferramenta faz, o que recebe e o que devolve. Saem delas: exemplos de
  cliente, roteiro de etapas, regras de gosto, avisos específicos do Premiere. Esses textos vão para a receita ou
  para o destino.
- **Uma ferramenta nova, `recipe(name=None)`**, no estilo de `motion_reference`: sem nome lista as receitas, com nome
  devolve a receita inteira. O modelo só carrega o roteiro quando precisa dele.
- **`montar` fica genérico:** pergunta o tipo (ou deduz do material), chama `recipe(tipo)` e segue.

O que já é motor e fica como está: `core`, `indexer`, `visual`, `people`, `describe`, `captions` (algoritmo dos
blocos), `cleanup`, `multicam`, `autocut`, `montage` (edit JSON → fit), análise de música em `music`, `qc`, `stereo`,
`sfx`, `nle_read`, `motion` (render de overlay).

## 2. Receitas

Uma receita = um tipo de projeto. Arquivo Markdown com frontmatter, legível por gente e por qualquer agente (mesma
ideia das Agent Skills e do `/brag`: a receita planeja, o motor executa).

```
src/claudio/receitas/            ← vêm com o Cláudio
  depoimento.md                  (o fluxo INC de hoje, sem nada do INC)
  recap-evento.md                (o que hoje é event_recap + passo 2 do MONTAR)
  beat-edit.md
  show-multicam.md
  curta-narrativo.md
  podcast.md                     ← novas
  tutorial.md
  cortes-de-video-longo.md
~/.config/claudio/receitas/      ← do usuário: cria novas ou substitui uma de cima pelo mesmo nome
```

Formato:

```markdown
---
nome: depoimento
titulo: Depoimento / entrevista com b-roll
quando: uma ou mais pessoas falando para a câmera, com material de apoio
padroes: {aspect: "9:16", duracao: 60, pace: medium, legendas: social, musica: sim}
entregas: [timeline, legendas_srt]
ferramentas: [index_folder, scenes, takes, broll, find_phrase, build_edit, edit_storyboard, export_edit, captions_srt]
---

## Brief (perguntar só o que o perfil do cliente não responde)
1. Mensagem principal e quem assiste.
2. Falas obrigatórias, pessoas, ordem.
…

## Etapas
1. index_folder → ler os <clipe>.md se já existirem.
2. scenes / takes: melhor take = o último completo; take que o falante criticou está fora.
…

## Regras de gosto
- Gancho nos 3 primeiros segundos.
- Nunca o mesmo ângulo duas vezes seguidas.
…
```

De onde sai o conteúdo das receitas que vêm com o Cláudio:
- `MONTAR` (passos 0–12) vira a base comum e a receita `depoimento`.
- O passo 2 (os 5 tipos) vira a lista de receitas.
- Os textos de `event_recap`, `auto_cut_multicam` e `clean_speech` sobre *quando* e *por que* usar vão para as
  receitas `recap-evento`, `show-multicam` e para as etapas de limpeza.
- Os prompts dos botões do painel (`selects`, `overview`, `notes`, `captions`, `vault`) viram etapas nomeadas:
  o botão pede `recipe('depoimento', etapa='legendas')` em vez de carregar o texto no `main.js`.

## 3. Perfil

Dois níveis, os dois já existem pela metade:

**Perfil do workspace** (novo): como esta pessoa ou estúdio trabalha. Fica em `~/.config/claudio/perfil.json`, ao lado do
`settings.json` atual. É configuração da máquina, então não vai para o repo nem para o vault.

```json
{
  "idioma": "pt",
  "nle": "premiere",
  "pastas": {
    "brutos": "BRUTOS", "audio": "BRUTOS/00 AUDIO", "fotos": "BRUTOS/03 FOTOS",
    "projeto": "#projeto", "trilhas": "#trilhas", "legendas": "#legendas",
    "overlays": "#projeto/claudio_overlays", "backups": "#projeto/claudio_backups"
  },
  "cor": {"material_em_log": true},
  "musica": {"fontes": ["storyblocks", "pasta"]},
  "stt": "whisper",
  "clientes": "/mnt/d/bernardo/vault/trabalho/clientes",
  "registro": "vault",
  "regras": ["nunca aninhar sequências", "mídia nova em trilha nova", "render só com autorização"]
}
```

O Cláudio passa a ter **padrões neutros** (`claudio/legendas`, NLE perguntado na primeira vez, sem loja de música,
sem vault). O seu `perfil.json` reproduz exatamente o comportamento de hoje. O `claudio-doctor` cria esse arquivo na
primeira execução perguntando 4 ou 5 coisas, ou detecta pelas pastas `#projeto` / `#trilhas` de um trabalho existente.

**Perfil do cliente** (já existe em `clients.py`): continua igual (vocabulário, formatos, cartela, música,
legendas, estilo medido). Duas mudanças: o caminho vem de `perfil.clientes` em vez da busca fixa pelo vault, e o
cliente pode dizer qual receita usa por padrão (`"receita": "depoimento"`).

## 4. Destinos

O que hoje está espalhado como "cuidado com o Premiere" vira conhecimento do destino:

| Destino | Saída | Regras que saem do motor |
|---|---|---|
| `premiere` | XML FCP7 com extensão de trilha estéreo, painel, leitura de `.prproj` | não importar XML pelo MCP; overlay pelo botão do painel; `add_to_timeline` é insert |
| `resolve` | XML FCP7 / `import_timeline_from_file` | — |
| `hyperframes` (futuro) | composição HTML → MP4 final, sem NLE | render local e determinístico |
| `arquivo` | EDL CMX3600 / JSON | — |

`export_edit(project, target=perfil.nle)`. O painel continua sendo do Premiere. Para outro NLE, as mesmas ações
rodam pelo `claudio-run` ou pela conversa.

---

## Migração em fases (cada uma entregável sozinha, sem mudar o seu resultado)

1. **Perfil do workspace.** ✅ 2026-10-30 — só `pastas`, `vault` e `clientes`; `idioma`, `nle`, `cor`, `musica` e
   `regras` entram nas fases em que forem usados. `claudio/profile.py` (`profile.get`, `profile.folder`) com os seus valores atuais como
   `perfil.json`; troca das 22 strings fixas (Python e JS). Teste: com o seu perfil, os caminhos gerados são
   idênticos aos de hoje; sem perfil, saem os neutros.
2. **Receitas.** ✅ 2026-10-30 — as 5 receitas do antigo passo 2 do `MONTAR` + `depoimento-horizontal` + `_comum.md`; o `montar` caiu de
   6,3 mil para 1,1 mil caracteres. Os prompts dos botões do painel continuam no `main.js` até a fase 5.
   Pasta `receitas/`, ferramenta `recipe`, `montar` genérico. `depoimento` e `recap-evento` primeiro,
   porque são as que você usa. Teste: o INC 9 Robson montado pela receita passa pelas mesmas etapas.
3. **Docstrings enxutas.** ✅ 2026-10-30 — o que vai em toda mensagem caiu de 59,6 mil para 40,4 mil caracteres
   (68 ferramentas: descrições 35,3 → 22,5 mil, esquemas 22,8 → 15,6 mil sem os `title` do pydantic, instruções
   1,5 → 2,3 mil). As regras repetidas (exportar a timeline antes, sequência nova, caminho completo para o painel, nunca
   importar XML pelo MCP do Premiere, trilha nova por overwrite) estão uma vez só nas instruções do servidor; roteiro
   foi para as receitas; sem exemplo de cliente nem caminho pessoal. `test_server` segura o orçamento.
4. **Destinos.** Parte 1 ✅ 2026-10-30: `claudio/targets.py` junta as regras de cada NLE (ler a timeline atual,
   importar o XML, o que nunca fazer) e as instruções do servidor levam só as do `nle` do perfil (sem `nle`, as duas
   e "pergunte uma vez"). Teste do HyperFrames (2026-10-30, `hyperframes@0.8.166`, Node 24 LTS na WSL, telemetria desligada):
   - lower third 1920×1080, 5 s, 29,97: `render --format mov` sai **ProRes 4444 com alpha** (`ap4h`,
     `yuva444p12le`, 150 quadros exatos); a transparência confere quadro a quadro. Lint, `check` e `doctor` ajudam.
   - 15,8 s e ~540 MB de pico; o `render_overlay` atual faz o mesmo tipo de quadro em 3,9 s (PNG-em-MOV) ou 11,5 s
     (ProRes). Boa parte dos 15,8 s é fixa (npx, Chrome, calibração) — em overlay curto ele não compensa.
   - Conclusão: não trocar o motor de overlay agora. O valor está em `export_edit(target="hyperframes")`: o corte
     inteiro, com o material de verdade, renderizado em MP4 sem NLE — o próximo teste, com um edit real e só com
     autorização para renderizar.
   - Corte inteiro (2026-10-30, INC 9 "INC - 01 Robson (limpo)", 28 s, 9:16): XML exportado pelo MCP do Premiere →
     script de teste (`~/spikes/hyperframes/robson/build.py`): ffmpeg prepara cada trecho (corte, LUT da Sony só no
     log com confiança média/alta, rotação, escala, ganho e keyframes de volume) e o HyperFrames compõe (posição,
     opacidade, ordem das trilhas, legenda .mov com alpha, áudio). Comparado quadro a quadro com o Premiere
     (`export_frame`): enquadramento, b-roll reenquadrado, logo, legenda e cortes batem; muda a cor (sem a Lumetri do
     editor, só o LUT) e faltam os Graphics de texto (endereços da cartela) e o texto dos MOGRT, que o XML não carrega.
     Tempo: 84 s de preparo + 112 s de render (Chrome sem GPU na WSL), ~620 MB; −16,6 LUFS integrado.
   - Achado: o Premiere EXPORTA o centro do Basic Motion em pixels da fonte (horiz × largura nativa), medido contra o
     export_frame (b-roll 231 px = 0,1198 × 1928). O `export_edit` do Cláudio escreve "±1 = meia tela". **Bug
     confirmado e corrigido no mesmo dia:** um XML do Cláudio com `reframe_x` 0 e 1 importado no Premiere deixou o
     b-roll inteiro fora do quadro (Position x = 4,43 da largura). `montage.fcp_center` escreve agora o deslocamento
     em pixels da sequência ÷ largura/altura do arquivo (também no clipe girado: a Sony deitada usa a altura do
     arquivo, 2160). Conferido no Premiere: os quatro casos caem a ≤ 2 px; `tests/test_fcp_center.py`.
5. **Painel a partir das receitas.** ✅ 2026-10-30 — botão "Montar com receita" (lista no chat, a do cliente
   marcada) e receitas com `botao` viram atalho no grupo Montar (o Recap saiu do HTML e veio da receita); numeração
   calculada na tela; prompt genérico "siga a receita X" com as pastas do perfil. Os prompts de etapa (legendas,
   selects, visão geral, notas, vault, multicam) continuam no `main.js`: são do painel, não de um tipo de projeto.
   Plano original: Os botões da etapa MONTAR vêm das receitas instaladas; os prompts saem do `main.js`.
6. **Receitas novas** ✅ 2026-10-30 (podcast, tutorial, cortes-de-video-longo; o teste de receitas aceita os argumentos
   reais das ferramentas e continua reprovando ferramenta inventada). No lugar da pasta `examples/` (o repo é público
   e o CI não roda Whisper), um **benchmark local**: `~/.config/claudio/benchmark.json` liga cada receita a um trabalho
   do editor; `claudio-run bench` confere o XML sem IA (formato, duração, QC, loudness, aninhadas, gancho — o que a
   receita declara em `verificar`) e guarda o histórico em `benchmark-results.jsonl`. Primeiro caso: INC 9 Robson
   (depoimento), 6 de 6. De quebra, o QC parou de acusar "passa do fim da mídia" em imagem parada (logo PNG).

## Decisões (2026-10-30)

- Receitas em Markdown com frontmatter.
- O perfil do workspace mora em `~/.config/claudio/perfil.json` (configuração da máquina, fora do repo e do vault).
- Padrões neutros no repo público; as convenções do Bernardo ficam só no perfil dele.
