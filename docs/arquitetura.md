# Arquitetura: motor, receitas e perfil

> Aprovada em 2026-10-30. Fase 1 (perfil do workspace) implementada; as outras ainda não. Objetivo: o Cláudio servir para qualquer tipo de projeto e de
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
2. **Receitas.** Pasta `receitas/`, ferramenta `recipe`, `montar` genérico. `depoimento` e `recap-evento` primeiro,
   porque são as que você usa. Teste: o INC 9 Robson montado pela receita passa pelas mesmas etapas.
3. **Docstrings enxutas.** Tirar roteiro, exemplos de cliente e regras de NLE das 55 ferramentas. Meta: cortar
   mais da metade dos ~26 mil caracteres que vão em toda mensagem.
4. **Destinos.** `export_edit(target=…)`; regras do Premiere juntas em um lugar só; depois, o teste do HyperFrames.
5. **Painel a partir das receitas.** Os botões da etapa MONTAR vêm das receitas instaladas; os prompts saem do `main.js`.
6. **Receitas novas** (podcast, tutorial, cortes de vídeo longo) e uma pasta `examples/` com material curto de cada
   tipo como suíte de testes, no estilo do `brag`.

## Decisões (2026-10-30)

- Receitas em Markdown com frontmatter.
- O perfil do workspace mora em `~/.config/claudio/perfil.json` (configuração da máquina, fora do repo e do vault).
- Padrões neutros no repo público; as convenções do Bernardo ficam só no perfil dele.
