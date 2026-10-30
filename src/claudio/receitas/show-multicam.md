---
nome: show-multicam
titulo: Show / clipe musical multicâmera
quando: apresentação musical ou palco gravado por várias câmeras ao mesmo tempo, com o som da mesa ou de um gravador
padroes: {"aspect": "16:9", "duracao": "música inteira", "pace": "medium", "pace_follows_music": true, "audio": "som do show", "cut_on": "downbeat"}
verificar: {"plataforma": "youtube"}
entregas: ["synced_sequence", "timeline"]
ferramentas: ["index_folder", "sync_multicam", "multicam_monitor", "music_map", "build_edit", "edit_storyboard", "export_edit", "qc_timeline"]
---

## Brief

1. Câmeras e gravadores: liste as pastas do trabalho e proponha o mapeamento pasta → câmera (nome curto, ex.: "CAM A —
   A7III") → gravador externo (mesa, Zoom, lapela) → cor de label por câmera, numa tabela. PERGUNTE antes de rodar.
2. Entrega: só a sequência sincronizada (o editor corta) · sincronizada + corte da música · uma música ou o show inteiro.
3. Formato (16:9 · 9:16 com reframe) e se haverá cortes para rede social depois.

## Etapas

1. sync_multicam(correct_drift true, labels com as cores) → resuma: clipes posicionados, os aproximados (sem áudio em
   comum, posicionados pelo relógio da câmera) e o drift do gravador.
2. multicam_monitor em 2–3 momentos em que todas as câmeras estão rodando; diga se algo parece fora de sincronia.
3. Entregue a Synced Sequence (V1..Vn por câmera, cada câmera na sua cor). Se o editor quiser só isso, pare aqui.
4. Corte da música: music_map no áudio do show (seções: verso, refrão, solo) → shots multicam ({"path", "offset", "beats",
   "reframe_x": "auto", "grade": "auto"}) trocando de câmera nas mudanças de seção e nos tempos fortes → build_edit
   (áudio do show como trilha, sem música por cima) → edit_storyboard → OLHAR → export_edit.

## Regras de gosto

- Solo → quem está tocando; voz → quem canta; refrão → abre para o palco ou o público.
- Câmera sem ninguém em quadro ou fora de foco não entra, mesmo no tempo.
- Corte no tempo forte, nunca no meio de uma palavra cantada.
- Para podcast, entrevista ou mesa redonda com várias câmeras, auto_cut_multicam corta pelo microfone de quem fala.
