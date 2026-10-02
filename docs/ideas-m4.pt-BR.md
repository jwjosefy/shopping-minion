Goal: Purchase-History-RAG --> descrever minha ideia para o flow de uso do historico de compras para melhorar a experiencia no minion.

## Intro

As compras anteriores ficam na página https://andorinhaonline.com.br/minha-conta/pedidos . 
cada pedido fica num endereço parecido com https://andorinhaonline.com.br/minha-conta/pedidos/<id> (exemplo - para fins de exercício, vou utilizar este pedido abaixo). Dentro do detalhe do pedido, existe o botão "Ver mais produtos" que ao clicar expande a lista completa. 

Minha ideia é utilizar as compras anteriores, cruzando com resultados de busca e a lista de compras original para dar um enhance na decisão sugerida do Jev.

## Mecanismo proposto

Considerando que temos:
1. [item] = item individual identificado no OCR
2. [busca] = lista resultado da busca simples pelo [item] no Andorinha (limitada a 10~15 entradas; parametro N tbd)
3. [hist] = lista com o histórico completo das últimas compras (N - TBD ou parametro)
4. [pref] = preferencias de características do produto informadas diretamente na UI (ex: refrigerante zero; leite zero-lactose; etc)

Minha ideia é para cada tupla ([item], [busca]), criar um set [hist(item)] representando o subset filtrado de todo o histórico com relação aos itens relevantes na tupla, tal que:

[hist(item)] = AI(filtrar [hist] trazendo apenas entradas relacionadas com [item] ou [busca])
Onde:
- AI --> Pode ser ou uma chamada estruturada a LLM leve (Haiku, Luna, GLM 5.3 Flash etc) *OU* uma chamada a API System1 like JEV/Julia-1/etc.
	- Em cada modelo precisa elaborar uma abordagem diferente, dado a natureza de cada um

Com os 3 elementos -- [item], [busca] e [hist(item)] -- redesenhar o decision making do Jev para fazer a pergunta "para este [item], considerando as [pref] que temo, considerando o histórico anterio em [hist(item)], qual Choice para [busca] é a mais provável?".
A quantidade comprada anteriormente do determinado item no histórico deveria ser usada como sugestão para a montagem do carrinho.
> Considere que aqui estou rascunhando o prompt e estrutura para o Jev, isso precisa ser refinado. 

### Exemplo prático

Na lista que tenho mais recente (que eu de fato preciso fazer a compra) tem o item "Laranja".
No meu histórico de compra, a compra mais recente teve apenas
-  laranja pêra rio kg | 2,045kg | R$ 6,11

A busca pelo termo "laranja" devolve algo como:
[Laranja Pêra Rio Kg] R$ 5,98 kg laranja pêra rio kg
[Laranja Bahia Kg] R$ 8,99 KG laranja bahia
[Hort Laranja Lima Kg] R$ 8,99 kg
[Laranja Pêra De Marchi Saco 3kg] R$ 10,99 un
[Pão De Laranja Kg] R$ 39,90 kg
[Bolo Seco Laranja Kg] R$ 39,90 kg
[Laranja Pré-Cozido Vácuo 1kg] R$ 39,99 un
[Refrigerante Coca Cola 2l + Fanta Laranja 2l] R$ 19,99 un
[Suco Xando Laranja 900ML] R$ 13,99 un **Oferta**

neste caso particular, a pergunta ao jev seria algo como

"para este item <laranja>, considerando as prefs <>, considerando o historico anterior (<laranja pêra rio kg | 2,045kg>), qual Choice é mais provável para [busca]?"

e o resultado esperado seria a  
[Laranja Pêra Rio Kg] R$ 5,98 kg laranja pêra rio kg
que btw tá mais barato que da ultima vez.

## Purchase History

Deve ser criado um mecanismo que possa ser acionado de forma automática para varrer o histórico de compras e construir a base de dados para ser consultada, evitando navegar multiplas vezes no histórico. Para fins de teste, eu iniciaria a base com as últimas 5 compras, para não poluir demais o contexto. 

## UX

Deveria ser possível entregar o M4 com este RAG de histórico de compras sem depender ou bloquear o M5. A interseção que vejo aqui é usar o retorno do Jev para sort pelas sugestões da [busca] com maior probabilidade. 

## Edge cases

Os 2 maiores edge case que visualizo antes de explorar mais a fundo são
1. Itens que nunca foram comprados
2. itens que não existem na busca.

Em ambos, deve haver um fallback para o mecanismo atual de decisão, i.e., non-RAG.