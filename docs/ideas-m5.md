# Ideas for M5

_Written by Johann in Portuguese while running his real list on 2026-10-02 (runs 9–11); translated to English by Claude on 2026-10-03. The original is [ideas-m5.pt-BR.md](ideas-m5.pt-BR.md). The design that came out of it is [hld-m5.md](hld-m5.md) and [lld-m5.md](lld-m5.md)._

### List OCR
Allow uploading more than one photo before starting the OCR.

### List review after the OCR
Each card has:
- line
- name
- search
- constraints
- brand

That's confusing. Ideally it would show only the "line" (what was read) and the "search" (what will actually be searched at the store).

Lines with several items that repeat >> I understood the mechanics, but it's confusing. It could be simpler as one card with the "line" and two "search" entries, grouped visually, for the user to confirm.

The quantity comes blank. I understand that in theory it comes from the OCR, but it's confusing too.

### Picking, to confirm after Jev

On this screen it would help to keep what was searched fixed at the top while I scroll to choose another product... Often what makes me choose another product is personal taste, or an offer that looks better, or knowing that a product's quality is better. I don't expect to code all of that, but to make the system learn from the user: the more you use it, the better it recommends.

Meat >> this is a sore point... Fresh meat usually doesn't have a set brand; searching by the type of cut works better. I suppose produce works in a similar way.
In this run (2 of 4), "carne de panela" returned a Maggi soup item, completely off target. I had even written "acém ou paleta" by hand; the OCR read it, and it was then ignored.

After confirming the picks, the cards show up to confirm the quantity. This flow of first clicking everything and then adding the quantities creates a bigger mental burden, because on a long list you go through the loop twice. For 40 items that's about 80 decisions --> virtually worse than doing it yourself on the site.

Also, the yellow labels get mixed up, because the same color (yellow) is used for different labels (last purchase's quantity vs assumed quantity), so you can't glance through and check whether something was left behind. Remember the idea is to automate, so I don't want to check a third time on the Andorinha site. It's also not clear whether "last purchase's quantity" literally means only the last purchase, or the last time THAT ITEM was bought, whenever that was.

### The add-to-cart loop, and the check after it

There are still small apparent bugs where the driver (Playwright) seems to move faster than the verification step, mostly on products that need several steps. Many of the items that showed as errors at the end came from that. Others maybe from rounding errors, and I see that was the smaller part. Fixing these bugs should lower the error rate.

## Run 3

A small bug in the QR code.

### Produce
It wasn't clear why the bot skipped the items below:
- goiaba
- uva
- mamão
- cebola
- salsinha

Several errors in the automation happened, which kept everything from going into the cart.
