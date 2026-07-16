from pokemon_preorder_bot.matcher import match_product_page, match_search_page

PREORDER = ["pre-order", "vorbestellen", "予約する"]
IN_STOCK = ["add to cart", "in den warenkorb", "カートに入れる"]
UNAVAILABLE = ["sold out", "coming soon", "notify me", "ausverkauft", "売り切れ"]
PRODUCT_KEYWORDS = ["Elite Trainer Box", "Ultra Premium Collection", "Sylveon ex", "Greninja ex"]


def test_search_page_separates_adjacent_product_cards():
    html = """
    <html><body>
    <div class="product-card">
      <h3>Pokemon TCG 30th Celebration Elite Trainer Box</h3>
      <span>$49.99</span><button>Add to Cart</button>
    </div>
    <div class="product-card">
      <h3>30th Celebration Sylveon ex Box</h3>
      <span>$24.99</span><span>Coming Soon - Notify Me</span>
    </div>
    </body></html>
    """
    results = match_search_page(html, ["30th Celebration"], PRODUCT_KEYWORDS, PREORDER, IN_STOCK, UNAVAILABLE)
    statuses = [r.status for r in results]
    assert "in_stock" in statuses
    assert "unavailable" in statuses


def test_search_page_filters_out_unrelated_product_with_same_set_keyword():
    """A page mentioning '30th Celebration'-adjacent but non-TCG merch (e.g.
    the separate Pokémon Day collection or plush) shouldn't count as a hit
    when product_keywords are required."""
    html = """
    <div class="product-card">
      <h3>Pokémon 30th Celebration Anniversary Plush Pikachu</h3>
      <span>Add to Cart</span>
    </div>
    """
    results = match_search_page(html, ["30th Celebration"], PRODUCT_KEYWORDS, PREORDER, IN_STOCK, UNAVAILABLE)
    assert results == []


def test_search_page_preorder_wins_over_generic_buy_signal():
    html = '<div class="product-card">30th Celebration Ultra Premium Collection - Pre-Order - Add to Cart</div>'
    results = match_search_page(html, ["30th Celebration"], PRODUCT_KEYWORDS, PREORDER, IN_STOCK, UNAVAILABLE)
    assert results[0].status == "preorder"


def test_search_page_no_keyword_match_returns_empty():
    html = "<html><body>Nothing relevant here.</body></html>"
    results = match_search_page(html, ["30th Celebration"], PRODUCT_KEYWORDS, PREORDER, IN_STOCK, UNAVAILABLE)
    assert results == []


def test_search_page_no_product_keywords_required_matches_on_set_keyword_alone():
    html = '<div class="product-card">30th Celebration - Add to Cart</div>'
    results = match_search_page(html, ["30th Celebration"], [], PREORDER, IN_STOCK, UNAVAILABLE)
    assert results[0].status == "in_stock"


def test_product_page_preorder():
    html = "<h1>Sylveon ex Ultra Premium Collection</h1><p>Pre-Order Now</p>"
    result = match_product_page(html, "Sylveon ex UPC", PREORDER, IN_STOCK, UNAVAILABLE)
    assert result.status == "preorder"


def test_product_page_unavailable():
    html = "<h1>Sylveon ex Ultra Premium Collection</h1><p>Sold Out</p>"
    result = match_product_page(html, "Sylveon ex UPC", PREORDER, IN_STOCK, UNAVAILABLE)
    assert result.status == "unavailable"
