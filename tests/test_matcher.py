from pokemon_preorder_bot.matcher import match_product_page, match_search_page

AVAILABLE = ["add to cart", "pre-order", "vorbestellen"]
UNAVAILABLE = ["sold out", "coming soon", "notify me", "ausverkauft"]


def test_search_page_separates_adjacent_product_cards():
    html = """
    <html><body>
    <div class="product-card">
      <h3>Pokemon TCG 30th Celebration Elite Trainer Box</h3>
      <span>$49.99</span><button>Add to Cart</button>
    </div>
    <div class="product-card">
      <h3>Sylveon ex Box</h3>
      <span>$24.99</span><span>Coming Soon - Notify Me</span>
    </div>
    </body></html>
    """
    results = match_search_page(html, ["Elite Trainer Box", "Sylveon ex"], AVAILABLE, UNAVAILABLE)
    by_keyword = {r.keyword: r.status for r in results}
    assert by_keyword["Elite Trainer Box"] == "available"
    assert by_keyword["Sylveon ex"] == "unavailable"


def test_search_page_no_keyword_match_returns_empty():
    html = "<html><body>Nothing relevant here.</body></html>"
    results = match_search_page(html, ["Elite Trainer Box"], AVAILABLE, UNAVAILABLE)
    assert results == []


def test_search_page_unknown_when_no_status_words_present():
    html = '<div class="product-card"><h3>Elite Trainer Box</h3></div>'
    results = match_search_page(html, ["Elite Trainer Box"], AVAILABLE, UNAVAILABLE)
    assert results[0].status == "unknown"


def test_product_page_available():
    html = "<h1>Sylveon ex Ultra Premium Collection</h1><p>Pre-Order Now</p>"
    result = match_product_page(html, "Sylveon ex UPC", AVAILABLE, UNAVAILABLE)
    assert result.status == "available"


def test_product_page_unavailable():
    html = "<h1>Sylveon ex Ultra Premium Collection</h1><p>Sold Out</p>"
    result = match_product_page(html, "Sylveon ex UPC", AVAILABLE, UNAVAILABLE)
    assert result.status == "unavailable"
