# 2026-09-30 — Skipping purchase history was a product design miss

_Drafted by Claude from Johann's note of 2026-09-30, for his review._

For v0 I deliberately left out anything that reads the purchase history and builds preferences from it ([ADR-0003](../adr/0003-preferences-as-static-yaml.md)): a hand-written YAML was enough to get a first end-to-end run, and history extraction depends on a store adapter that didn't exist.

Working through how quantities get decided showed the cost of that shortcut. The rule is simple:

1. if the list says a quantity, use it;
2. if not, take it from the preferences;
3. if the preferences don't have it, assume 1 unit and flag the item for human review.

Step 3 will fire for almost every item, because the preferences file only has what I typed by hand. And the information that would fill it already exists: past orders say, for each kind of item, which product I buy and **how much**. Reading quantities from previous purchases is probably the fastest way to build a knowledge graph of the user's preferences, quantities included. It would turn most "assumed" flags into real defaults without asking the user anything.

So what I cut to simplify the delivery is also what would make the delivery good.

**For v1:** read the order history first and generate the preferences from it (brand, size, usual quantity per item type), before refining anything else. ADR-0004 already reserves `order_history()` on the adapter for this.
