from datetime import UTC, datetime

from stockwatch.storage.migrate import legacy_to_product

T1 = datetime(2025, 1, 1, tzinfo=UTC)
T2 = datetime(2025, 2, 1, tzinfo=UTC)


def test_maps_v1_document():
    key, fields = legacy_to_product(
        {
            "_id": "x",
            "name": "RTX 3080",
            "price": 45000,
            "link": "https://gameloot.in/shop/rtx-3080/",
            "inStock": False,
            "type": "gpu",
            "firstSeenAt": T1,
            "priceUpdatedAt": T2,
            "priceHistory": [
                {"price": 50000, "at": T1, "inStock": True},
                {"price": 45000, "at": T2, "inStock": False},
            ],
        }
    )
    assert key == {"site": "gameloot", "category": "gpu", "url": "https://gameloot.in/shop/rtx-3080/"}
    assert fields["in_stock"] is False
    assert fields["first_seen_at"] == T1 and fields["last_seen_at"] == T2
    assert fields["price_history"] == [
        {"price": 50000, "at": T1, "in_stock": True},
        {"price": 45000, "at": T2, "in_stock": False},
    ]


def test_old_documents_without_history_still_migrate():
    key, fields = legacy_to_product(
        {"name": "X", "price": 1, "link": "https://g/x", "inStock": True, "type": "ram"}
    )
    assert key["category"] == "ram"
    assert fields["price_history"] == [] and fields["first_seen_at"] is None


def test_unusable_documents_are_skipped():
    assert legacy_to_product({"name": "X", "price": 1, "type": "gpu"}) is None  # no link
    assert legacy_to_product({"name": "X", "price": 1, "link": "https://g/x"}) is None  # no type
    assert legacy_to_product({"name": "X", "price": "1", "link": "https://g/x", "type": "gpu"}) is None
