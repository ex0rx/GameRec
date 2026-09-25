from gamerec.models.game import Game


def build_game_embedding_text(game: Game) -> str:
    parts: list[str] = []

    if game.name.strip():
        parts.append(f"Title: {game.name.strip()}")

    if game.short_description and game.short_description.strip():
        parts.append(f"Description: {game.short_description.strip()}")

    if game.genres:
        parts.append(f"Genres: {', '.join(game.genres)}")

    if game.categories:
        parts.append(f"Categories: {', '.join(game.categories)}")

    return "\n".join(parts)
