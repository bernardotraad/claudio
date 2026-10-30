---
nome: cortes-de-video-longo
titulo: Cortes de um vídeo longo (shorts / reels)
quando: um vídeo longo já gravado ou publicado — podcast, palestra, live, entrevista — de onde saem vários vídeos curtos verticais
padroes: {"aspect": "9:16", "duracao": 45, "pace": "fast", "legendas": "social", "musica": "não", "cut_on": "none", "quantidade": 5}
verificar: {"plataforma": "reels", "gancho_s": 0.5}
entregas: ["timeline", "legendas_srt"]
ferramentas: ["index_folder", "project_digest", "transcript", "find_phrase", "people", "find_person", "snap_cut", "reframe_preview", "build_edit", "edit_storyboard", "export_edit", "clean_speech", "caption_sentences", "caption_style_preview", "captions_srt", "caption_overlay", "add_sfx", "qc_timeline"]
---

## Brief

1. Quantos cortes (3 · 5 · 10) e de quanto tempo (15–30 · 30–60 · até 90 s).
2. Para onde vão (reels · tiktok · shorts) e o que o canal quer: crescer (polêmica, opinião forte), ensinar (dica
   completa), vender (prova, resultado, CTA).
3. Algum trecho obrigatório ou proibido (assunto sensível, convidado que não pode aparecer).

## Etapas

1. index_folder no vídeo longo (ou nas câmeras dele) → project_digest → transcript dos trechos promissores.
2. Proponha o DOBRO de candidatos, numa tabela: tempo, primeira frase (o gancho), a ideia em uma linha, duração, por
   que funciona sozinho. O editor escolhe; não monte antes disso.
3. Cada corte escolhido precisa ficar de pé sozinho: começa numa frase que prende sem contexto (se a boa frase vem no
   meio, abra com ela e volte ao começo do raciocínio), termina numa conclusão, não num "e aí…". snap_cut (mode speech)
   em cada emenda.
4. Vertical a partir de 16:9: people / find_person para saber quem fala; reframe_preview com reframe_x no rosto de quem
   fala, conferido em 2–3 instantes do trecho; em conversa, o reframe troca de pessoa a cada fala.
5. Um build_edit por corte, cada um no seu project (<projeto>/cortes/<n>), com nome "Canal — Corte n — tema" →
   edit_storyboard → export_edit.
6. clean_speech com pace apertado em cada corte (vertical pede ritmo), punch só nas falas que o editor aprovar.
7. Legendas sociais (caption_sentences → caption_style_preview → captions_srt / caption_overlay) e, se o editor quiser,
   add_sfx.
8. qc_timeline(platform conforme o destino) em cada um.

## Regras de gosto

- Os primeiros 2 segundos decidem tudo: nada de "então", "bom", "como eu tava falando" abrindo um corte.
- Um corte = uma ideia. Duas ideias boas viram dois cortes.
- O rosto de quem fala fica no terço de cima da tela vertical, longe da legenda.
- O corte termina na conclusão ou numa frase que dá vontade de ver o episódio inteiro.
