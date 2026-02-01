import requests
from bs4 import BeautifulSoup
from pymongo.errors import ServerSelectionTimeoutError, ConnectionFailure, PyMongoError
import asyncio
import logging
from datetime import datetime
from telegram_helper import send_telegram_message
from db_utils import get_mongo_conn, remove_list_duplicates


def convert_price_to_int(price_str):
    """Convert Gameloot price string to integer."""
    # Replace non-breaking space character with regular space, remove "Rs." and commas, then convert to integer
    price_str = price_str.replace("\xa0", " ").replace("Rs. ", "").replace(",", "")
    return int(price_str)


def clean_product_name(name):
    """Clean Gameloot product name by removing content in parentheses."""
    # Find the last position of the last occurrence of '('
    pos = name.rfind("(")
    # If a '(' is found, return the substring before it, else return the original name
    if pos != -1:
        return name[:pos].strip()
    return name.strip()


def scrape_product_page(url):
    """Scrape a single Gameloot product page.

    Returns:
        list: List of product dictionaries if successful
        None: If page 404 (end of pagination)
        "SCRAPE_FAILED": If non-200/404 error occurred
    """
    response = requests.get(url)

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


def scrape_all_products(base_url):
    """Scrape all products from Gameloot by paginating through pages.

    Returns:
        list: List of all product dictionaries if successful
        "SCRAPE_FAILED": If any page returned a non-200/404 error
    """
    all_products = []
    page_number = 1
    while True:
        logging.info(f"Scraping page: {page_number}")
        url = f"{base_url}/page/{page_number}/?stock=instock"
        products = scrape_product_page(url)

        # Check for scrape failure - abort immediately
        if products == "SCRAPE_FAILED":
            logging.error(f"Scraping failed on page {page_number}. Aborting entire scrape run.")
            return "SCRAPE_FAILED"

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


def process_gameloot_stock(base_url="https://gameloot.in/product-category/graphics-card", product_type="gpu"):
    """Process Gameloot stock updates and send notifications for new/back in stock items.
    Uses a single collection with a 'type' field (gpu, cpu, mobo, ram)."""
    logging.info(f"Started at: {datetime.now()}")
    all_products = scrape_all_products(base_url)
    if all_products == "SCRAPE_FAILED":
        logging.warning("Scraping failed with non-200 response. Aborting to prevent false 'sold' notifications. Will retry on next scheduled run.")
        return "SCRAPE_FAILED"

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
        return "MONGODB_UNAVAILABLE"

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
        if result:
            if result["inStock"] == False:  # Product which were our of stock in db
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

        # print("Inserting to Mongo")
        update = {"$set": product}
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
            update = {"$set": {"inStock": False}}
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


def track_gpu():
    """Track Gameloot GPU stock."""
    try:
        logging.info("Tracking GPU")
        gpu_base_url = "https://gameloot.in/product-category/graphics-card"
        result = process_gameloot_stock(gpu_base_url, product_type="gpu")
        if result == "MONGODB_UNAVAILABLE":
            logging.warning("GPU tracking skipped due to MongoDB unavailability")
        elif result == "SCRAPE_FAILED":
            logging.warning("GPU tracking skipped due to scraping failure (non-200 response)")
    except Exception as e:
        logging.error(f"Error in track_gpu: {e}", exc_info=True)


def track_cpu():
    """Track Gameloot CPU stock."""
    try:
        logging.info("Tracking CPU")
        cpu_base_url = "https://gameloot.in/product-category/buy-cpu/"
        result = process_gameloot_stock(cpu_base_url, product_type="cpu")
        if result == "MONGODB_UNAVAILABLE":
            logging.warning("CPU tracking skipped due to MongoDB unavailability")
        elif result == "SCRAPE_FAILED":
            logging.warning("CPU tracking skipped due to scraping failure (non-200 response)")
    except Exception as e:
        logging.error(f"Error in track_cpu: {e}", exc_info=True)


def track_mobo():
    """Track Gameloot Motherboard stock."""
    try:
        logging.info("Tracking Mobo")
        motherboard_base_url = "https://gameloot.in/product-category/motherboard/"
        result = process_gameloot_stock(motherboard_base_url, product_type="mobo")
        if result == "MONGODB_UNAVAILABLE":
            logging.warning("Mobo tracking skipped due to MongoDB unavailability")
        elif result == "SCRAPE_FAILED":
            logging.warning("Mobo tracking skipped due to scraping failure (non-200 response)")
    except Exception as e:
        logging.error(f"Error in track_mobo: {e}", exc_info=True)


def track_ram():
    """Track Gameloot RAM stock."""
    try:
        logging.info("Tracking RAM")
        ram_base_url = "https://gameloot.in/product-category/desktop-ram/"
        result = process_gameloot_stock(ram_base_url, product_type="ram")
        if result == "MONGODB_UNAVAILABLE":
            logging.warning("RAM tracking skipped due to MongoDB unavailability")
        elif result == "SCRAPE_FAILED":
            logging.warning("RAM tracking skipped due to scraping failure (non-200 response)")
    except Exception as e:
        logging.error(f"Error in track_ram: {e}", exc_info=True)
