import requests
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from pymongo.errors import ServerSelectionTimeoutError, ConnectionFailure, PyMongoError
import asyncio
import logging
from datetime import datetime
from enum import Enum
from typing import Optional

from telegram_helper import send_telegram_message
from db_utils import get_mongo_conn, remove_list_duplicates


class ScrapeResult(Enum):
    """Enum for scrape operation results."""
    SCRAPE_FAILED = "SCRAPE_FAILED"
    MONGODB_UNAVAILABLE = "MONGODB_UNAVAILABLE"

# Plain requests uses python-requests/* as User-Agent; many sites return 403. Mimic a current desktop browser.
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
}

_gameloot_http = requests.Session()
_gameloot_http.headers.update(_BROWSER_HEADERS)
_REQUEST_TIMEOUT = (10, 30)


def _listing_page_url(base_url: str, page_number: int) -> str:
    """Build paginated listing URL without double slashes when base_url has a trailing slash.

    Args:
        base_url: Base URL of the Gameloot product category.
        page_number: Page number to generate URL for.

    Returns:
        Complete URL for the specified page.
    """
    base = base_url.rstrip("/")
    return f"{base}/page/{page_number}/?stock=instock"


def convert_price_to_int(price_str: str) -> int:
    """Convert Gameloot price string to integer.

    Args:
        price_str: Price string from website (e.g., "Rs. 45,000").

    Returns:
        Price as integer.
    """
    # Replace non-breaking space character with regular space, remove "Rs." and commas, then convert to integer
    price_str = price_str.replace("\xa0", " ").replace("Rs. ", "").replace(",", "")
    return int(price_str)


def clean_product_name(name: str) -> str:
    """Clean Gameloot product name by removing content in parentheses.

    Args:
        name: Raw product name from website.

    Returns:
        Cleaned product name.
    """
    # Find the last position of the last occurrence of '('
    pos = name.rfind("(")
    # If a '(' is found, return the substring before it, else return the original name
    if pos != -1:
        return name[:pos].strip()
    return name.strip()


def scrape_product_page(url: str) -> Optional[list]:
    """Scrape a single Gameloot product page.

    Args:
        url: URL of the product page to scrape.

    Returns:
        List of product dictionaries if successful, None if page 404 (end of pagination),
        or ScrapeResult.SCRAPE_FAILED if non-200/404 error occurred.
    """
    parsed = urlparse(url)
    referer = f"{parsed.scheme}://{parsed.netloc}/"
    response = _gameloot_http.get(
        url,
        timeout=_REQUEST_TIMEOUT,
        headers={"Referer": referer},
    )

    # 404 means end of pagination - this is expected
    if response.status_code == 404:
        return None

    # Any other non-200 status is an error - abort scraping
    if response.status_code != 200:
        logging.error(f"Non-200 response received: {response.status_code} for URL: {url}")
        return "SCRAPE_FAILED"

    webpage_content = response.content
    soup = BeautifulSoup(webpage_content, "html.parser")
    product_containers = soup.find_all("div", class_="kad_product")

    products = []
    for container in product_containers:
        name_tag = container.find("h5")
        name = name_tag.text.strip() if name_tag else "No name found"
        logging.debug(name)
        price_tag = container.find("ins")
        if price_tag:
            price = price_tag.find("span", class_="woocommerce-Price-amount").text.strip()
        else:
            price_tag = container.find("span", class_="woocommerce-Price-amount")
            price = price_tag.text.strip() if price_tag else "No price found"

        link_tag = container.find("a", class_="product_item_link")
        href = link_tag["href"] if link_tag else "No link found"
        name = clean_product_name(name)
        price = convert_price_to_int(price)

        products.append({"name": name, "price": price, "link": href, "inStock": True})

    return products


def scrape_all_products(base_url: str) -> Optional[list]:
    """Scrape all products from Gameloot by paginating through pages.

    Args:
        base_url: Base URL of the Gameloot product category.

    Returns:
        List of all product dictionaries if successful, or ScrapeResult.SCRAPE_FAILED
        if any page returned a non-200/404 error.
    """
    all_products = []
    page_number = 1
    while True:
        logging.info(f"Scraping page: {page_number}")
        url = _listing_page_url(base_url, page_number)
        products = scrape_product_page(url)

        # Check for scrape failure - abort immediately
        if products == ScrapeResult.SCRAPE_FAILED.value:
            logging.error(f"Scraping failed on page {page_number}. Aborting entire scrape run.")
            return ScrapeResult.SCRAPE_FAILED.value

        # None means 404 - end of pagination (expected)
        if products is None:
            logging.info(f"No more product listing. End of page")
            break

        # Empty list means no products found on this page (shouldn't happen, but handle gracefully)
        if not products:
            logging.info(f"No products found on page {page_number}. End of page")
            break

        all_products.extend(products)
        page_number += 1

    return all_products


# Single collection for all Gameloot product types (gpu, cpu, mobo, ram)
GAMELOOT_COLLECTION = "gameloot_products"


def process_gameloot_stock(base_url: str = "https://gameloot.in/product-category/graphics-card", product_type: str = "gpu") -> Optional[str]:
    """Process Gameloot stock updates and send notifications for new/back in stock items.
    Uses a single collection with a 'type' field (gpu, cpu, mobo, ram).

    Args:
        base_url: URL of the Gameloot product category to scrape.
        product_type: Type of product being tracked (gpu, cpu, mobo, ram).

    Returns:
        ScrapeResult value or None on success.
    """
    logging.info(f"Started at: {datetime.now()}")
    all_products = scrape_all_products(base_url)
    if all_products == ScrapeResult.SCRAPE_FAILED.value:
        logging.warning("Scraping failed with non-200 response. Aborting to prevent false 'sold' notifications. Will retry on next scheduled run.")
        return ScrapeResult.SCRAPE_FAILED.value

    all_products = remove_list_duplicates(all_products)
    # Add type to each product for single-collection storage
    for product in all_products:
        product["type"] = product_type
    # Print the extracted product details
    for product in all_products:
        logging.debug(f"Product Name: {product['name']}, Price: {product['price']}, Link: {product['link']}")
    logging.info(f"Total Products: {len(all_products)}")

    # Get MongoDB connection with retry logic (single collection)
    try:
        mongo_col = get_mongo_conn(GAMELOOT_COLLECTION, retry=True)
    except (ServerSelectionTimeoutError, ConnectionFailure, PyMongoError) as e:
        logging.error(f"MongoDB not available for {GAMELOOT_COLLECTION}: {e}")
        logging.info("Will retry on next scheduled run")
        return ScrapeResult.MONGODB_UNAVAILABLE.value

    link_set = set()
    all_new_item_text = "NEW PRODUCT IN STOCK! :"
    all_sold_item_text = "NO LONGER IN STOCK, SOLD!:"
    count_new_items = 0
    count_sold_items = 0
    logging.info("Finding new items")
    for product in all_products:
        logging.debug("*************")
        logging.debug(product)
        query = {"link": product["link"], "type": product_type}
        link_set.add(product["link"])
        result = mongo_col.find_one(query)
        now = datetime.utcnow()

        if result:
            if result["inStock"] == False:  # Product which were out of stock in db
                logging.debug(f"RESULT: {result}")
                logging.info(f"Back in Stock: {product['name']}, {product['price']}, {product['link']}")
                new_item = f"\n\n-{product['name']} - {product['price']} - {product['link']}"
                all_new_item_text = all_new_item_text + new_item
                count_new_items += 1

        else:  # New Product not in db
            logging.info(f"New Listing: {product['name']}, {product['price']}")
            new_item = f"\n\n-{product['name']} - {product['price']} - {product['link']}"
            all_new_item_text = all_new_item_text + new_item
            count_new_items += 1

        # Build update: always set product fields and priceUpdatedAt
        set_doc = {**product, "priceUpdatedAt": now}
        update = {"$set": set_doc}

        if result is None:
            # New product: set firstSeenAt and initial priceHistory entry
            set_doc["firstSeenAt"] = now
            set_doc["priceHistory"] = [{"price": product["price"], "at": now, "inStock": True}]
        else:
            # Push to priceHistory only when price changed or product was OOS (restock)
            price_changed = result.get("price") != product["price"]
            was_out_of_stock = result.get("inStock") is False
            if price_changed or was_out_of_stock:
                update["$push"] = {
                    "priceHistory": {
                        "price": product["price"],
                        "at": now,
                        "inStock": True,
                    }
                }

        query_res = mongo_col.update_one(query, update, upsert=True)
        if not query_res.raw_result["ok"]:
            print(query_res.raw_result)
            raise Exception("Mongo update failed: ", query_res.raw_result)

    logging.info("Finding Sold Items")
    result = mongo_col.find({"type": product_type})
    for db_product in result:
        logging.debug("------------------")
        logging.debug(db_product)
        if db_product["link"] in link_set:
            # print("Its in set")
            continue
        elif db_product["inStock"] == True:
            logging.debug("inStock True")
            logging.info(f"No Longer in Stock: {db_product['name']}, {db_product['price']}")
            sold_item = f"\n\n-{db_product['name']} - {db_product['price']} - {db_product['link']}"
            all_sold_item_text = all_sold_item_text + sold_item
            count_sold_items += 1
            now = datetime.utcnow()
            update = {
                "$set": {"inStock": False},
                "$push": {
                    "priceHistory": {
                        "price": db_product["price"],
                        "at": now,
                        "inStock": False,
                    }
                },
            }
            query = {"link": db_product["link"], "type": product_type}
            query_res = mongo_col.update_one(query, update, upsert=True)
            if not query_res.raw_result["ok"]:
                print(query_res.raw_result)
                raise Exception("Mongo update failed: ", query_res.raw_result)
        logging.debug("$$$ Not in SET $$$")

    logging.info(f"# New Listing/Back in Stock items: {count_new_items}")
    logging.info(f"# No Longer in Stock: {count_sold_items}")
    logging.info("Sending Telegram Messages")
    if count_new_items >= 1:
        asyncio.run(send_telegram_message(all_new_item_text))
    if count_sold_items >= 1:
        asyncio.run(send_telegram_message(all_sold_item_text))
    logging.info("Completed")


# Product type configuration
_PRODUCT_CONFIG = {
    "gpu": "https://gameloot.in/product-category/graphics-card",
    "cpu": "https://gameloot.in/product-category/buy-cpu/",
    "mobo": "https://gameloot.in/product-category/motherboard/",
    "ram": "https://gameloot.in/product-category/desktop-ram/",
}


def _track_product(product_type: str) -> Optional[str]:
    """Track Gameloot stock for a specific product type.

    Args:
        product_type: Type of product to track (gpu, cpu, mobo, ram).

    Returns:
        ScrapeResult value if tracking was skipped, None otherwise.
    """
    base_url = _PRODUCT_CONFIG.get(product_type)
    if not base_url:
        logging.error(f"Unknown product type: {product_type}")
        return ScrapeResult.SCRAPE_FAILED.value

    try:
        logging.info(f"Tracking {product_type.upper()}")
        result = process_gameloot_stock(base_url, product_type=product_type)
        if result == ScrapeResult.MONGODB_UNAVAILABLE.value:
            logging.warning(f"{product_type.upper()} tracking skipped due to MongoDB unavailability")
        elif result == ScrapeResult.SCRAPE_FAILED.value:
            logging.warning(f"{product_type.upper()} tracking skipped due to scraping failure (non-200 response)")
        return result
    except Exception as e:
        logging.error(f"Error in track_{product_type}: {e}", exc_info=True)
        return ScrapeResult.SCRAPE_FAILED.value


def track_gpu() -> Optional[str]:
    """Track Gameloot GPU stock."""
    return _track_product("gpu")


def track_cpu() -> Optional[str]:
    """Track Gameloot CPU stock."""
    return _track_product("cpu")


def track_mobo() -> Optional[str]:
    """Track Gameloot Motherboard stock."""
    return _track_product("mobo")


def track_ram() -> Optional[str]:
    """Track Gameloot RAM stock."""
    return _track_product("ram")
