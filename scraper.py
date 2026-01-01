import requests
from bs4 import BeautifulSoup
import pymongo
from pymongo.errors import ServerSelectionTimeoutError, ConnectionFailure, PyMongoError
import asyncio
import time
from datetime import datetime
import logging
import os
from telegram_helper import send_telegram_message
import logging_config
import schedule
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# MongoDB connection settings
MONGO_CONNECTION_TIMEOUT = 5  # seconds
MONGO_RETRY_DELAY = 5  # seconds between retries
MONGO_MAX_RETRY_DELAY = 60  # maximum delay between retries


def check_mongodb_available(mongo_uri=None, timeout=MONGO_CONNECTION_TIMEOUT):
    """
    Check if MongoDB is available and accessible.
    Returns True if available, False otherwise.
    """
    try:
        if mongo_uri is None:
            mongo_uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
        
        client = pymongo.MongoClient(
            mongo_uri,
            serverSelectionTimeoutMS=timeout * 1000
        )
        # Try to ping the server
        client.admin.command('ping')
        client.close()
        return True
    except (ServerSelectionTimeoutError, ConnectionFailure, Exception) as e:
        logging.debug(f"MongoDB check failed: {e}")
        return False


def wait_for_mongodb(max_wait_time=None, check_interval=MONGO_RETRY_DELAY):
    """
    Wait for MongoDB to become available.
    Blocks until MongoDB is available or max_wait_time is reached.
    
    Args:
        max_wait_time: Maximum time to wait in seconds (None for infinite wait)
        check_interval: Time between checks in seconds
    
    Returns:
        True if MongoDB is available, False if max_wait_time was reached
    """
    mongo_uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
    start_time = time.time()
    attempt = 0
    
    logging.info("Waiting for MongoDB to become available...")
    
    while True:
        attempt += 1
        if check_mongodb_available(mongo_uri):
            elapsed = time.time() - start_time
            logging.info(f"MongoDB is now available! (waited {elapsed:.1f} seconds, {attempt} attempts)")
            return True
        
        if max_wait_time is not None:
            elapsed = time.time() - start_time
            if elapsed >= max_wait_time:
                logging.error(f"MongoDB not available after {max_wait_time} seconds")
                return False
        
        logging.warning(f"MongoDB not available (attempt {attempt}), retrying in {check_interval} seconds...")
        time.sleep(check_interval)


def get_mongo_conn(collection, retry=True, max_retries=3):
    """
    Get MongoDB collection connection with retry logic.
    
    Args:
        collection: Collection name
        retry: Whether to retry on failure
        max_retries: Maximum number of retry attempts
    
    Returns:
        MongoDB collection object
    
    Raises:
        ConnectionFailure: If MongoDB is not available after retries
    """
    mongo_uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
    
    for attempt in range(max_retries):
        try:
            client = pymongo.MongoClient(
                mongo_uri,
                serverSelectionTimeoutMS=MONGO_CONNECTION_TIMEOUT * 1000
            )
            # Verify connection
            client.admin.command('ping')
            db = client["huruhuru"]
            collection_obj = db[collection]
            return collection_obj
        except (ServerSelectionTimeoutError, ConnectionFailure, PyMongoError) as e:
            if attempt < max_retries - 1 and retry:
                wait_time = min(MONGO_RETRY_DELAY * (2 ** attempt), MONGO_MAX_RETRY_DELAY)
                logging.warning(f"MongoDB connection failed (attempt {attempt + 1}/{max_retries}): {e}")
                logging.info(f"Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
            else:
                logging.error(f"Failed to connect to MongoDB after {max_retries} attempts: {e}")
                raise
        except Exception as e:
            logging.error(f"Unexpected error connecting to MongoDB: {e}")
            raise


def convert_price_to_int(price_str):
    # Replace non-breaking space character with regular space, remove "Rs." and commas, then convert to integer
    price_str = price_str.replace("\xa0", " ").replace("Rs. ", "").replace(",", "")
    return int(price_str)


def clean_product_name(name):
    # Find the last position of the last occurrence of '('
    pos = name.rfind("(")
    # If a '(' is found, return the substring before it, else return the original name
    if pos != -1:
        return name[:pos].strip()
    return name.strip()


def remove_list_duplicates(dict_list):
    logging.info("removing product duplicate")
    logging.info(f"Original list length: {len(dict_list)}")
    for i in dict_list:
        logging.debug(i)
    # Convert each dictionary to a tuple of sorted items
    tuple_list = [tuple(sorted(d.items())) for d in dict_list]

    # Remove duplicates by converting the list of tuples to a set
    unique_tuples = set(tuple_list)

    # Convert the tuples back to dictionaries
    unique_dict_list = [dict(t) for t in unique_tuples]
    logging.info(f"Deduplicate list length: {len(unique_dict_list)}")
    return unique_dict_list


def scrape_product_page(url):
    response = requests.get(url)
    if not (response.status_code == 200 or response.status_code == 404):
        print("Correct:", response.status_code)
        return None
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
    all_products = []
    page_number = 1
    while True:
        logging.info(f"Scraping page: {page_number}")
        url = f"{base_url}/page/{page_number}/?stock=instock"
        products = scrape_product_page(url)
        if not products:
            logging.info(f"No more product listing. End of page")
            break
        all_products.extend(products)
        page_number += 1
    if "FAILED" in all_products:
        return "FAILED"
    return all_products


def process_gameloot_stock(base_url="https://gameloot.in/product-category/graphics-card", mongo_col_name="gameloot_gpu"):
    logging.info(f"Started at: {datetime.now()}")
    # base_url = "https://gameloot.in/product-category/graphics-card"
    all_products = scrape_all_products(base_url)
    if "FAILED" in all_products:
        logging.warning("FAILED to scrape pages, Try after sleeping")
        return "FAILED"

    all_products = remove_list_duplicates(all_products)
    # Print the extracted product details
    for product in all_products:
        logging.debug(f"Product Name: {product['name']}, Price: {product['price']}, Link: {product['link']}")
    logging.info(f"Total Products: {len(all_products)}")
    
    # Get MongoDB connection with retry logic
    try:
        mongo_col = get_mongo_conn(mongo_col_name, retry=True)
    except (ServerSelectionTimeoutError, ConnectionFailure, PyMongoError) as e:
        logging.error(f"MongoDB not available for {mongo_col_name}: {e}")
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
        query = {"link": product["link"]}
        link_set.add(product["link"])
        result = mongo_col.find_one(query)
        if result:
            if result["inStock"] == False:  # Product which were our of stock in db
                logging.debug("RESULT", result)
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
    result = mongo_col.find()
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
            query = {"link": db_product["link"]}
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


# --------------------------------------------------------------------
def track_gpu():
    try:
        logging.info("Tracking GPU")
        gpu_base_url = "https://gameloot.in/product-category/graphics-card"
        mongo_col_name = "gameloot_gpu"
        result = process_gameloot_stock(gpu_base_url, mongo_col_name)
        if result == "MONGODB_UNAVAILABLE":
            logging.warning("GPU tracking skipped due to MongoDB unavailability")
    except Exception as e:
        logging.error(f"Error in track_gpu: {e}", exc_info=True)


def track_cpu():
    try:
        logging.info("Tracking CPU")
        cpu_base_url = "https://gameloot.in/product-category/buy-cpu/"
        mongo_col_name = "gameloot_cpu"
        result = process_gameloot_stock(cpu_base_url, mongo_col_name)
        if result == "MONGODB_UNAVAILABLE":
            logging.warning("CPU tracking skipped due to MongoDB unavailability")
    except Exception as e:
        logging.error(f"Error in track_cpu: {e}", exc_info=True)


def track_mobo():
    try:
        logging.info("Tracking Mobo")
        motherboard_base_url = "https://gameloot.in/product-category/motherboard/"
        mongo_col_name = "gameloot_mobo"
        result = process_gameloot_stock(motherboard_base_url, mongo_col_name)
        if result == "MONGODB_UNAVAILABLE":
            logging.warning("Mobo tracking skipped due to MongoDB unavailability")
    except Exception as e:
        logging.error(f"Error in track_mobo: {e}", exc_info=True)


def track_ram():
    try:
        logging.info("Tracking RAM")
        motherboard_base_url = "https://gameloot.in/product-category/desktop-ram/"
        mongo_col_name = "gameloot_ram"
        result = process_gameloot_stock(motherboard_base_url, mongo_col_name)
        if result == "MONGODB_UNAVAILABLE":
            logging.warning("RAM tracking skipped due to MongoDB unavailability")
    except Exception as e:
        logging.error(f"Error in track_ram: {e}", exc_info=True)


def task_scheduler():
    schedule.every(15).minutes.do(track_gpu)
    schedule.every(22).minutes.do(track_cpu)
    schedule.every(27).minutes.do(track_mobo)
    # schedule.every(27).minutes.do(track_ram)
    logging.info("Scheduler started with Jobs:")
    for jobs in schedule.get_jobs():
        print(jobs)

    while True:
        try:
            schedule.run_pending()
        except Exception as e:
            logging.error(f"Error in scheduler: {e}", exc_info=True)
        time.sleep(1)




if __name__ == "__main__":
    task_scheduler()
    # process_gameloot_stock()
    # track_cpu()
    # track_mobo()
    # track_ram()
