"""Best-effort dollar-price extraction: one comparable numeric price per deal."""
from grocery_planner.scrapers.base import extract_dollar_price


def test_prefers_structured_fields():
    assert extract_dollar_price(sale_price="1.99") == "1.99"
    assert extract_dollar_price(discount_amount="2.00") == "2"
    assert extract_dollar_price(regular_price="4.50") == "4.5"
    # sale_price wins over description text
    assert extract_dollar_price(sale_price="1.25", deal_description="Save $9") == "1.25"


def test_extracts_from_description_tail():
    assert extract_dollar_price(deal_description="Tyson — $3.49/lb") == "3.49"
    assert extract_dollar_price(deal_description="Kellogg's - $2.99") == "2.99"


def test_extracts_save_and_off_phrasings():
    assert extract_dollar_price(deal_description="Save $5 on beef") == "5"
    assert extract_dollar_price(deal_description="$2 off cereal") == "2"


def test_falls_back_to_item_name():
    assert extract_dollar_price(item_name="Whole Milk $1.50") == "1.5"


def test_returns_empty_when_no_price():
    assert extract_dollar_price() == ""
    assert extract_dollar_price(deal_description="Weekly ad item", item_name="Mystery") == ""
