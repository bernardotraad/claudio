---
nome: beat-edit
titulo: Beat edit / hype sem fala
quando: só imagem e música, sem narração nem depoimento (teaser, vídeo de produto, hype de marca, reel de lugar)
padroes: {"aspect": "9:16", "duracao": 30, "pace": "hype", "audio": "mute", "cut_on": "beat", "pace_follows_music": true}
verificar: {"plataforma": "reels"}
entregas: ["timeline"]
ferramentas: ["index_folder", "broll", "find_shot", "contact_sheet", "music_map", "music_windows", "build_edit", "edit_storyboard", "export_edit", "render_overlay", "qc_timeline"]
---

## Brief

1. IDEIA: o que o vídeo vende ou mostra, a sensação (energia, luxo, calma, humor), quem assiste.
2. AJUSTES (uma chamada): formato · duração · ritmo (segue a música · rápido · hype) · textos na tela (nenhum · palavras
   grandes no tempo · título e CTA).
3. MÚSICA PRIMEIRO (como na receita depoimento): é ela que dá a estrutura. music_map → seções, drop, silêncios;
   music_windows(duração) → 2–3 trechos.

## Etapas

1. broll em cada clipe → janelas utilizáveis (estáveis, em foco, expostas) com o movimento de câmera de cada uma.
2. find_shot para os planos que a ideia pede ("close do produto", "pessoa rindo") e contact_sheet em cada candidato.
3. Monte o mapa: cada seção da música recebe um tipo de plano (abertura calma → planos abertos e lentos; subida → mais
   curtos; drop → os mais fortes, 1–2 tempos cada).
4. build_edit com "cut_on": "beat" (ou "downbeat" nos trechos calmos), "pace_follows_music": true, shots com "beats" →
   edit_storyboard → OLHAR → export_edit.
5. Textos: render_overlay (motion_reference antes) numa trilha nova, no tempo dos acentos.

## Regras de gosto

- Movimento conversa com movimento: pan continua no mesmo sentido do plano seguinte, ou corta no contrário de propósito.
- O plano mais forte fica guardado para o drop.
- Nada de plano repetido; nada de plano mole no drop.
- Termina no último tempo forte da música, não no meio da frase musical.
