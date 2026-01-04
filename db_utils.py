import pymongo
from pymongo.errors import ServerSelectionTimeoutError, ConnectionFailure, PyMongoError
import time
import logging
import os

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

        client = pymongo.MongoClient(mongo_uri, serverSelectionTimeoutMS=timeout * 1000)
        # Try to ping the server
        client.admin.command("ping")
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
            client = pymongo.MongoClient(mongo_uri, serverSelectionTimeoutMS=MONGO_CONNECTION_TIMEOUT * 1000)
            # Verify connection
            client.admin.command("ping")
            db = client["huruhuru"]
            collection_obj = db[collection]
            return collection_obj
        except (ServerSelectionTimeoutError, ConnectionFailure, PyMongoError) as e:
            if attempt < max_retries - 1 and retry:
                wait_time = min(MONGO_RETRY_DELAY * (2**attempt), MONGO_MAX_RETRY_DELAY)
                logging.warning(f"MongoDB connection failed (attempt {attempt + 1}/{max_retries}): {e}")
                logging.info(f"Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
            else:
                logging.error(f"Failed to connect to MongoDB after {max_retries} attempts: {e}")
                raise
        except Exception as e:
            logging.error(f"Unexpected error connecting to MongoDB: {e}")
            raise


def remove_list_duplicates(dict_list):
    """Remove duplicate dictionaries from a list."""
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
