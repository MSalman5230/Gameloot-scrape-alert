import unittest
from unittest.mock import Mock, patch

import requests

import gameloot


URL = "https://gameloot.in/product-category/graphics-card/page/1/?stock=instock"
PRODUCT = """
<div class="kad_product">
  <a class="product_item_link" href="https://gameloot.in/shop/test-gpu/">
    <h5>Test GPU (Pre-owned)</h5>
  </a>
  <del><span class="woocommerce-Price-amount">Rs.49,999</span></del>
  <ins><span class="woocommerce-Price-amount"><bdi><span
    class="woocommerce-Price-currencySymbol">Rs.</span>44,999</bdi></span></ins>
</div>
"""
EMPTY = '<p class="woocommerce-info">No products were found matching your selection.</p>'


def response(html=PRODUCT, status=200):
    return Mock(status_code=status, content=html.encode("utf-8"))


class PriceTests(unittest.TestCase):
    def test_supported_whole_rupee_formats(self):
        for value in ["Rs.44,999", "Rs. 44,999", "Rs.\xa044,999", "₹44,999",
                      "INR 44,999.00", "44,999", " 44999 "]:
            with self.subTest(value=value):
                self.assertEqual(gameloot.convert_price_to_int(value), 44999)

    def test_invalid_or_fractional_prices_are_not_silently_changed(self):
        for value in ["No price found", "", "Rs.4,999.50", "Rs.100 - Rs.200", "-100"]:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    gameloot.convert_price_to_int(value)


class ScrapeTests(unittest.TestCase):
    @patch.object(gameloot._gameloot_http, "get")
    def test_live_markup_uses_sale_price(self, get):
        get.return_value = response()
        self.assertEqual(gameloot.scrape_product_page(URL), [{
            "name": "Test GPU", "price": 44999,
            "link": "https://gameloot.in/shop/test-gpu/", "inStock": True,
        }])
        self.assertEqual(get.call_args.kwargs["timeout"], gameloot._REQUEST_TIMEOUT)

    @patch.object(gameloot._gameloot_http, "get")
    def test_regular_price(self, get):
        get.return_value = response(PRODUCT.replace("<del>", "").replace("</del>", "")
                                    .split("<ins>")[0] + "</div>")
        self.assertEqual(gameloot.scrape_product_page(URL)[0]["price"], 49999)

    @patch.object(gameloot._gameloot_http, "get")
    def test_bad_product_aborts_entire_page(self, get):
        for broken in [PRODUCT.replace("44,999", "unavailable"),
                       PRODUCT.replace('class="woocommerce-Price-amount"', 'class="changed"'),
                       PRODUCT.replace("<h5>Test GPU (Pre-owned)</h5>", ""),
                       PRODUCT.replace('href="https://gameloot.in/shop/test-gpu/"', "")]:
            with self.subTest(html=broken):
                get.return_value = response(PRODUCT + broken)
                self.assertEqual(gameloot.scrape_product_page(URL), "SCRAPE_FAILED")

    @patch.object(gameloot._gameloot_http, "get")
    def test_explicit_empty_category(self, get):
        get.return_value = response(EMPTY)
        self.assertEqual(gameloot.scrape_product_page(URL), [])

    @patch.object(gameloot._gameloot_http, "get")
    def test_unrecognized_page_fails(self, get):
        get.return_value = response("<html><h1>Please wait</h1></html>")
        self.assertEqual(gameloot.scrape_product_page(URL), "SCRAPE_FAILED")

    @patch.object(gameloot._gameloot_http, "get")
    def test_http_error(self, get):
        get.return_value = response(status=403)
        self.assertEqual(gameloot.scrape_product_page(URL), "SCRAPE_FAILED")

    @patch.object(gameloot._gameloot_http, "get")
    def test_timeout(self, get):
        get.side_effect = requests.Timeout("timed out")
        self.assertEqual(gameloot.scrape_product_page(URL), "SCRAPE_FAILED")

    @patch.object(gameloot._gameloot_http, "get")
    def test_pagination(self, get):
        get.side_effect = [response(), response(PRODUCT.replace("test-gpu/", "second-gpu/")),
                           response(status=404)]
        products = gameloot.scrape_all_products("https://gameloot.in/product-category/graphics-card/")
        self.assertEqual(len(products), 2)
        self.assertIn("/page/2/?stock=instock", get.call_args_list[1].args[0])

    @patch.object(gameloot._gameloot_http, "get")
    def test_missing_category_is_failure(self, get):
        get.return_value = response(status=404)
        self.assertEqual(gameloot.scrape_all_products(URL), "SCRAPE_FAILED")

    @patch("gameloot.send_telegram_message")
    @patch("gameloot.get_mongo_conn")
    @patch.object(gameloot._gameloot_http, "get")
    def test_failed_later_page_cannot_update_stock_or_send_alerts(self, get, mongo, send):
        get.side_effect = [response(), response(PRODUCT.replace("44,999", "unavailable"))]
        self.assertEqual(gameloot.process_gameloot_stock(), "SCRAPE_FAILED")
        mongo.assert_not_called()
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
