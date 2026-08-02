"""Store scrapers. ``base`` owns the shared Flipp/Wishabi client (GFP-6); each
Flipp-sourced store module supplies its
:class:`~grocery_planner.scrapers.base.StoreConfig`. ``wholefoods`` (GFP-4) is
a different shape of source (the retailer's own storefront, not a Flipp
flyer) and is plain ``httpx`` against that storefront instead -- see its
module docstring.

``SCRAPERS`` is the registry the CLI dispatches on — adding a store is: create a
thin module (see ``foodlion``/``harristeeter``/``wholefoods``) and list it here.
"""
from . import foodlion, harristeeter, wholefoods

SCRAPERS = {m.STORE_KEY: m for m in (foodlion, harristeeter, wholefoods)}
