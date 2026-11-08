"""Site files must be revalidated so a deploy is never hidden by a stale browser cache."""

import pytest
from fastapi.testclient import TestClient

import app

client = TestClient(app.app)


@pytest.mark.parametrize("path", ["/styles.css", "/main.js", "/"])
def test_site_files_are_revalidated(path):
    r = client.get(path)
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-cache"
    assert "etag" in r.headers


def test_revalidation_returns_304():
    etag = client.get("/styles.css").headers["etag"]
    assert client.get("/styles.css", headers={"if-none-match": etag}).status_code == 304
