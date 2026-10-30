---
nome: recap-evento
titulo: Aftermovie / resumo de evento
quando: material de um evento (festa, show, palestra, lançamento, casamento) e uma música; pouca ou nenhuma fala
padroes: {"aspect": "9:16", "duracao": 45, "pace": "fast", "audio": "mute", "cut_on": "beat"}
entregas: ["timeline"]
botao: {"rotulo": "Recap de evento", "dica": "Aftermovie do evento: melhores momentos no beat da música"}
ferramentas: ["index_folder", "index_status", "music_map", "music_windows", "event_recap", "edit_storyboard", "build_edit", "export_edit", "qc_timeline"]
---

## Brief

Uma chamada só: duração (15 · 30 · 45 · 60 s) · formato (9:16 · 16:9 · 4:5) · ritmo (rápido · médio · hype) · música
(uma da pasta do trabalho, um arquivo do editor, ou opções de uma loja de música licenciada pelo mood, como na receita
depoimento) · som (só a música · ambiente baixo por baixo) · cartela final (arquivo, se houver).

## Etapas

1. Confira se o material está indexado (index_status; se não, index_folder e espere).
2. music_map na faixa escolhida; music_windows(duração) para o trecho, se o editor não tiver um.
3. event_recap(project, footage, music, duration, aspect, pace, name=<nome do evento>, end_card, audio) — escolhe os
   melhores momentos (estáveis e nítidos, barulhentos: público, refrão, aplauso; com gente; pouca fala), o mais forte
   como gancho, o resto na ordem em que aconteceu, sem repetir ângulo, cortado no tempo e ajustado à duração.
4. edit_storyboard → OLHE e troque o que não estiver bom (plano fora de foco, repetido, sem gente) com build_edit.
5. export_edit e entregue o caminho do XML. Resuma em 3–5 linhas o que entrou e por quê.

## Regras de gosto

- O momento mais forte da noite abre; o resto segue a cronologia, para o vídeo contar o evento.
- Gente acima de lugar vazio; reação (rosto, mão, pulo) acima de plano geral parado.
- Corte no tempo da música; no drop ou refrão, cortes mais curtos.
- Fala só entra se for curta e carregar o evento (um grito, um brinde); senão, só música.
