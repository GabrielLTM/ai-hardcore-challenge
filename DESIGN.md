---
version: alpha
name: "Trilha"
description: "Atendimento com agentes, fontes e andamento visíveis para suporte Getnet"
colors:
  bg: "#F5F7FA"
  surface: "#FFFFFF"
  subtle: "#F9FAFB"
  ink: "#282828"
  ink-2: "#454C52"
  ink-3: "#67717A"
  border: "#E1E6EB"
  border-strong: "#C5CBD3"
  accent: "#EC0000"
  accent-hover: "#D31513"
  accent-soft: "#FBE4E4"
  success: { text: "#166534", background: "#E7F4EC" }
  warning: { text: "#8A4B00", background: "#FDF1DE" }
  danger: { text: "#A50E0E", background: "#FDECEA" }
  demo: { text: "#6B3FA0", background: "#F2ECFA" }
  neutral: { text: "#454C52", background: "#EEF0F3" }
  progress: { text: "#454C52", background: "#E1E6EB" }
typography:
  sans:
    fontFamily: "'IBM Plex Sans', system-ui, sans-serif"
    page-title: "22px / 600"
    section-title: "16px / 600"
    body: "14px / 400"
    meta: "12-13px"
  mono:
    fontFamily: "'IBM Plex Mono', ui-monospace, monospace"
    size: "12px"
rounded:
  control: "6px"
  card: "10px"
  chip: "999px"
spacing:
  scale: ["4px", "8px", "12px", "16px", "24px", "32px"]
elevation:
  overlay: "0 12px 32px rgba(21, 24, 30, 0.18)"
layout:
  nav-width: "224px"
  list-width: "272px"
  panel-width: "360px"
components:
  button: { primary: "accent", secondary: "branco com borda border-strong", danger: "branco com texto e borda danger", height: "36px (44px em toque)" }
  chip: { shape: "pílula", content: "ícone + texto, nunca só cor" }
  field: { height: "36px (44px em toque)", error: "borda danger + mensagem com ícone" }
  dialog: { shadow: "overlay", focus: "preso e restaurado" }
  toast: { role: "status", exclusive: false }
---

# Trilha design system

Referência visual aprovada pelo usuário; as telas de origem estão em `design/` do pedido de design (Conversas, Agentes, Editor, Versões, Base, Estados e versões móveis). Este documento descreve o que está implementado em `frontend/src`. Para trocar a identidade visual basta editar `frontend/src/styles/tokens.css`: todos os componentes consomem essas variáveis CSS (`--accent`, `--surface`, `--success-fg`, `--radius-card` etc.) e nenhuma cor está fixa nos componentes, exceto o SVG da marca em `shared/icons.tsx`.

## Overview

Console web em português do Brasil para um operador de atendimento que também administra agentes. O chat é a atividade principal; o resto existe para dar confiança e controle. Princípio: **simples por padrão, técnico sob demanda**. O operador lê passos em linguagem de atendimento ("Consultou sua maquininha", "Buscou orientação na base Getnet"); versões, IDs, durações, nomes de ferramentas, tokens, custo e `trace_id` ficam em "Detalhes técnicos", recolhido mas sempre presente. Nunca imitar a marca Getnet: sem vermelho Getnet e sem logotipo dela.

Marca provisória: **Trilha** (percurso com três pontos sobre o azul de ação), 32×32 no menu lateral e 24×24 no celular, ao lado do nome escrito e do subtítulo "Atendimento com agentes".

## Colors

Vermelho Getnet `accent` (#EC0000, da folha de estilos de site.getnet.com.br) para ação primária, links, foco e item ativo; `progress` é neutro e `danger` é um vermelho mais escuro, sempre com ícone e texto, para não serem confundidos com a cor da marca; `accent-soft` para seleção. Superfícies brancas sobre fundo `bg`, bordas de 1px, texto `ink` com `ink-2` para apoio e `ink-3` como mínimo para metadados (contraste AA). Cada estado tem um par texto/fundo semântico: `success` (concluída, sucesso, publicado), `warning` (parcial, limitação, rascunho, reconectando, interrompida), `danger` (falhou, erro, ciclo), `demo` (dados de demonstração), `neutral` (cancelada, não iniciado, informativo) e `progress` (em andamento, gerando, roteando). Estado nunca depende só de cor: todo chip leva ícone e texto.

## Typography

IBM Plex Sans para interface (carregada do Google Fonts em `index.html`, com `system-ui` como reserva) e IBM Plex Mono para IDs, `trace_id`, `request_id`, nomes de ferramentas e diffs. Títulos de página 22/600, seção 16/600, corpo 14, metadados 12–13. Caixa de sentença, frases curtas, português claro.

## Layout

Desktop (≥ 1280px): navegação, lista de conversas, conversa e painel lateral "Andamento / Fontes" em quatro colunas, cada uma com rolagem própria. Entre 1100px e 1279px o painel vira drawer à direita. Abaixo de 1100px a navegação e a lista de conversas ficam num drawer à esquerda (botão de menu) e abaixo de 760px o painel vira sheet inferior. Agentes e Base usam uma coluna de página (máx. 1280px) com tabelas dentro de `overflow-x: auto`. Tudo reflui em zoom de 200%.

## Elevation & Depth

Sem gradientes e sem cartões com borda lateral colorida. Sombra só em diálogos, drawers, sheets e toasts (`--shadow-overlay`).

## Shapes

Controles 6px, cartões 10px, chips 999px. Trecho recuperado e não usado tem borda tracejada para não parecer citação.

## Components

### Buttons and actions

Primário em azul, secundário branco com borda, perigo (Parar, cancelar) branco com texto e borda `danger`, "link" para ações leves. Desabilitado usa `disabled` real e fundo `neutral`. Alvo de toque mínimo de 44px em ponteiro grosso ou largura ≤ 760px.

### Chips

Sempre ícone + texto. Tipos de fonte: Curada (documento), Fonte oficial (documento com check), Web (globo). "Dados de demonstração" aparece no cabeçalho do chat e nos passos que consultam dados simulados.

### Forms and overlays

Campo = rótulo associado + controle + dica + erro (`aria-invalid`, `aria-describedby`, ícone). Ao falhar a validação o foco vai ao primeiro campo com erro e o texto digitado é mantido. Diálogo: `role="dialog"` `aria-modal`, foco preso, fundo inerte, Esc fecha, foco volta ao gatilho; nunca `alert/confirm/prompt` nativos. Toast: empilha, não rouba foco, `role="status"`.

### Navigation and data display

Navegação marca a página ativa com `aria-current`. Lista de conversas agrupa por Hoje/Ontem/Anteriores e só mostra chip quando a conversa não está concluída. Tabelas têm `caption` e `th scope`. Diff usa `+`/`−` e rótulo em texto ("Adicionado:", "Removido:") além da cor; linhas iguais distantes das mudanças ficam recolhidas.

### Timeline (Andamento)

Lista `ol` plana: ícone de estado (check, spinner, alerta, x, círculo) + frase curta em negrito + linha de apoio. O estado também existe em texto para leitor de tela. Passos nascem dos eventos reais do backend; nada é inventado.

### Content and data visualization

Valor ausente é "não informado", nunca zero (custo, tokens, duração). Fontes mostram origem, data de coleta e limitações; "Usadas na resposta" e "Consultadas, mas não usadas" ficam em seções separadas. Datas da API chegam em UTC e são exibidas em `America/Sao_Paulo` (BRT) com a hora UTC no detalhe técnico.

### Motion

Animação só comunica trabalho em andamento (spinner, cursor de geração). `prefers-reduced-motion` desliga todas as animações e transições.

## Do's and Don'ts

- **Do:** manter o chat limpo e levar o técnico para "Detalhes técnicos".
- **Do:** distinguir trechos recuperados de fontes citadas.
- **Do:** preservar texto parcial, rascunho do campo e edições do agente em qualquer falha.
- **Don't:** ocultar erros atrás de mensagem efêmera: erro persistente aparece em contexto, com `request_id`.
- **Don't:** apresentar dados de suporte simulados como reais.
- **Don't:** prender o usuário em rolagem automática ou em movimento.
