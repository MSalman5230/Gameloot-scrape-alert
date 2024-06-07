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


def scrape_product_page(url):
    response = requests.get(url)
    webpage_content = response.content
    soup = BeautifulSoup(webpage_content, "html.parser")
    product_containers = soup.find_all("div", class_="kad_product")

    products = []
    for container in product_containers:
        name_tag = container.find("h5")
        name = name_tag.text.strip() if name_tag else "No name found"

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
        url = f"{base_url}/page/{page_number}/?swoof=1&stock=instock&orderby=price"
        products = scrape_product_page(url)
        if not products:
            logging.info(f"No more product listing. End of page")
            break
        all_products.extend(products)
        page_number += 1
    return all_products


def process_gameloot_stock():
    logging.info(f"Started at: {datetime.now()}")
    base_url = "https://gameloot.in/product-category/graphics-card"
    all_products = scrape_all_products(base_url)

    # Print the extracted product details
    for product in all_products:
        # print(f"Product Name: {product['name']}, Price: {product['price']}, Link: {product['link']}")
        logging.debug(f"Product Name: {product['name']}, Price: {product['price']}, Link: {product['link']}")

    mongo_col = get_mongo_conn("gameloot_gpu")
    link_set = set()
    all_new_item_text = "NEW PRODUCT IN STOCK! :"
    all_sold_item_text = "NO LONGER IN STOCK, SOLD!:"
    count_new_items = 0
    count_sold_items = 0
    for product in all_products:
        query = {"link": product["link"]}
        link_set.add(product["link"])
        result = mongo_col.find_one(query)
        if result:
            if result["inStock"] == False:  # Product which were our of stock in db
                logging.info(f"Back in Stock: {product['name']}, {product['price']}")
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
        mongo_col.update_one(query, update, upsert=True)

    result = mongo_col.find()
    for db_product in result:
        if db_product["link"] in link_set:
            continue
        elif db_product["inStock"] == True:
            update = {"$set": {"inStock": False}}
            query = {"link": db_product["link"]}
            mongo_col.update_one(query, update, upsert=True)
            logging.info(f"No Longer in Stock: {product['name']}, {product['price']}")
            sold_item = f"\n\n-{product['name']} - {product['price']} - {product['link']}"
            all_sold_item_text = all_sold_item_text + sold_item
            count_sold_items += 1

    logging.info(f"# New Listing/Back in Stock items: {count_new_items}")
    logging.info(f"# No Longer in Stock: {count_sold_items}")
    logging.info("Sending Telegram Messages")
    if count_new_items > 1:
        asyncio.run(send_telegram_message(all_new_item_text))
    if count_sold_items > 1:
        asyncio.run(send_telegram_message(all_sold_item_text))
    logging.info("Completed")


def run_in_loop():

    while True:
        process_gameloot_stock()
        print("Sleeping for 30 min")
        time.sleep(1800)


if __name__ == "__main__":
    run_in_loop()
