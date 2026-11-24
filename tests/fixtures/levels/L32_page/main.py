"""L32 — bind to a page you do not own, by what the element is."""

from pyodex import ext_page, on_page, start, status


@start(interactive=False)  # no UI of our own: the page must keep receiving clicks
def main():
    ext_page.log.text = "ready"
    status(None)


@on_page("click", any_of=[{"selector": "#nothing-here"}, {"text": "Add to cart"}])
def add_to_cart(payload):
    ext_page.log.text = "added"


@on_page("click", selector="#promo", prevent_default=True)
def promo(payload):
    ext_page.log.text = "promo:" + payload["target"]["href"]


@on_page("click", role="button", name="Buy now")
def buy(payload):
    ext_page.log.text = "bought"
