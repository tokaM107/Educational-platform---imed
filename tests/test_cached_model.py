"""Finding the RunPod Cached Model on the volume.

The worker downloads nothing: the weights are mounted by RunPod under
/runpod-volume/huggingface-cache/hub in the standard hub layout, and this
resolves the snapshot directory to hand `from_pretrained`. Getting it wrong is
expensive in a way that is easy to miss -- a resolver that returns the model id
instead of a path turns into a hub download on a worker with no token, mid-job,
with a GPU already billing -- so the failure modes are pinned here rather than
discovered on the endpoint.

No GPU, no network, no /runpod-volume: the cache is built in tmp_path.
"""

import pytest

from rag import transcribe_cohere


CHECKPOINT = "CohereLabs/cohere-transcribe-arabic-07-2026"

REPO_DIR = "models--CohereLabs--cohere-transcribe-arabic-07-2026"


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    """No inherited HF or RunPod settings leaking into a test."""

    for name in (
        "COHERE_TRANSCRIBE_MODEL",
        "COHERE_TRANSCRIBE_MODEL_PATH",
        "RUNPOD_MODEL_CACHE",
        "HF_HUB_OFFLINE",
        "TRANSFORMERS_OFFLINE",
    ):
        monkeypatch.delenv(name, raising=False)


def make_snapshot(root, revision, repo=REPO_DIR, marker=True):
    """One snapshot directory in the hub cache layout."""

    path = root / repo / "snapshots" / revision
    path.mkdir(parents=True)

    if marker:
        (path / "config.json").write_text("{}", encoding="utf-8")

    return path


def write_ref(root, revision, repo=REPO_DIR):
    ref = root / repo / "refs" / "main"
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_text(revision, encoding="utf-8")

    return ref


def use_cache(monkeypatch, root):
    monkeypatch.setenv("RUNPOD_MODEL_CACHE", str(root))


def go_offline(monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")


def test_the_repo_directory_follows_the_hub_naming(): 
    assert transcribe_cohere.repo_dir_name(CHECKPOINT) == REPO_DIR


def test_refs_main_is_preferred_over_any_other_snapshot(tmp_path, monkeypatch):
    """Two snapshots on the volume, and refs/main says which was asked for."""

    make_snapshot(tmp_path, "aaaa1111")
    wanted = make_snapshot(tmp_path, "bbbb2222")
    write_ref(tmp_path, "bbbb2222")

    use_cache(monkeypatch, tmp_path)

    assert transcribe_cohere.cached_snapshot(CHECKPOINT) == wanted


def test_a_snapshot_is_found_without_refs_main(tmp_path, monkeypatch):
    """A mount that never wrote refs/main still has a usable model."""

    only = make_snapshot(tmp_path, "aaaa1111")
    use_cache(monkeypatch, tmp_path)

    assert transcribe_cohere.cached_snapshot(CHECKPOINT) == only


def test_a_dangling_refs_main_falls_back_to_a_real_snapshot(tmp_path, monkeypatch):
    """refs/main naming a revision that is not on the volume must not win."""

    real = make_snapshot(tmp_path, "aaaa1111")
    write_ref(tmp_path, "cccc3333")

    use_cache(monkeypatch, tmp_path)

    assert transcribe_cohere.cached_snapshot(CHECKPOINT) == real


def test_an_empty_snapshot_directory_is_not_a_model(tmp_path, monkeypatch):
    """A partial mount is worse than none: it fails deep inside transformers."""

    make_snapshot(tmp_path, "aaaa1111", marker=False)
    use_cache(monkeypatch, tmp_path)

    assert transcribe_cohere.cached_snapshot(CHECKPOINT) is None


def test_refs_main_pointing_at_an_empty_snapshot_falls_back(tmp_path, monkeypatch):
    real = make_snapshot(tmp_path, "aaaa1111")
    make_snapshot(tmp_path, "bbbb2222", marker=False)
    write_ref(tmp_path, "bbbb2222")

    use_cache(monkeypatch, tmp_path)

    assert transcribe_cohere.cached_snapshot(CHECKPOINT) == real


def test_the_newest_snapshot_wins_when_there_is_no_ref(tmp_path, monkeypatch):
    import os

    old = make_snapshot(tmp_path, "aaaa1111")
    new = make_snapshot(tmp_path, "bbbb2222")

    os.utime(old, (1_000_000, 1_000_000))
    os.utime(new, (2_000_000, 2_000_000))

    use_cache(monkeypatch, tmp_path)

    assert transcribe_cohere.cached_snapshot(CHECKPOINT) == new


def test_an_empty_cache_resolves_to_nothing(tmp_path, monkeypatch):
    use_cache(monkeypatch, tmp_path)

    assert transcribe_cohere.cached_snapshot(CHECKPOINT) is None


def test_another_model_in_the_cache_is_not_mistaken_for_this_one(
    tmp_path, monkeypatch
):
    make_snapshot(tmp_path, "aaaa1111", repo="models--openai--whisper-large-v3")
    use_cache(monkeypatch, tmp_path)

    assert transcribe_cohere.cached_snapshot(CHECKPOINT) is None


def test_resolve_returns_the_local_path_not_the_model_id(tmp_path, monkeypatch):
    """The whole point: from_pretrained must get a directory, never a repo id."""

    wanted = make_snapshot(tmp_path, "aaaa1111")
    write_ref(tmp_path, "aaaa1111")

    use_cache(monkeypatch, tmp_path)
    go_offline(monkeypatch)

    assert transcribe_cohere.resolve_checkpoint() == str(wanted)


def test_offline_with_no_cached_model_fails_with_an_actionable_message(
    tmp_path, monkeypatch
):
    """The worker must say what is missing and where, not fail at a download."""

    use_cache(monkeypatch, tmp_path)
    go_offline(monkeypatch)

    with pytest.raises(transcribe_cohere.ModelNotCached) as caught:
        transcribe_cohere.resolve_checkpoint()

    message = str(caught.value)

    assert CHECKPOINT in message
    assert REPO_DIR in message
    assert "Cached Model" in message


def test_an_explicit_path_overrides_the_cache(tmp_path, monkeypatch):
    make_snapshot(tmp_path, "aaaa1111")
    write_ref(tmp_path, "aaaa1111")
    use_cache(monkeypatch, tmp_path)

    override = make_snapshot(tmp_path / "elsewhere", "dddd4444")
    monkeypatch.setenv("COHERE_TRANSCRIBE_MODEL_PATH", str(override))

    assert transcribe_cohere.resolve_checkpoint() == str(override)


def test_an_explicit_path_that_is_not_a_model_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("COHERE_TRANSCRIBE_MODEL_PATH", str(tmp_path / "nowhere"))

    with pytest.raises(transcribe_cohere.ModelNotCached):
        transcribe_cohere.resolve_checkpoint()


def test_the_workstation_path_still_uses_the_hub(tmp_path, monkeypatch):
    """Not offline and no volume: `python -m rag.transcribe_cohere` as before."""

    use_cache(monkeypatch, tmp_path)

    assert transcribe_cohere.resolve_checkpoint() == CHECKPOINT


def test_the_configured_model_id_drives_the_lookup(tmp_path, monkeypatch):
    """COHERE_TRANSCRIBE_MODEL must move the cache directory that is searched."""

    make_snapshot(tmp_path, "aaaa1111", repo="models--acme--other-asr")
    write_ref(tmp_path, "aaaa1111", repo="models--acme--other-asr")

    use_cache(monkeypatch, tmp_path)
    monkeypatch.setenv("COHERE_TRANSCRIBE_MODEL", "acme/other-asr")
    go_offline(monkeypatch)

    resolved = transcribe_cohere.resolve_checkpoint()

    assert resolved.endswith("models--acme--other-asr/snapshots/aaaa1111")


@pytest.mark.parametrize(
    "offline_var", ["HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"]
)
def test_either_offline_flag_is_enough(tmp_path, monkeypatch, offline_var):
    use_cache(monkeypatch, tmp_path)
    monkeypatch.setenv(offline_var, "1")

    with pytest.raises(transcribe_cohere.ModelNotCached):
        transcribe_cohere.resolve_checkpoint()
