"""Manual explanation script resource ownership and metadata substitutions."""

from unittest.mock import AsyncMock, Mock

import pytest

from gamerec.schemas.explanation import SearchExplanation, SearchExplanationContext
from gamerec.scripts import verify_search_explanations as script
from gamerec.services.search_explanation import SearchExplanationGeneration


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "metadata", "inference"])
async def test_shared_client_and_database_cleanup(monkeypatch, failure, capsys):
    context = SearchExplanationContext(
        steam_app_id=10,
        name="Stored game",
        search_query="games",
        description="Stored facts",
    )
    getter = AsyncMock(return_value=context)
    generate = AsyncMock(
        return_value=SearchExplanationGeneration(
            SearchExplanation(explanation="Grounded explanation", matching_features=[]),
            None,
        )
    )
    if failure == "metadata":
        getter.side_effect = RuntimeError("metadata failed")
    if failure == "inference":
        generate.side_effect = RuntimeError("inference failed")
    session = AsyncMock()
    client = AsyncMock()
    client.__aenter__.return_value = client
    factory = Mock(return_value=client)
    dispose = AsyncMock()
    monkeypatch.setattr(script, "SessionLocal", lambda: session)
    monkeypatch.setattr(script, "create_ollama_client", factory)
    monkeypatch.setattr(script, "get_search_explanation_context", getter)
    monkeypatch.setattr(script, "generate_search_explanation", generate)
    monkeypatch.setattr(script, "engine", Mock(dispose=dispose))

    if failure:
        with pytest.raises(RuntimeError, match=f"{failure} failed"):
            await script.main()
    else:
        await script.main()
        assert generate.await_count == 3
        assert all(call.args[1] is client for call in generate.await_args_list)
        output = capsys.readouterr().out
        assert '"matching_features": []' in output
        assert '"limitations"' not in output
        assert "LLM metadata input:" in output
    session.__aexit__.assert_awaited_once()
    dispose.assert_awaited_once_with()
    if failure != "metadata":
        factory.assert_called_once_with()
        client.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
async def test_substitution_is_reported_when_preferred_metadata_is_insufficient(
    monkeypatch, capsys
):
    getter = AsyncMock(
        side_effect=[
            SearchExplanationContext(
                steam_app_id=108600, name="Preferred", search_query="games"
            ),
            SearchExplanationContext(
                steam_app_id=892970,
                name="Alternate",
                search_query="games",
                description="Stored facts",
            ),
        ]
    )
    session = AsyncMock()
    client = AsyncMock()
    generate = AsyncMock(
        return_value=SearchExplanationGeneration(
            SearchExplanation(explanation="Grounded explanation", matching_features=[]),
            None,
        )
    )
    monkeypatch.setattr(script, "EXAMPLES", (((108600, 892970), "games"),))
    monkeypatch.setattr(script, "SessionLocal", lambda: session)
    monkeypatch.setattr(script, "create_ollama_client", lambda: client)
    monkeypatch.setattr(script, "get_search_explanation_context", getter)
    monkeypatch.setattr(script, "generate_search_explanation", generate)
    monkeypatch.setattr(script, "engine", Mock(dispose=AsyncMock()))
    await script.main()
    assert "Substitution: 108600" in capsys.readouterr().out
    assert [call.args[1] for call in getter.await_args_list] == [108600, 892970]
    assert generate.call_args.args[0].steam_app_id == 892970
