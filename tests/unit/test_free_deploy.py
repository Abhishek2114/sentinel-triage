"""Settings and headers for the single-container deploy (Render or HF Space + Neon + Upstash)."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings, asyncpg_url, celery_redis_url
from app.core.security import _CSP, framing_headers


class TestDatabaseUrl:
    def test_neon_url_becomes_asyncpg_with_ssl(self):
        url = "postgresql://owner:pw@ep-x.us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
        assert asyncpg_url(url) == (
            "postgresql+asyncpg://owner:pw@ep-x.us-east-1.aws.neon.tech/neondb?ssl=require"
        )

    def test_postgres_scheme_alias(self):
        assert asyncpg_url("postgres://u:p@h:5432/d") == "postgresql+asyncpg://u:p@h:5432/d"

    def test_other_params_are_kept(self):
        url = "postgresql+asyncpg://u:p@/d?host=/tmp/sock&sslmode=disable"
        assert asyncpg_url(url) == "postgresql+asyncpg://u:p@/d?host=%2Ftmp%2Fsock&ssl=disable"

    def test_non_postgres_urls_are_untouched(self):
        assert asyncpg_url("sqlite+aiosqlite:///x.db?sslmode=require") == (
            "sqlite+aiosqlite:///x.db?sslmode=require"
        )

    def test_settings_apply_it(self):
        s = Settings(database_url="postgres://u:p@h/d?sslmode=require")
        assert s.database_url == "postgresql+asyncpg://u:p@h/d?ssl=require"


class TestCeleryRedisUrl:
    def test_tls_url_gets_certificate_checking(self):
        assert celery_redis_url("rediss://default:pw@x.upstash.io:6379") == (
            "rediss://default:pw@x.upstash.io:6379?ssl_cert_reqs=CERT_REQUIRED"
        )

    def test_existing_choice_and_plain_urls_are_kept(self):
        for url in ("rediss://h:6379/0?ssl_cert_reqs=CERT_NONE", "redis://localhost:6379/1"):
            assert celery_redis_url(url) == url


class TestFraming:
    def test_nobody_may_frame_by_default(self):
        headers = dict(framing_headers(Settings(), _CSP))
        assert b"frame-ancestors 'none'" in headers[b"content-security-policy"]
        assert headers[b"x-frame-options"] == b"DENY"

    def test_named_origins_may_frame(self):
        s = Settings(frame_ancestors=["https://huggingface.co"])
        headers = dict(framing_headers(s, _CSP))
        csp = headers[b"content-security-policy"].decode()
        assert "frame-ancestors 'self' https://huggingface.co;" in csp
        # X-Frame-Options can't name an origin, so it would block the Space page
        assert b"x-frame-options" not in headers

    @pytest.mark.parametrize(
        "origin",
        [
            "*",
            "http://huggingface.co",
            "https://a.com; script-src *",
            "https://a.com/path",
            "'self'",
        ],
    )
    def test_only_plain_https_origins_are_accepted(self, origin):
        # The value goes verbatim into the CSP header
        with pytest.raises(ValidationError):
            Settings(frame_ancestors=[origin])
