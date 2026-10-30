---
nome: podcast
titulo: Podcast / videocast
quando: conversa gravada com uma câmera por pessoa (ou um plano aberto) e um microfone por pessoa; episódio inteiro para YouTube, às vezes com cortes depois
padroes: {"aspect": "16:9", "duracao": "episódio inteiro", "pace": "calm", "legendas": "srt separado", "musica": "vinheta de abertura e fechamento", "cut_on": "none"}
verificar: {"plataforma": "youtube"}
entregas: ["synced_sequence", "timeline", "legendas_srt"]
ferramentas: ["index_folder", "sync_multicam", "multicam_monitor", "people", "name_people", "auto_cut_multicam", "clean_speech", "project_digest", "save_project_overview", "caption_sentences", "captions_srt", "qc_timeline"]
---

## Brief

1. Quem está no episódio: nome de cada pessoa, qual câmera mostra quem (e qual é o plano aberto) e qual microfone é de
   quem. Se o editor não souber, descubra: people (rostos e vozes) → mostre o quadro de rostos → name_people com os nomes
   confirmados.
2. Entrega: episódio inteiro · episódio + cortes para redes (os cortes seguem a receita cortes-de-video-longo depois).
3. Abertura e fechamento: vinheta (arquivo), gancho com a melhor fala do episódio antes da vinheta, ou direto na conversa.
4. Quanto limpar: só tempo morto (pace solto) · ritmo de conversa (médio) · enxuto (apertado). Em podcast o padrão é solto.

## Etapas

1. index_folder em tudo (câmeras e gravadores) e, com várias câmeras, sync_multicam (correct_drift true) →
   multicam_monitor em 2–3 momentos para conferir a sincronia.
2. auto_cut_multicam(cameras = {trilha: [pessoas]}, mics = {pessoa: trilha do mic}) na sequência sincronizada: troca de
   câmera por quem fala, plano aberto nas falas cruzadas e nas pausas longas, as câmeras alternativas desligadas logo
   abaixo (o editor troca um corte com um clique).
3. Depois que o editor revisar o corte de câmera: clean_speech com o pace escolhido (preview=True primeiro, se ele
   estiver em dúvida). Notas faladas para o editor ("editor, …", "vou de novo", "errei") saem com marcador.
4. VISÃO GERAL do episódio: project_digest → save_project_overview com os temas na ordem em que aparecem e o tempo de
   cada um — serve de lista de capítulos para a descrição do YouTube e de mapa para os cortes.
5. Legendas: .srt separado (captions_srt) para subir na plataforma.
6. qc_timeline(platform "youtube"): loudness do episódio, picos, níveis de fala que pulam entre as pessoas.

## Legendas

As regras comuns valem; em episódio longo, revise a gramática por blocos de 10–15 minutos para a tabela não ficar
enorme.

## Regras de gosto

- Quem fala aparece; reação de quem escuta só quando ela conta alguma coisa (riso, surpresa).
- Nunca cortar no meio da frase de quem fala para mostrar outra pessoa.
- Pausa de pensamento faz parte da conversa: limpe tempo morto, não a respiração da fala.
- Os níveis de voz das pessoas ficam iguais entre si; música de vinheta nunca por baixo da conversa.
