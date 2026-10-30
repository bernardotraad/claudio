---
nome: curta-narrativo
titulo: Cena / curta narrativo
quando: ficção ou cena roteirizada gravada em takes, com claquete falada ("cena 3, take 2… ação… corta")
padroes: {"aspect": "16:9", "audio": "dialogue", "cut_on": "none", "musica": "depois do corte"}
verificar: {}
entregas: ["selects", "timeline"]
ferramentas: ["index_folder", "scenes", "takes", "timeline_strip", "contact_sheet", "make_selects", "build_edit", "edit_storyboard", "export_edit", "qc_timeline"]
---

## Brief

1. Roteiro: o editor tem o roteiro ou a decupagem técnica? Peça (texto ou arquivo); sem ele, a ordem vem das claquetes.
2. Entrega: só selects por cena · primeiro corte da cena · o curta inteiro.
3. Formato e se há música (normalmente entra depois do corte, não dita o ritmo).

## Etapas

1. scenes → agrupa os clipes por cena/parte/plano pela claquete falada, pega a atuação entre "ação" e "corta", marca
   takes abortados ("peraí", "de novo") e compara as falas de cada take com a versão de consenso. mode diz como julgar:
   pelas falas (diálogo), pela atuação (improviso) ou pela imagem (sem diálogo).
2. Clipes sem claquete ficam à parte (b-roll, bastidores, conversa).
3. make_selects → o editor confere as escolhas de take no NLE antes de cortar.
4. Primeiro corte: na ordem do roteiro, o melhor take de cada plano (prefer='last' favorece os últimos), cortes de fala
   justos conferidos com timeline_strip → build_edit com "cut_on": "none" → edit_storyboard → OLHAR → export_edit.

## Regras de gosto

- A atuação manda: um take com a fala um pouco diferente do roteiro e atuação melhor ganha (mostre a diferença).
- Continuidade: confira posição de objetos, mãos e olhar entre planos da mesma cena.
- Respeite o eixo; se o material quebrar o eixo, avise em vez de esconder.
- Sem música por cima no primeiro corte, a menos que o editor peça.
