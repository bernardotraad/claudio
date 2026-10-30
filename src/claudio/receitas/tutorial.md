---
nome: tutorial
titulo: Tutorial / aula / gravação de tela
quando: alguém ensinando algo — gravação de tela, mãos na bancada ou pessoa explicando para a câmera — com retakes e pausas para pensar
padroes: {"aspect": "16:9", "duracao": "o necessário", "pace": "medium", "legendas": "srt separado", "musica": "baixa ou nenhuma", "cut_on": "none"}
verificar: {"plataforma": "youtube", "gancho_s": 2.0}
entregas: ["timeline", "legendas_srt"]
ferramentas: ["index_folder", "takes", "find_phrase", "timeline_strip", "contact_sheet", "snap_cut", "build_edit", "edit_storyboard", "export_edit", "clean_speech", "render_overlay", "caption_sentences", "captions_srt", "qc_timeline"]
---

## Brief

1. O que a pessoa aprende ao fim do vídeo (uma frase) e para quem é (iniciante, avançado).
2. Roteiro ou lista de passos: se existir, peça; sem ele, a ordem vem da gravação, mas confira se nenhum passo ficou
   de fora.
3. Material: gravação de tela, câmera no rosto, câmera nas mãos — qual mostra o quê, e se a câmera do rosto entra num
   canto (picture-in-picture) ou alternando.
4. Textos na tela: títulos de cada passo, destaques ("clique aqui"), atalhos de teclado.

## Etapas

1. index_folder em todo o material.
2. takes: o autor costuma repetir a explicação; o melhor take é normalmente o último completo, e take que ele mesmo
   critica ("não, peraí", "de novo") está fora. find_phrase para achar cada passo do roteiro.
3. Monte na ordem dos passos: um bloco por passo, com a fala inteira e o que se vê na tela no mesmo momento. Corte de
   fala com snap_cut (mode speech, tighten se for corte seco de explicação), conferido em timeline_strip.
4. Detalhe que ninguém enxerga em tela cheia: o shot da gravação de tela com "zoom" e "reframe_x"/"reframe_y" na região
   que importa (o menu, o campo, o código), conferido no edit_storyboard.
5. build_edit com "cut_on": "none" → edit_storyboard → OLHAR → export_edit.
6. Títulos dos passos e destaques: render_overlay (motion_reference antes), numa trilha nova no tempo de cada passo.
7. Depois da revisão do editor: clean_speech com pace médio. Ele decide pelo áudio e não vê a tela: rode antes com
   preview=True e confira com contact_sheet os silêncios que ele cortaria — onde a tela ainda está mudando (carregando,
   resultado aparecendo), use max_pause maior ou pace solto.

## Legendas

As regras comuns valem. Termos técnicos, nomes de menus e atalhos escritos exatamente como aparecem na tela
(vocabulário do índice).

## Regras de gosto

- Nunca cortar enquanto a tela mostra o resultado de uma ação: a pessoa precisa ver o que aconteceu.
- Erro e correção ao vivo só ficam se ensinam alguma coisa; senão, sai o erro e fica a forma certa.
- Cada passo começa dizendo o que vai fazer e termina mostrando o resultado.
- Texto na tela fica tempo suficiente para ler duas vezes.
