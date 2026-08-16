"""Chunking is pure logic — no database, no API key needed to test it."""

from rag import chunking


TRANSCRIPT = """
===== chunk_000.wav | 00:00:00 --> 00:05:00 =====

{first}

===== chunk_001.wav | 00:05:00 --> 00:10:00 =====

{second}
""".format(
    first=" ".join(f"a{i}" for i in range(300)),
    second=" ".join(f"b{i}" for i in range(300)),
)


def build():
    blocks = chunking.parse_blocks(TRANSCRIPT)
    return blocks, chunking.chunk_transcript(blocks, chunk_words=120, overlap_words=25)


def test_blocks_carry_their_time_range():

    blocks, _ = build()

    assert [block.source for block in blocks] == ["chunk_000.wav", "chunk_001.wav"]
    assert (blocks[0].start_ts, blocks[0].end_ts) == (0, 300)
    assert (blocks[1].start_ts, blocks[1].end_ts) == (300, 600)


def test_chunks_stay_inside_their_block():
    """A chunk must never span two ASR blocks, or its timestamps would lie."""

    _, chunks = build()

    for chunk in chunks:

        words = chunk.text.split()
        prefixes = {word[0] for word in words}

        assert len(prefixes) == 1, "chunk mixes text from two blocks"

        if prefixes == {"a"}:
            assert 0 <= chunk.start_ts < chunk.end_ts <= 300
        else:
            assert 300 <= chunk.start_ts < chunk.end_ts <= 600


def test_windows_overlap_and_cover_everything():

    _, chunks = build()

    first_block = [chunk for chunk in chunks if chunk.text.startswith("a")]

    # Every word of the block survives somewhere
    seen = set()
    for chunk in first_block:
        seen.update(chunk.text.split())

    assert len(seen) == 300

    # Consecutive windows share their overlap
    for earlier, later in zip(first_block, first_block[1:]):
        assert set(earlier.text.split()) & set(later.text.split())


def test_short_tail_is_merged_not_emitted():

    blocks = chunking.parse_blocks(
        "===== chunk_000.wav | 00:00:00 --> 00:05:00 =====\n\n"
        + " ".join(f"w{i}" for i in range(130))
    )

    chunks = chunking.chunk_block(blocks[0], chunk_words=120, overlap_words=25)

    assert len(chunks) == 1
    assert len(chunks[0].text.split()) == 130


def test_cut_snaps_to_a_sentence_end_when_one_is_near():

    words = ["w"] * 200
    words[110] = "w."

    blocks = chunking.parse_blocks(
        "===== chunk_000.wav | 00:00:00 --> 00:05:00 =====\n\n" + " ".join(words)
    )

    chunks = chunking.chunk_block(blocks[0], chunk_words=120, overlap_words=25)

    assert chunks[0].text.endswith("w.")


def test_stamp_round_trip():

    assert chunking.to_stamp(4482) == "01:14:42"
    assert chunking.to_seconds("01:14:42") == 4482
