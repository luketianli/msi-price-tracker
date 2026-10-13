"""Tests for the opt-in fetch-proxy indirection."""

import pytest

from tracker import http


class TestProxyTemplate:
    def test_url_is_substituted_and_encoded(self, monkeypatch):
        monkeypatch.setenv(
            http.PROXY_TEMPLATE_ENV,
            "https://fetch.example/?api_key=K&url={url}",
        )
        out = http._apply_proxy("https://www.microcenter.com/search?Ntt=321UP&x=1")
        assert out.startswith("https://fetch.example/?api_key=K&url=")
        # The inner URL must be percent-encoded, or its own query string would
        # be parsed as the proxy's parameters.
        assert "https%3A%2F%2Fwww.microcenter.com" in out
        assert "&x=1" not in out.split("url=", 1)[1]

    def test_missing_template_is_a_clear_failure(self, monkeypatch):
        monkeypatch.delenv(http.PROXY_TEMPLATE_ENV, raising=False)
        with pytest.raises(http.FetchError, match="is not set"):
            http._apply_proxy("https://example.test")

    def test_template_without_placeholder_is_rejected(self, monkeypatch):
        monkeypatch.setenv(http.PROXY_TEMPLATE_ENV, "https://fetch.example/?key=K")
        with pytest.raises(http.FetchError, match="placeholder"):
            http._apply_proxy("https://example.test")

    def test_proxy_configured_reflects_the_environment(self, monkeypatch):
        monkeypatch.delenv(http.PROXY_TEMPLATE_ENV, raising=False)
        assert http.proxy_configured() is False
        monkeypatch.setenv(http.PROXY_TEMPLATE_ENV, "https://f.example/?url={url}")
        assert http.proxy_configured() is True
        monkeypatch.setenv(http.PROXY_TEMPLATE_ENV, "   ")
        assert http.proxy_configured() is False


class TestFetchRouting:
    def test_direct_by_default(self, monkeypatch):
        seen = {}

        def fake_get(url, **kwargs):
            seen["url"] = url

            class R:
                status_code = 200
                text = "ok"

            return R()

        monkeypatch.setattr(http.requests, "get", fake_get)
        monkeypatch.setenv(http.PROXY_TEMPLATE_ENV, "https://fetch.example/?url={url}")
        http.fetch("https://example.test/page", retries=1)
        assert seen["url"] == "https://example.test/page", "must not proxy unless asked"

    def test_via_proxy_rewrites_the_url(self, monkeypatch):
        seen = {}

        def fake_get(url, **kwargs):
            seen["url"] = url

            class R:
                status_code = 200
                text = "ok"

            return R()

        monkeypatch.setattr(http.requests, "get", fake_get)
        monkeypatch.setenv(http.PROXY_TEMPLATE_ENV, "https://fetch.example/?url={url}")
        http.fetch("https://example.test/page", retries=1, via_proxy=True)
        assert seen["url"] == "https://fetch.example/?url=https%3A%2F%2Fexample.test%2Fpage"

    def test_via_proxy_without_a_template_raises(self, monkeypatch):
        monkeypatch.delenv(http.PROXY_TEMPLATE_ENV, raising=False)
        with pytest.raises(http.FetchError):
            http.fetch("https://example.test", retries=1, via_proxy=True)
