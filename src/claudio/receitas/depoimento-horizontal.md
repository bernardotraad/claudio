---
nome: depoimento-horizontal
titulo: Depoimento horizontal (16:9)
quando: depoimento ou entrevista para YouTube, site, apresentação ou institucional, em 16:9, normalmente mais longo que o vertical e com uma ou duas câmeras por pessoa
padroes: {"aspect": "16:9", "duracao": 120, "pace": "calm", "legendas": "srt separado", "musica": "baixa, sob a fala", "cut_on": "none"}
verificar: {"plataforma": "youtube", "gancho_s": 2.0}
entregas: ["selects", "timeline", "legendas_srt"]
ferramentas: ["index_folder", "sync_multicam", "multicam_monitor", "project_digest", "save_project_overview", "make_selects", "takes", "scenes", "broll", "find_phrase", "find_shot", "people", "find_person", "music_map", "build_edit", "edit_storyboard", "export_edit", "clean_speech", "render_overlay", "caption_sentences", "captions_srt", "qc_timeline"]
---

## Brief

1. IDEIA (mensagem livre): do que se trata, a mensagem principal, falas/pessoas obrigatórias, quem assiste e onde
   passa (YouTube, site, telão, apresentação).
2. AJUSTES (uma chamada): duração (60 · 90 · 120 · 180+ s) · câmeras (uma · plano aberto + fechado por pessoa) ·
   legendas (.srt separado para a plataforma · queimadas discretas · não) · NLE (Premiere · Resolve).
3. IDENTIFICAÇÃO: nome e cargo de cada pessoa para o lower third, escritos como o cliente quer (vocabulário do perfil).
4. MÚSICA (como na receita depoimento, mas a fala manda): faixa calma, sem drop, baixa por baixo da fala. Sem música
   também é uma resposta boa para institucional.
5. ABERTURA E FECHAMENTO: abre com b-roll + a frase mais forte, ou com a pessoa direto · cartela final/logo (arquivo).

## Etapas

1. Duas câmeras na mesma pessoa (aberto + fechado) ou gravador externo: sync_multicam (correct_drift true) →
   multicam_monitor em 2–3 momentos para conferir a sincronia.
2. VISÃO GERAL: project_digest → save_project_overview → conte em 4–6 linhas → proponha 2–4 direções (ordem dos temas,
   o que abre, o que fecha).
3. SELECTS: make_selects → o editor confere as escolhas de take e b-roll no NLE antes do corte.
4. DECUPAGEM: takes (melhor take = normalmente o último completo; take que o falante criticou está fora) · find_phrase
   para a fala exata · people / find_person para quem fala · broll e find_shot para cobrir cada tema com a imagem dele.
5. ESTRUTURA: abertura → blocos por tema (uma ideia por bloco, a fala inteira, sem picotar) → fechamento → cartela.
   Com duas câmeras, shots multicam ({"path", "offset", ...}) trocando de ângulo a cada mudança de ideia ou para
   esconder um corte de fala; com uma câmera, o corte de fala vai coberto por b-roll.
6. build_edit com "cut_on": "none" (música baixa não dita o corte) → edit_storyboard → OLHAR → export_edit.
7. LOWER THIRDS: render_overlay (motion_reference antes; cores e fontes do cliente, se houver) com nome e cargo na
   primeira vez que cada pessoa aparece, 3–4 s, um .mov com alpha na trilha NOVA do topo, no tempo da timeline exportada.
   Não use "texts" do edit para isso: eles só aparecem num render, não no XML.
8. Depois da primeira revisão do editor: clean_speech com pace "medio" (ou "solto" se a pessoa fala devagar de
   propósito); zoom só se ele pedir, e nunca no lugar da segunda câmera que existe.

## Legendas

As regras comuns valem. Para YouTube/site o padrão é um .srt separado (captions_srt) que o editor sobe na plataforma,
não queimado na imagem. Se for queimar, estilo discreto, embaixo, frase inteira por bloco.

## Regras de gosto

- A fala respira: pausas naturais ficam, o horizontal é visto com atenção e não precisa do ritmo do vertical.
- Troca de ângulo tem motivo (ideia nova, ênfase, esconder um corte), não acontece por tempo.
- B-roll mostra o que a fala diz, no momento em que diz, e dura o suficiente para ser lido (≥ 3 s).
- Lower third uma vez por pessoa, longe de corte de câmera.
- Regras do cliente (perfil: estrutura, notas) valem acima destas.
