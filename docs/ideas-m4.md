# Ideas for M4

_Written by Johann in Portuguese; translated to English by Claude on 2026-10-02. The original is [ideas-m4.pt-BR.md](ideas-m4.pt-BR.md). The design that came out of it is [lld-m4.md](lld-m4.md)._

Goal: Purchase-History-RAG. My idea for a flow that uses the purchase history to improve the experience in the minion.

## Intro

Past purchases are on the page https://andorinhaonline.com.br/minha-conta/pedidos.
Each order has an address like https://andorinhaonline.com.br/minha-conta/pedidos/<id> (an example; I'll use this order below for the exercise). In the order details there is a "Ver mais produtos" (see more products) button that expands the full list when clicked.

My idea is to use past purchases, crossed with the search results and the original shopping list, to enhance the decision Jev suggests.

## Proposed mechanism

Given that we have:
1. [item] = a single item identified by the OCR
2. [busca] = the results of a simple search for [item] at Andorinha (limited to 10~15 entries; parameter N tbd)
3. [hist] = the full history of the latest purchases (N tbd, or a parameter)
4. [pref] = preferences about product features, entered directly in the UI (e.g. zero soda, lactose-free milk, etc.)

My idea is, for each tuple ([item], [busca]), to build a set [hist(item)]: the subset of the whole history filtered down to what is relevant to the tuple, such that:

[hist(item)] = AI(filter [hist], keeping only entries related to [item] or [busca])

Where:
- AI --> can be either a structured call to a light LLM (Haiku, Luna, GLM 5.3 Flash etc.) *OR* a call to a System1-like API such as Jev/Julia-1/etc.
	- Each model needs a different approach, given the nature of each one.

With the 3 elements, [item], [busca] and [hist(item)], redesign Jev's decision making to ask: "for this [item], considering the [pref] we have, considering the previous history in [hist(item)], which Choice in [busca] is the most likely?"
The quantity bought before for that item in the history should be used as a suggestion when building the cart.
> Keep in mind that I'm drafting the prompt and the structure for Jev here; this needs refining.

### A practical example

The most recent list I have (one I actually need to buy) has the item "Laranja" (orange).
In my purchase history, the most recent purchase had only
- laranja pêra rio kg | 2,045kg | R$ 6,11

A search for the term "laranja" returns something like:
[Laranja Pêra Rio Kg] R$ 5,98 kg laranja pêra rio kg
[Laranja Bahia Kg] R$ 8,99 KG laranja bahia
[Hort Laranja Lima Kg] R$ 8,99 kg
[Laranja Pêra De Marchi Saco 3kg] R$ 10,99 un
[Pão De Laranja Kg] R$ 39,90 kg
[Bolo Seco Laranja Kg] R$ 39,90 kg
[Laranja Pré-Cozido Vácuo 1kg] R$ 39,99 un
[Refrigerante Coca Cola 2l + Fanta Laranja 2l] R$ 19,99 un
[Suco Xando Laranja 900ML] R$ 13,99 un **Oferta**

In this particular case, the question to Jev would be something like

"for this item <laranja>, considering the prefs <>, considering the previous history (<laranja pêra rio kg | 2,045kg>), which Choice in [busca] is the most likely?"

and the expected result would be
[Laranja Pêra Rio Kg] R$ 5,98 kg laranja pêra rio kg
which, by the way, is cheaper than last time.

## Purchase History

There should be a mechanism that can run automatically to go through the purchase history and build the database to query, so we avoid browsing the history many times. For testing, I'd start the database with the last 5 purchases, so as not to pollute the context too much.

## UX

It should be possible to deliver M4 with this purchase-history RAG without depending on or blocking M5. The intersection I see here is using Jev's answer to sort the [busca] suggestions by highest probability.

## Edge cases

The 2 biggest edge cases I see, before exploring further, are
1. items that were never bought;
2. items that don't exist in the search.

In both, there should be a fallback to the current decision mechanism, i.e. non-RAG.
