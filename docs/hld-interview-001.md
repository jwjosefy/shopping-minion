Perguntas e respostas

1. O que vai na busca. "feijão normal / preto não" não serve como termo de busca. Proposta: o OCR (Haiku via claude -p) devolve, para cada item, o texto original, um termo de busca e as restrições ("não preto"). Você edita isso na tela de revisão.
> a lista pode ter tanto um cenário onde o / representa uma observação sobre o item, como pode simplesmente ser um separador para itens diferentes. ex "atum / leite" com certeza diz sobre 2 itens. para resolver isso, entendo que um prompt elaborado com regras simples e usado como input complementar no haiku seja suficiente para guiar o output do modelo para JSON. Elabore um prompt adequado e versione - me informe onde está para eu revisar, mas entendo que seria parte do source novo.

2. Como o Jev decide. Proposta: uma pergunta Choice por item, com os ~15 resultados mais uma opção "nenhum serve". O estado leva só o item, as restrições e os campos de cada produto. Vão 5 itens por chamada. Sobre a confiança:
   - acima de 0,8 aceito o produto, entre 0,5 e 0,8 pergunto, abaixo de 0,5 pergunto mostrando que nada serviu;
   - esses números são chute por enquanto, e eu calibro rodando os 7 casos de eval do alfa0.
> ok, aceito.

3. Assets do alfa0. Trago de volta list-001 (eval do OCR) e os 7 casos do resolver (eval do Jev)? Proposta: sim, só esses dois.
> ok

4. Quantidade e preferências. Mantenho a regra A→B→C: o que a lista diz, depois as preferências, depois 1 unidade com alerta. Proposta: sem YAML escrito à mão. O SQLite começa vazio e cada escolha ou correção sua vira histórico. O "B" passa a ser "o que você escolheu da última vez para esse termo", e isso vale para produto e quantidade.
> aqui eu sei que vou me contradizer, mas vamos manter o YAML de preferencias. porém use nomes em portugues-brasil

5. Login. Proposta: você loga uma vez na janela do Playwright e eu salvo a sessão em .auth/, sem guardar email e senha. Se a sessão expirar, o app pausa e pede para você logar de novo.
> ok por enquanto - guardamos uma decisão de futuramente usar o login e senha para automatizar esse step também.

6. Primeiro marco. Proposta: 3 itens indo para o carrinho real pela CLI, com as três passadas e sem UI. Depois vem a webapp com Vue via CDN (sem build com node) e as barras de progresso.
> ok, unico ponto de atenção é manter o headless=false por enquanto