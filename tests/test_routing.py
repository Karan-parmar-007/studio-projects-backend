from app.services.routing import LinkRow, candidates, explain


def _links() -> list[LinkRow]:
    return [
        LinkRow("llm", "local", "http://pc/llm", True),
        LinkRow("llm", "remote", "http://server/llm", True),
        LinkRow("speech", "local", "", True),
        LinkRow("speech", "remote", "http://server/speech", True),
    ]


def test_local_formatter_wins_when_it_is_ready():
    chosen = candidates(_links(), "llm")
    assert [item.placement for item in chosen] == ["local", "remote"]
    assert chosen[0].url == "http://pc/llm"


def test_formatter_skips_a_local_link_whose_toggle_is_off():
    links = [
        LinkRow("llm", "local", "http://pc/llm", False),
        LinkRow("llm", "remote", "http://server/llm", True),
    ]
    chosen = candidates(links, "llm")
    assert [item.placement for item in chosen] == ["remote"]


def test_speech_never_uses_the_server_link():
    links = [
        LinkRow("speech", "local", "", True),
        LinkRow("speech", "remote", "http://server/speech", True),
    ]
    assert candidates(links, "speech") == []
    states = {item["placement"]: item["state"] for item in explain(links, "speech")}
    assert states["local"] == "no_link"
    assert states["remote"] == "ready"


def test_empty_url_is_not_called_even_if_the_toggle_is_on():
    links = [LinkRow("llm", "local", "  ", True), LinkRow("llm", "remote", "", False)]
    assert candidates(links, "llm") == []
