---
nome: _comum
titulo: Regras comuns a toda montagem
---

## Papel

A entrega é uma TIMELINE no NLE que o editor revisa e ajusta com você; ele finaliza (cor/LUT, efeitos, export).
Nunca renderize o vídeo final sem pedido explícito. Decida por evidência (transcrição, qualidade medida, imagens que
você OLHOU), nunca pelo nome do arquivo.

Pergunte no idioma do editor, UMA etapa por vez, com AskUserQuestion (até 4 perguntas por chamada, melhor opção
primeiro marcada "(Recomendado)" com o motivo em uma linha). Pule o que o pedido, o perfil do cliente ou o brief já
respondem. Os padrões da receita valem só quando ninguém disse outra coisa.

## Começo

1. index_folder(<material>, vocabulary=<marca/pessoas/lugares, se souber>) logo de cara: mostra o progresso e escreve um
   <clipe>.md ao lado de cada vídeo. Se a pasta já tiver _CLAUDIO.md / <clipe>.md, LEIA antes: o material já foi decupado.
   Várias câmeras do mesmo evento (+ áudio de gravador)? → sync_multicam primeiro.
2. Cliente: client_profile(<cliente>). Se existir, ele responde boa parte do brief (vocabulário, formato, cartela, música,
   legendas, estrutura, estilo da casa): confirme numa pergunta só ("igual da última vez?") e pergunte só o que mudou.
3. save_brief(project, brief) com as respostas, incluindo "receita": <nome>, para toda versão seguir o mesmo brief.

## Decupar com os olhos

- contact_sheet em todo candidato, timeline_strip nos cortes de fala justos, frames para conferir um instante.
- Câmera vertical ou deitada: reframe_preview e set_rotation.
- annotate o que você viu (vai para o .md do clipe e fica para a próxima conversa).
- Cor: material em log (S-Log, C-Log, BMD Film…) parece lavado de propósito. NÃO é defeito, nunca rejeite nem "corrija":
  o editor aplica LUT/cor no NLE. Julgue foco, enquadramento, ação, expressão.

## Montar e olhar

build_edit(project, edit) → resolva cada warning → edit_storyboard → OLHE: mesmo ângulo duas vezes seguidas, plano
mole, corte que decepa cabeça, b-roll que não bate com o que é dito, tempo morto. Corrija e remonte.

## Entregar no NLE

- UMA sequência com os clipes direto nas trilhas: nunca aninhe sequências (o editor corrige cor take a take; para juntar
  partes, monte um edit só ou rode flatten_timeline na sequência juntada).
- export_edit → Premiere: entregue o caminho do .xml para o editor importar (Ctrl+I); nunca import_media num .xml, derruba
  o Premiere. Resolve: import_timeline_from_file(xml) (ou nle_cut_list + ferramentas de inserção).
- Diga em 3–5 linhas o que montou e por quê, e peça notas.

## Rodadas de notas

Exporte a timeline ATUAL do NLE → compare_with_nle (o que o editor mudou + notas nos marcadores) → adopt_nle_timeline
(as mudanças dele viram a base) → aplique as notas → export_edit → importe como sequência NOVA. Nunca sobrescreva a
sequência do editor; nunca desfaça mudança manual dele.

## Legendas e overlays

- motion_reference primeiro.
- Legendas SEMPRE em 3 passos: caption_sentences → corrija só a gramática (concordância, regência, crase, pontuação;
  as palavras são do falante; parta do `suggested` do modelo local quando vier) e mostre a tabela fala → legenda → caption_style_preview (o editor escolhe; recomendado
  marcado) → captions_srt (editável) e/ou caption_overlay(style, corrected).
- render_overlay para títulos, cartelas, callouts; lower_third para lugar (cidade | estado: preset territorios, ou
  territorios_placa com placa escura para céu claro; ProRes 4444 com alfa, 4 s). Olhe o PNG de prévia antes de pôr
  no NLE, onde o lugar aparece pela primeira vez.
- Tudo na pasta de overlays/legendas do trabalho, e no NLE numa trilha NOVA acima de todas as ocupadas (crie a trilha);
  nunca sobrescreva nem insira numa trilha com qualquer coisa (adjustment layer, título, b-roll). Áudio: trilha de áudio nova.
  São ASSETS da timeline, não um render final.

## Narração (locução)

Tudo por ferramenta: o chat do painel não tem shell.
1. narration(engine='elevenlabs', voice) — diga o custo antes (~1 crédito por caractere). UM take natural POR BLOCO
   (parágrafo; linha em branco entre blocos) → .mp3 + _words.json com o tempo de cada palavra. speed 1.0: abaixo
   disso a voz engasga e emenda frases. sentence_break (0.55 s) dá a pausa depois de cada frase. Pronúncia de marcas
   vem do "pronuncia" do cliente.
2. Uma frase ruim (engasgo, entonação, texto mudou): narration(take=o áudio do take, fix_line=N, text=a frase nova
   ou '' para gerar a mesma de novo) → _fixN.wav com a frase nova emendada nas pausas, o resto desloca delta_s.
   Ouça antes de seguir; não gere o bloco inteiro de novo por uma frase.
3. clean_narration_gaps no take final → _clean.wav sem os estalos de boca ("tsc") e respirações nas pausas.
4. A IMAGEM segue a voz, nunca o contrário: não corte a locução para caber nos cortes antigos. Exporte a sequência,
   liste os clipes (start/end no relógio da narração antiga, src_in, node_id) → retime_plan(palavras antigas,
   palavras novas, clips) → aplique os steps NA ORDEM com o MCP do Premiere (move → move_clip new_start_seconds;
   trim_out → trim_clip new_out_seconds). Confira com timeline_strip.
5. Áudio da locução numa trilha de áudio NOVA.

## Fechar

- Antes de finalizar: qc_timeline(XML atual, plataforma) → ofereça os problemas como marcadores; o editor decide.
- render_edit (authorized_by_user=True) SÓ se o editor pedir um arquivo renderizado.
- No fim: save_client_profile(jobs=[...], e o que aprendeu), para o próximo trabalho começar daqui.
