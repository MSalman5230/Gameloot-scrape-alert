import httpx
import pytest
import respx

from stockwatch.models import ScrapedItem
from stockwatch.sites.base import Progress
from stockwatch.sites.gameloot import Gameloot, clean_name, listing_page_url, parse_price
from stockwatch.sites.http import ScrapeError, make_site_http

BASE = "https://gameloot.in/product-category/graphics-card/"
PAGE1 = listing_page_url(BASE, 1)
PAGE2 = listing_page_url(BASE, 2)
PAGE3 = listing_page_url(BASE, 3)

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
TEST_GPU = ScrapedItem("https://gameloot.in/shop/test-gpu/", "Test GPU", 44999)


@pytest.fixture
async def http():
    site_http = make_site_http(Gameloot())  # real client config: browser headers, timeouts
    site_http.request_delay, site_http.max_retries, site_http.retry_backoff = 0, 1, 0
    yield site_http
    await site_http.aclose()


@pytest.fixture
def mock():
    with respx.mock(assert_all_mocked=True) as router:
        yield router


async def scrape(http, url=BASE):
    return await Gameloot().scrape_category(http, url, Progress())


def page(mock, url, html="", status=200):
    return mock.get(url).mock(return_value=httpx.Response(status, text=html))


# -- pure parsing ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value", ["Rs.44,999", "Rs. 44,999", "Rs.\xa044,999", "₹44,999", "INR 44,999.00", "44,999", " 44999 "]
)
def test_supported_whole_rupee_formats(value):
    assert parse_price(value) == 44999


@pytest.mark.parametrize("value", ["No price found", "", "Rs.4,999.50", "Rs.100 - Rs.200", "-100"])
def test_invalid_or_fractional_prices_are_rejected(value):
    with pytest.raises(ValueError):
        parse_price(value)


def test_clean_name_drops_last_parenthesised_suffix():
    assert clean_name("RTX 3080 (10GB) (Pre-owned)") == "RTX 3080 (10GB)"
    assert clean_name("  Plain name ") == "Plain name"


def test_listing_url_normalises_trailing_slash():
    assert listing_page_url(BASE, 2) == listing_page_url(BASE.rstrip("/"), 2)
    assert PAGE2.endswith("/graphics-card/page/2/?stock=instock")


# -- scraping ----------------------------------------------------------------------------------


async def test_uses_sale_price_and_browser_headers(http, mock):
    first = page(mock, PAGE1, PRODUCT)
    page(mock, PAGE2, status=404)
    assert await scrape(http) == [TEST_GPU]
    request = first.calls.last.request
    assert request.headers["Referer"] == "https://gameloot.in/"
    assert "Chrome/" in request.headers["User-Agent"]


async def test_regular_price_when_not_on_sale(http, mock):
    regular = PRODUCT.replace("<del>", "").replace("</del>", "").split("<ins>")[0] + "</div>"
    page(mock, PAGE1, regular)
    page(mock, PAGE2, status=404)
    assert (await scrape(http))[0].price == 49999


@pytest.mark.parametrize(
    "broken",
    [
        PRODUCT.replace("44,999", "unavailable"),
        PRODUCT.replace('class="woocommerce-Price-amount"', 'class="changed"'),
        PRODUCT.replace("<h5>Test GPU (Pre-owned)</h5>", ""),
        PRODUCT.replace('href="https://gameloot.in/shop/test-gpu/"', ""),
    ],
)
async def test_one_bad_product_fails_the_whole_scrape(http, mock, broken):
    page(mock, PAGE1, PRODUCT + broken)
    with pytest.raises(ScrapeError):
        await scrape(http)


async def test_explicit_empty_category(http, mock):
    page(mock, PAGE1, EMPTY)
    assert await scrape(http) == []


async def test_unrecognized_page_fails(http, mock):
    page(mock, PAGE1, "<html><h1>Please wait</h1></html>")
    with pytest.raises(ScrapeError, match="Unrecognized"):
        await scrape(http)


async def test_http_error_fails(http, mock):
    page(mock, PAGE1, status=403)
    with pytest.raises(ScrapeError, match="HTTP 403"):
        await scrape(http)


async def test_timeout_fails_after_retries(http, mock):
    route = mock.get(PAGE1).mock(side_effect=httpx.ConnectTimeout("timed out"))
    with pytest.raises(ScrapeError, match="Request failed"):
        await scrape(http)
    assert route.call_count == 2  # first try + one retry


async def test_retries_transient_server_errors(http, mock):
    mock.get(PAGE1).mock(side_effect=[httpx.Response(503), httpx.Response(200, text=PRODUCT)])
    page(mock, PAGE2, status=404)
    assert await scrape(http) == [TEST_GPU]


async def test_pagination_until_404(http, mock):
    page(mock, PAGE1, PRODUCT)
    page(mock, PAGE2, PRODUCT.replace("test-gpu/", "second-gpu/"))
    page(mock, PAGE3, status=404)
    progress = Progress()
    items = await Gameloot().scrape_category(http, BASE, progress)
    assert [i.url for i in items] == [TEST_GPU.url, "https://gameloot.in/shop/second-gpu/"]
    assert (progress.pages, progress.items) == (2, 2)


async def test_pagination_until_empty_notice(http, mock):
    page(mock, PAGE1, PRODUCT)
    page(mock, PAGE2, EMPTY)
    assert await scrape(http) == [TEST_GPU]


async def test_missing_category_is_failure(http, mock):
    page(mock, PAGE1, status=404)
    with pytest.raises(ScrapeError, match="Category not found"):
        await scrape(http)


async def test_failed_later_page_fails_everything(http, mock):
    page(mock, PAGE1, PRODUCT)
    page(mock, PAGE2, PRODUCT.replace("44,999", "unavailable"))
    with pytest.raises(ScrapeError):
        await scrape(http)


async def test_repeated_page_is_detected(http, mock):
    page(mock, PAGE1, PRODUCT)
    page(mock, PAGE2, PRODUCT)  # e.g. the site redirects past-the-end pages back to page 1
    with pytest.raises(ScrapeError, match="pagination"):
        await scrape(http)
