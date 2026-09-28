"""Pure change detection between the stored state of a category and a fresh, complete listing."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from stockwatch.models import KnownProduct, ScrapedItem


@dataclass(slots=True)
class ChangeSet:
    new: list[ScrapedItem] = field(default_factory=list)
    restocked: list[tuple[KnownProduct, ScrapedItem]] = field(default_factory=list)
    sold_out: list[KnownProduct] = field(default_factory=list)
    price_changed: list[tuple[KnownProduct, ScrapedItem]] = field(default_factory=list)
    renamed: list[ScrapedItem] = field(default_factory=list)  # in stock, same price, listing name changed
    unchanged: list[ScrapedItem] = field(default_factory=list)  # in stock before and now, nothing changed


def compute_changes(known: Mapping[str, KnownProduct], scraped: Sequence[ScrapedItem]) -> ChangeSet:
    changes = ChangeSet()
    seen = set()
    for item in scraped:
        seen.add(item.url)
        prev = known.get(item.url)
        if prev is None:
            changes.new.append(item)
        elif not prev.in_stock:
            changes.restocked.append((prev, item))
        elif prev.price != item.price:
            changes.price_changed.append((prev, item))
        elif prev.name != item.name:
            changes.renamed.append(item)
        else:
            changes.unchanged.append(item)
    changes.sold_out = [p for url, p in known.items() if p.in_stock and url not in seen]
    return changes
