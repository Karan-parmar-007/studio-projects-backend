def test_products_are_public(anon_client):
    response = anon_client.get("/api/studio/projects")
    assert response.status_code == 200
    slugs = [item["slug"] for item in response.json()]
    assert "whisperflow" in slugs
    flow = next(item for item in response.json() if item["slug"] == "whisperflow")
    assert flow["page"] == "/whisperflow-clone"
    assert flow["playground"] is True
    assert any(e["path"] == "/api/studio/v1/whisperflow/format" for e in flow["endpoints"])


def test_unknown_product_is_404(anon_client):
    assert anon_client.get("/api/studio/projects/nope").status_code == 404
