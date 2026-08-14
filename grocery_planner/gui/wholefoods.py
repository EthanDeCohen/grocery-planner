# ######### decohen-partners ##########
# Protein Ledger
"""Mint a Whole Foods session inside the app (GFP-80).

Turning Whole Foods on used to mean opening browser DevTools, finding a cookie
named ``wfm_store_d8``, and pasting its raw value into a JSON file. A
nutritionist will not do that, which made the best data source this product has
(GFP-70/GFP-4) unreachable for a real customer.

This shows them the actual Whole Foods store picker in an embedded browser, lets
them choose their store the way they would in Chrome, and reads the cookie out
of the app's OWN browser profile. No DevTools, no copy-paste, and JavaScript
runs -- so the ``x-amzn-csrf`` bootstrap that defeated the plain-HTTP approach
(GFP-70) solves itself.

WHY THIS IS ALLOWED TO BE HERE AT ALL. The user's rule that customers must
never handle their own credentials turned out to be KROGER-ONLY (GFP-101). For
Whole Foods they explicitly want each customer to mint their own -- which is
also the only thing that can work, since the cookie pins one physical store and
therefore cannot be minted centrally and shipped.

THE COST, decided knowingly and recorded so it is not rediscovered as a
surprise: Qt WebEngine takes ``gplan-gui`` from 50.6 MB to 180.1 MB. Measured,
3.56x, on every download and every update.

WHY EVERY WEBENGINE IMPORT IN THIS FILE IS DEFERRED
---------------------------------------------------
Importing WebEngine costs real memory and startup time, and the overwhelming
majority of launches never open this window. So nothing here is imported at
module scope, and ``gui/app.py`` imports this module only when the menu item is
triggered.

There is one exception that CANNOT be deferred, and it lives in ``app.py``
rather than here: Qt requires ``QtWebEngineCore`` to be imported BEFORE the
``QApplication`` is constructed. Deferring that one would make the window fail
to open at the moment a user asked for it, which is the worst possible time to
find out. See ``app.py``'s ``main()``.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
)

from ..scrapers import wholefoods

#: Where the mint starts. The store finder rather than the home page: the whole
#: point is to reach the store picker, and starting a step earlier is a step
#: the user has to work out for themselves.
STORE_FINDER_URL = "https://www.wholefoodsmarket.com/stores"

#: The one cookie worth having. Everything else a browsing session accumulates
#: is deliberately discarded -- see wholefoods.save_session.
COOKIE_NAME = b"wfm_store_d8"


class WebEngineUnavailable(RuntimeError):
    """This build has no Qt WebEngine in it.

    Possible for a CLI-only or size-trimmed build, and it must be a clean
    message rather than an ImportError traceback in front of a nutritionist.
    """


def webengine_available() -> bool:
    """Can this build show the minting window at all?"""
    try:
        import PySide6.QtWebEngineWidgets  # noqa: F401
    except ImportError:
        return False
    return True


class MintSessionDialog(QDialog):
    """The embedded store picker, and the cookie it produces.

    Takes a ZIP because the cookie is pinned to one store and therefore to one
    ZIP (GFP-83). A mint that did not know which ZIP it was for could not tell
    the user they had picked the wrong store -- which is precisely the mistake
    this replaces DevTools to avoid, since it produces prices that are real but
    for somewhere else.
    """

    def __init__(self, postal_code: str, parent=None) -> None:
        super().__init__(parent)
        if not webengine_available():
            raise WebEngineUnavailable(
                "This build of Grocery Planner does not include the embedded "
                "browser needed to connect Whole Foods."
            )

        from PySide6.QtWebEngineCore import QWebEngineProfile
        from PySide6.QtWebEngineWidgets import QWebEngineView

        self.postal_code = str(postal_code).strip()
        self.cookie_value: str | None = None
        self.saved_to = None

        self.setWindowTitle("Connect Whole Foods")
        self.resize(980, 760)

        self.explain = QLabel(
            f"Choose your Whole Foods store for {self.postal_code}. "
            "When you have set it as your store, this window will finish by "
            "itself."
        )
        self.explain.setWordWrap(True)

        self.status = QLabel("Waiting for you to choose a store…")
        self.status.setWordWrap(True)

        # An OFF-THE-RECORD profile: nothing is written to disk by the browser
        # itself. The only thing that persists is the single cookie we
        # deliberately save, which keeps a customer's browsing out of the
        # application's data directory entirely.
        self._profile = QWebEngineProfile(self)
        self._profile.cookieStore().cookieAdded.connect(self._on_cookie_added)

        from PySide6.QtWebEngineCore import QWebEnginePage

        self.view = QWebEngineView(self)
        self.view.setPage(QWebEnginePage(self._profile, self.view))
        self.view.load(QUrl(STORE_FINDER_URL))

        self.buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self.explain)
        layout.addWidget(self.view, stretch=1)
        row = QHBoxLayout()
        row.addWidget(self.status, stretch=1)
        row.addWidget(self.buttons)
        layout.addLayout(row)

    # ------------------------------------------------------------------ #
    def _on_cookie_added(self, cookie) -> None:
        """Called for every cookie the embedded browser receives.

        A Whole Foods page sets a great many. This ignores all of them except
        one, and does not store even that until it has been decoded and its ZIP
        checked -- so a user who picks the wrong store is told so here, while
        the picker is still open and the mistake is one click from being fixed.
        """
        if bytes(cookie.name()) != COOKIE_NAME:
            return
        value = bytes(cookie.value()).decode("utf-8", errors="replace")
        self._consider(value)

    def _consider(self, value: str) -> None:
        """Validate a candidate cookie and finish if it is the right one."""
        try:
            data = wholefoods._decode_store_cookie(value)
        except wholefoods.SessionExpiredError:
            # Whole Foods sets this cookie early with a placeholder before a
            # store is chosen. Not an error -- just not finished yet.
            self.status.setText("Waiting for you to choose a store…")
            return

        cookie_zip = str(data.get("deliveryZip") or "").strip()
        if self.postal_code and cookie_zip and cookie_zip != self.postal_code:
            # The commonest real mistake, and the one that produces prices that
            # are correct for somewhere the client does not live.
            self.status.setText(
                f"That store delivers to {cookie_zip}, not {self.postal_code}. "
                "Choose a store for your ZIP, or close this and change your ZIP "
                "in Settings."
            )
            return
        if not cookie_zip:
            self.status.setText("Waiting for you to choose a store…")
            return

        self.cookie_value = value
        self.status.setText(f"Connected to the {cookie_zip} store. Saving…")
        self._save()

    def _save(self) -> None:
        try:
            self.saved_to = wholefoods.save_session(
                self.cookie_value, postal_code=self.postal_code
            )
        except Exception as exc:                # noqa: BLE001
            # save_session validates again and can still refuse. Report it here
            # rather than closing on a failure the user would never see.
            self.status.setText(f"Could not save the session: {exc}")
            return
        self.accept()


def mint(postal_code: str, parent=None) -> "MintSessionDialog | None":
    """Open the minting window. Returns the dialog if it saved, else None."""
    dialog = MintSessionDialog(postal_code, parent=parent)
    if dialog.exec() == QDialog.Accepted:
        return dialog
    return None
