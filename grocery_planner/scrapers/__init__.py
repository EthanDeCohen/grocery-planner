"""Store scrapers. ``base`` owns the shared Flipp/Wishabi client (GFP-6); each
store module supplies its :class:`~grocery_planner.scrapers.base.StoreConfig`.

``SCRAPERS`` is the registry the CLI dispatches on — adding a store is: create a
thin module (see ``foodlion``/``harristeeter``) and list it here.
"""
from . import foodlion, harristeeter

SCRAPERS = {m.STORE_KEY: m for m in (foodlion, harristeeter)}
