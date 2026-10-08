from math import sqrt

from gamerec.ml.embedding_model import embed_search_query, load_embedding_model


def main() -> None:
    model = load_embedding_model()
    queries = (
        "Open-world RPG with character progression",
        "Cooperative survival game with crafting",
        "Fast-paced first-person shooter",
    )
    for query in queries:
        embedding = embed_search_query(query, model)
        print(f"Query: {query}")
        print(f"Dimensions: {len(embedding)}")
        print(f"L2 norm: {sqrt(sum(value * value for value in embedding)):.6f}")


if __name__ == "__main__":
    main()
