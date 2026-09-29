"""Gameloot.in (WooCommerce) adapter."""

import re
from decimal import Decimal

from selectolax.parser import HTMLParser

from stockwatch.models import ScrapedItem
from stockwatch.sites.base import CategoryDef, Progress, ScrapeError, SiteAdapter, SiteHttp, register

MAX_PAGES = 200
EMPTY_NOTICE = "No products were found matching your selection"
_PRICE_RE = re.compile(r"(?:Rs\.?|INR|₹)?\s*([0-9]+(?:\.0+)?)")


def listing_page_url(base_url: str, page: int) -> str:
    return f"{base_url.rstrip('/')}/page/{page}/?stock=instock"


def parse_price(text: str) -> int:
    """Parse whole-rupee prices ("Rs.44,999", "₹ 44,999", "INR 44,999.00"); reject anything else."""
    normalized = text.replace("\xa0", " ").replace(",", "").strip()
    match = _PRICE_RE.fullmatch(normalized)
    if not match:
        raise ValueError(f"Unrecognized whole-rupee price: {text!r}")
    return int(Decimal(match.group(1)))


def clean_name(name: str) -> str:
    """Drop the trailing "(...)" condition/warranty suffix Gameloot appends to names."""
    pos = name.rfind("(")
    return (name[:pos] if pos != -1 else name).strip()


def parse_listing(html: str, page_url: str) -> list[ScrapedItem]:
    """Parse one listing page. Returns [] only for Gameloot's explicit "no products" notice."""
    tree = HTMLParser(html)
    containers = tree.css("div.kad_product")
    if not containers:
        if any(EMPTY_NOTICE in n.text(separator=" ", strip=True) for n in tree.css(".woocommerce-info")):
            return []
        raise ScrapeError(f"Unrecognized product listing at {page_url}")

    items = []
    for container in containers:
        name_tag = container.css_first("h5")
        sale = container.css_first("ins")
        price_tag = (sale or container).css_first("span.woocommerce-Price-amount")
        link_tag = container.css_first("a.product_item_link")
        name = name_tag.text(strip=True) if name_tag else ""
        href = link_tag.attributes.get("href") if link_tag else None
        if not name or price_tag is None or not href:
            raise ScrapeError(f"Incomplete product listing at {page_url}")
        try:
            price = parse_price(price_tag.text())
        except ValueError as exc:
            raise ScrapeError(f"Invalid price for {name!r} at {page_url}: {exc}") from exc
        items.append(ScrapedItem(url=href, name=clean_name(name), price=price))
    return items


@register
class Gameloot(SiteAdapter):
    key = "gameloot"
    name = "Gameloot"
    base_url = "https://gameloot.in"
    categories = (
        CategoryDef("gpu", "Graphics cards", "https://gameloot.in/product-category/graphics-card/", 15),
        CategoryDef("cpu", "CPUs", "https://gameloot.in/product-category/buy-cpu/", 18),
        CategoryDef("mobo", "Motherboards", "https://gameloot.in/product-category/motherboard/", 22),
        CategoryDef("ram", "Desktop RAM", "https://gameloot.in/product-category/desktop-ram/", 30),
    )

    async def scrape_category(self, http: SiteHttp, url: str, progress: Progress) -> list[ScrapedItem]:
        found: dict[str, ScrapedItem] = {}
        for page in range(1, MAX_PAGES + 1):
            page_url = listing_page_url(url, page)
            response = await http.get(page_url, headers={"Referer": f"{self.base_url}/"})
            if response.status_code == 404:
                # Past the last page, but a missing first page means the category itself is gone.
                if page == 1:
                    raise ScrapeError(f"Category not found: {url}")
                break
            if response.status_code != 200:
                raise ScrapeError(f"HTTP {response.status_code} for {page_url}")

            items = parse_listing(response.text, page_url)
            if not items:
                break
            fresh = [item for item in items if item.url not in found]
            if not fresh:
                # The site served an already-seen page (e.g. redirected past the end); don't loop forever.
                raise ScrapeError(f"Page {page} repeated earlier products; pagination looks broken")
            for item in fresh:
                found[item.url] = item
            progress.page_done(len(items))
        else:
            raise ScrapeError(f"More than {MAX_PAGES} pages at {url}")
        return list(found.values())
