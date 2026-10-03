### ocr da lista
permitir que seja feito o upload de mais de uma foto antes de dispara o OCR

### revisão da lista pós OCR
em cada card tem
- linha
- nome
- busca
- restrições
- marca

fica confuso. o ideal seria mostrar apenas a "linha" (o que foi lido) e "busca" (o que será de fato pesquisado no mercado)

linhas com multiplos items que se repetem >> entendi a mecanica, mas é confusa. poderia ser mais simples se fosse um card com a "linha" e duas entradas de "busca" agrupadas visualmente para o usuario confirmar.

a quantidade vem em branco - entendo que esse em tese vem do OCR, mas fica confuso tbm

### picking para confirmar depois do Jev

nessa tela, seria interessante manter fixo no topo o que foi pesquisado enquanto scrollo para poder escolher outro... muitas vezes, o que leva a escolher outro produto é um gosto pessoal, ou alguma promoção que parece melhor, ou que sei que a qualidade do produto é melhor - não espero codificar tudo isso, mas sim fazer o sistema aprender com o usuário, quanto mais usar, melhor recomenda.

carne >> esse é um ponto de discordia... carne fresca não costuma ter uma marca definida, a busca funciona melhor pelo tipo do corte. hortifruti suponho que funcione parecido. 
nessa rodada 2 de 4, "carne de panela" devolveu um item sopão da magie, completamente fora do objetivo. eu inclusive cheguei a anotar "acém ou paleta" manualmente, foi lido pelo OCR, e ignorado posteriormente.

após confirmar as escolhas, os cards aparecem para confirmar a qtd. esse flow de primeiro clica tudo, depois inclui a qtd gera um burden mental maior, pq numa lista longa vc passa no loop 2x. em 40 itens, são 80 decisões +/- --> virtualmente pior do que fazer vc mesmo manualmente no site. 

também, os labels amarelos se confundem, pois usa a mesma cor (amarelo) em labels diferentes (quantidade de última compra X quantidade assumida), então não dá pra fazer um "glance through" e checar se algo ficou pra trás. lembra que a ideia é automatizar, então não quero ter que conferir uma terceira vez no site do andorinha. também não tá claro se "quantidade da última compra" é literalmente apenas a última compra ou a última vez que AQUELE ITEM foi comprado, independente de quando foi.

### loop de incluir no carrinho e conferência pós loop

ainda tem pequenos bugs aparentes onde o driver (playwright) parece andar mais rápido que o step de verificação, principalmente em produtos que exigem multi-step. muitos dos itens que mostraram como erro no final derivaram dessa situação. outros talvez por erros de arredondamento, e vejo que foi a menor parte. corrigir esses bugs deveria reduzir a taxa de erros. 

## rodada 3

pequeno bug no QR Code

### hortifruti
não ficou claro porque o bot pulou os itens abaixo
- goiaba
- uva
- mamão
- cebola
- salsinha

varios erros na automação aconteceram, impedindo de colocar tudo no carrinho.