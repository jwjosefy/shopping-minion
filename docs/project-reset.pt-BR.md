<Oct. 1st>

# Intro

A construção do projeto parou ontem a tarde, por conta do limite no plano do Claude. Eu poderia ter seguido com outro model, mas resolvi parar para focar em outras coisas e reavaliar. 

Olhando em retrocesso aqui - o projeto ficou desnecessariamente complicado. N camadas, abstrações por cima de abstrações, pra no final AINDA não chegar num modelo funcionando. 

Quero que vc gere dentro de docs/ um [[lessons-learned]] com o que de fato foi aproveitado, tanto do design, como da minha interação contigo. 

### self-reflection
O modelo mental que eu estava seguindo para operar o core da compra era de um loop master pelos itens da lista, executando os passos para cada item, algo como:
``` ORIGINAL MODEL
loop (each item) {
	search()     -- use the original item name to search
	decide()     -- choose which option to pick
	add_cart()   -- execute the action to put in the cart
}
review()
```

olhando de volta para o prototipo que fiz com meu filho, a abordagem feita lá era diferente por fazer o loop repetir para cada operação, ao invés de tentar ter um único loop. Na prática, isso torna toda a operação muito mais rápida de se executar no end-to-end, atingindo o objetivo final de otimizar o tempo do usuário - vide [[#Reset definittion]].

``` NEW MODEL
loop (each item) { search() } -- 100% deterministic python code, simple scraper
loop (each item) { decide() } -- optimized calls to Jev/models, allow better context
loop (each item) { add_cart()} -- 100% deterministic python

review()
```


# Reset definittion

objetivo: construir automação + webapp para efetuar compras no Andorinhaonline.com.br, lendo (OCR) de uma lista de compras manual e finalizando com o carrinho de compras montado e pronto para revisão e checkout humanos. Objetivo final é reduzir esforço manual de montar carrinho de compras para listas mais longas (50+ itens), otimizando o tempo do usuário.

## out-of-scope
- fazer o checkout automático, de qualquer forma
- automatizar outros mercados na v0
- cloud-hosting na v0
- mobile app
- docker
## Flow esperado a ser automatizado 

1. usuário envia upload da lista de compras
2. app faz OCR da lista com LLM [[#note-1]]
3. app pede ao usuário para revisar/editar a lista
4. app inicia busca no site (loop search)
	1. para cada item pesquisado: capturar os resultados (top 15~) da página para posterior matching (nome, marca, un medida, preço, desconto, possibilidade de unidades [un/kg])
	2. barra de progresso no webapp enquanto o python está pilotando o site
5. app faz o matching (decide())
	1. para cada item: elabore consulta padronizada ao Jev para escolher qual o item mais provável considerando (item da lista original)<->(opções encontradas)
	2. loop -> eval para todos os itens, 1 call por item ou batches de 5 em 5
	3. para opções com confiança alta: assumir aquele item
	4. confiança média-baixa: apresentar tela de escolha para desambiguar item
		1. em loop, item por item, um de cada vez para o usuário escolher
	5. apresentar opção para o usuário fazer um último review e ajuste
6. app monta o carrinho (add_cart)
	1. cada item: navega no site para o item, executa ação para adicionar no carrinho
	2. atenção com a interação > deve clicar multiplas vezes no botão mais (+) - controlar timeout e race condiiton
	3. barra de progresso no webapp enquanto o python está pilotando o site
7. app informa ao usuário status concluído e oferece abrir com o site no carrinho aberto.

### note-1
Note que "faz OCR da lista" pode ser feito de diversas formas. Para economizar, elabore uma forma de invocar o claude com -p no parametro, modelo haiku, para passar o arquivo de input como referência e direcionar o output estruturado em JSON.

## Output pós redesign - arquitetura esperada

mover TODA a implementação atual para uma subpasta alfa0/ , começar nova implementação na pasta do projeto.

Webapp Python simples (FastAPI com vue.js e tema dark moderno e interessante)
Playwright pilotando o chrome
- modo headless=false > exibir o site navegando
- usar um user agent adequado igual um navegador regular
Sqlite local em data/ para manter históricos, inputs, correções
- essa informação posteriormente irá evoluir para um mecanismo de preferências, para direcionar melhor o matching
novo HLD
novo LLD > somente após review e stamp no HLD
- plano de implementação (PLAN = Opus; RUN=Sonnet)

continuar usando dotenvx para secrets

# Dúvidas antes de começar

1. "Jev": o que é? É um modelo ou serviço específico, ou um typo (Julia-1? LLM em geral?).
	> modelo da typesafe.ai estilo System1 - o que inspirou o Julia-1 na verdade. vamos começar com o Jev, vou fornecer a api-key no .env, depois iremos testar com Julia-1

2. Abrir o site com o carrinho no final: o carrinho fica na sessão do Chrome do Playwright. Ou deixamos essa janela aberta no fim, ou é preciso fazer login para o carrinho aparecer no seu navegador. Isso vai para o HLD como decisão. Você tem preferência?
	> manter o playwright aberto já resolve por hora. por enquanto vou logar manualmente no andorinha pra garantir que o carrinho permaneça na conta, mesmo que eu troque de navegador.

3. ADRs, journal e evals antigos: vão todos para alfa0/, ou docs/adr e docs/journal continuam na raiz como histórico, com ADRs novos superando os antigos? Imagino que as fixtures do eval (list-001, resolver-cases) sejam reaproveitadas.
	> TUDO para alfa0, menos os docs/journal - inclusive altere o CLAUDE.md para que ignore a pasta e não siga os ADRs de lá.
	> docs/journal documentam a jornada e servem de base para o blog na sequencia - devem ser tratados como append only

4. Branch de fix não mergeado (worktree agent-abdb617314a348016, ajustes do agente de descoberta e S1 run 2): descarto, ou faço o merge antes do move para ficar registrado no alfa0?
	> merge antes do move para manter o registro. 