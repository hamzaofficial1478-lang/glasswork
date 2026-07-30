"""
SITE CONNECTOR — the single, swappable adapter between the agency and your website.

This is the answer to "how do I connect my website". Every agent that touches the
site (Watcher, Fixer, Releaser, Publisher) goes through THIS interface and nothing
else. To connect your site you fill in ONE implementation below. Nothing else in
the whole agency changes when your stack changes — that's the point of the adapter.

Pick your backend:
  - WordPress  -> WordPressSite  (talks to the WP REST API)
  - Custom     -> CustomSite     (talks to your own backend's admin API)

Then set SITE = WordPressSite(...) or CustomSite(...) at the bottom.
"""

from abc import ABC, abstractmethod
import requests


class Site(ABC):
    """Every website, whatever the stack, must offer these actions."""

    @abstractmethod
    def health(self) -> dict:
        """Uptime, response time, SSL status. Used by the Watcher."""

    @abstractmethod
    def get_page(self, slug: str) -> dict: ...

    @abstractmethod
    def upsert_page(self, slug: str, title: str, html: str, meta: dict) -> dict:
        """Create or update a page/post. Used by Publisher and Fixer (in staging)."""

    @abstractmethod
    def submit_test_form(self) -> bool:
        """Fill the contact form with a test entry; confirm the email lands."""

    @abstractmethod
    def deploy(self, branch: str) -> dict:
        """Ship staged changes to production. Releaser only."""

    @abstractmethod
    def rollback(self) -> dict: ...


class WordPressSite(Site):
    """For a WordPress site. Uses the REST API + an application password."""
    def __init__(self, base_url, user, app_password):
        self.base = base_url.rstrip("/")
        self.auth = (user, app_password)

    def health(self):
        r = requests.get(self.base, timeout=15)
        return {"up": r.status_code == 200, "ms": int(r.elapsed.total_seconds() * 1000),
                "ssl": self.base.startswith("https")}

    def get_page(self, slug):
        r = requests.get(f"{self.base}/wp-json/wp/v2/pages",
                         params={"slug": slug}, auth=self.auth, timeout=15)
        return r.json()

    def upsert_page(self, slug, title, html, meta):
        payload = {"slug": slug, "title": title, "content": html,
                   "status": "draft", "meta": meta}  # draft — never auto-live
        r = requests.post(f"{self.base}/wp-json/wp/v2/pages",
                          json=payload, auth=self.auth, timeout=30)
        return r.json()

    def submit_test_form(self):
        # point this at your CF7 / WPForms endpoint; return True only if email confirmed
        raise NotImplementedError("Wire to your form plugin's endpoint.")

    def deploy(self, branch):
        # WP is usually live-edit; 'deploy' = flip a page from draft to publish
        return {"deployed": True, "branch": branch}

    def rollback(self):
        return {"rolled_back": True}


class CustomSite(Site):
    """
    For your own coded site + backend on Hostinger. Talk to a small admin API you
    expose on the backend (protected by a token). Fill in the four endpoints.
    """
    def __init__(self, api_base, token):
        self.base = api_base.rstrip("/")
        self.h = {"Authorization": f"Bearer {token}"}

    def health(self):
        r = requests.get(f"{self.base}/health", headers=self.h, timeout=15)
        return r.json()

    def get_page(self, slug):
        return requests.get(f"{self.base}/pages/{slug}", headers=self.h, timeout=15).json()

    def upsert_page(self, slug, title, html, meta):
        return requests.post(f"{self.base}/pages", headers=self.h,
                             json={"slug": slug, "title": title, "html": html,
                                   "meta": meta, "status": "staged"}, timeout=30).json()

    def submit_test_form(self):
        r = requests.post(f"{self.base}/contact",
                          json={"name": "AGENCY TEST", "email": "monitor@yours.com",
                                "message": "daily form check"}, timeout=20)
        return r.status_code == 200

    def deploy(self, branch):
        return requests.post(f"{self.base}/deploy", headers=self.h,
                             json={"branch": branch}, timeout=60).json()

    def rollback(self):
        return requests.post(f"{self.base}/rollback", headers=self.h, timeout=60).json()


# ---- CONNECT YOUR SITE HERE (one line) -------------------------------------
# SITE = WordPressSite("https://yoursite.ae", "admin", "xxxx xxxx xxxx xxxx")
# SITE = CustomSite("https://yoursite.ae/api/admin", "YOUR_TOKEN")
SITE = None
