"""API tests for /ingest input validation (these paths never touch the database)."""


async def test_rejects_path_outside_ingest_root(client):
    resp = await client.post("/api/v1/ingest", json={"file_path": "/etc/passwd"})
    assert resp.status_code == 403


async def test_rejects_path_traversal(client):
    resp = await client.post("/api/v1/ingest", json={"file_path": "data/../.env"})
    assert resp.status_code == 403


async def test_missing_file_is_404(client):
    resp = await client.post("/api/v1/ingest", json={"file_path": "data/nope.log"})
    assert resp.status_code == 404


async def test_empty_body_is_422(client):
    resp = await client.post("/api/v1/ingest", json={})
    assert resp.status_code == 422
