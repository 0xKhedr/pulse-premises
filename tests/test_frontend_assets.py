"""Keep LAN page rendering independent of public CDNs and font servers."""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
CSS_URLS = re.compile(r"url\(\s*['\"]?([^'\"\s)]+)['\"]?\s*\)", re.IGNORECASE)
CSS_IMPORTS = re.compile(r"@import\s+['\"]([^'\"]+)['\"]", re.IGNORECASE)


class PageResources(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script" and attrs.get("src"):
            self.urls.append(attrs["src"])
        if tag == "link" and attrs.get("href"):
            rel = set(attrs.get("rel", "").lower().split())
            if rel & {"stylesheet", "manifest", "icon", "apple-touch-icon", "preload", "modulepreload"}:
                self.urls.append(attrs["href"])


@pytest.mark.parametrize("template", sorted((ROOT / "templates").glob("*.html")), ids=lambda path: path.name)
def test_template_resources_do_not_depend_on_external_hosts(template):
    parser = PageResources()
    parser.feed(template.read_text(encoding="utf-8"))
    for url in parser.urls:
        parts = urlsplit(url)
        assert not parts.scheme and not parts.netloc, f"{template.name} requires external resource {url}"
        if parts.path.startswith("/static/") and "{{" not in parts.path:
            assert (ROOT / parts.path.lstrip("/")).is_file(), f"Missing asset: {url}"


@pytest.mark.parametrize("page", ["/", "/admin-login", "/client-login"])
def test_login_resource_graph_is_served_locally_without_authentication(client, page):
    response = client.get(page)
    assert response.status_code == 200
    parser = PageResources()
    parser.feed(response.text)
    pending = list(parser.urls)
    seen = set()

    while pending:
        url = pending.pop()
        if url in seen:
            continue
        seen.add(url)
        parts = urlsplit(url)
        assert not parts.scheme and not parts.netloc, f"External page dependency: {url}"
        response = client.get(url)
        assert response.status_code == 200, f"Cannot serve {url}: {response.status_code}"
        assert response.content, f"Empty asset: {url}"
        content_type = response.headers.get("content-type", "").split(";", 1)[0]

        if parts.path.endswith(".css"):
            assert content_type == "text/css"
            refs = CSS_URLS.findall(response.text) + CSS_IMPORTS.findall(response.text)
            for ref in refs:
                if ref.startswith(("data:", "#")):
                    continue
                assert not urlsplit(ref).scheme and not urlsplit(ref).netloc, f"{url} requires {ref}"
                pending.append(urljoin(url, ref))
        elif parts.path.endswith(".json"):
            manifest = json.loads(response.text)
            pending.extend(urljoin(url, icon["src"]) for icon in manifest.get("icons", []))
        elif parts.path.endswith(".js"):
            assert content_type in {"text/javascript", "application/javascript"}
        elif parts.path.endswith(".woff2"):
            assert content_type == "font/woff2"
            assert response.content.startswith(b"wOF2"), f"Invalid font: {url}"
        elif parts.path.endswith(".svg"):
            assert content_type == "image/svg+xml"

    assert any(url.endswith(".woff2") for url in seen), "The shared fonts were not exercised"
