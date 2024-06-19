import requests
from bs4 import BeautifulSoup
import pymongo
import asyncio
import time
from datetime import datetime
import logging
import os
from telegram_helper import send_telegram_message
import logging_config
import schedule


def get_mongo_conn(collection):
    client = pymongo.MongoClient("mongodb://huruhuru:huruhuru42@192.168.11.3:27017/?authMechanism=DEFAULT&authSource=huruhuru")
    db = client["huruhuru"]
    collection = db[collection]
    return collection


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
        return ["FAILED"]
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
    mongo_col = get_mongo_conn(mongo_col_name)
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
    logging.info("Tacking GPU")
    gpu_base_url = "https://gameloot.in/product-category/graphics-card"
    mongo_col_name = "gameloot_gpu"
    process_gameloot_stock(gpu_base_url, mongo_col_name)


def track_cpu():
    logging.info("Tacking CPU")
    cpu_base_url = "https://gameloot.in/product-category/buy-cpu/"
    mongo_col_name = "gameloot_cpu"
    process_gameloot_stock(cpu_base_url, mongo_col_name)


def track_mobo():
    logging.info("Tacking Mobo")
    motherboard_base_url = "https://gameloot.in/product-category/motherboard/"
    mongo_col_name = "gameloot_mobo"
    process_gameloot_stock(motherboard_base_url, mongo_col_name)


def task_scheduler():
    schedule.every(15).minutes.do(track_gpu)
    schedule.every(22).minutes.do(track_cpu)
    schedule.every(27).minutes.do(track_mobo)
    logging.info("Scheduler started with Jobs:")
    for jobs in schedule.get_jobs():
        print(jobs)

    while True:
        try:
            schedule.run_pending()
        except Exception as e:
            print(e)
        time.sleep(1)


def run_in_loop():
    while True:
        process_gameloot_stock()
        print("Sleeping for 15 min")
        logging.debug("\n\n\n\n\n\n\n\n\n\n\n\n")
        time.sleep(900)


if __name__ == "__main__":
    task_scheduler()
    # process_gameloot_stock()
    # track_cpu()
    # track_mobo()
