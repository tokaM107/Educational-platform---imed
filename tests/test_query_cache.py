"""The question side of the embeddings is cached, like the transcript side."""

from types import SimpleNamespace

from app.services import query_cache


class FakeEmbedder:
    """Records every call, so a cache hit is provable."""

    def __init__(self):
        self.calls = []
        self.settings = SimpleNamespace(embed_model="test-model", embed_dim=4)

    def embed_query(self, text):
        self.calls.append(text)
        return [0.1, 0.2, 0.3, 0.4]


def use_dict_store(monkeypatch):
    """Swap the two SQL helpers for an in-memory dict."""

    store = {}

    monkeypatch.setattr(
        query_cache,
        "get",
        lambda conn, question, model, dim:
            store.get((query_cache.cache_key(question), model, dim)),
    )
    monkeypatch.setattr(
        query_cache,
        "put",
        lambda conn, question, model, dim, embedding:
            store.__setitem__(
                (query_cache.cache_key(question), model, dim), embedding
            ),
    )

    return store


def test_repeated_question_is_not_embedded_twice(monkeypatch):

    use_dict_store(monkeypatch)
    embedder = FakeEmbedder()

    first = query_cache.embed_query(None, embedder, "امتى بيتكون الانحناء العنقي؟")
    second = query_cache.embed_query(None, embedder, "امتى بيتكون الانحناء العنقي؟")

    assert embedder.calls == ["امتى بيتكون الانحناء العنقي؟"]
    assert first == second


def test_whitespace_differences_still_hit_the_cache(monkeypatch):

    use_dict_store(monkeypatch)
    embedder = FakeEmbedder()

    query_cache.embed_query(None, embedder, "سؤال عن العظام")
    query_cache.embed_query(None, embedder, "  سؤال   عن\nالعظام ")

    assert len(embedder.calls) == 1


def test_different_questions_are_embedded_separately(monkeypatch):

    use_dict_store(monkeypatch)
    embedder = FakeEmbedder()

    query_cache.embed_query(None, embedder, "سؤال أول")
    query_cache.embed_query(None, embedder, "سؤال تاني")

    assert len(embedder.calls) == 2


def test_key_is_scoped_to_model_and_dimension(monkeypatch):
    """Vectors from another model must never be reused as if they matched."""

    store = use_dict_store(monkeypatch)

    embedder = FakeEmbedder()
    query_cache.embed_query(None, embedder, "سؤال")

    other = FakeEmbedder()
    other.settings = SimpleNamespace(embed_model="another-model", embed_dim=4)
    query_cache.embed_query(None, other, "سؤال")

    assert len(store) == 2
    assert other.calls == ["سؤال"]


def test_normalise_and_key_are_stable():

    assert query_cache.normalise("  a   b \n c ") == "a b c"
    assert query_cache.cache_key("a b") == query_cache.cache_key(" a  b ")
    assert query_cache.cache_key("a b") != query_cache.cache_key("a c")
