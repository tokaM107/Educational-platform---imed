"""Arabic ASR over the audio chunks, with a local Cohere model.

Kept as the transcription step rather than replaced by Bunny's own Transcribe
AI: Bunny's is Whisper, and this is a model trained for Arabic. On an Egyptian
lecture the difference is the difference between a transcript worth retrieving
over and one that is not.

Writes the format rag/chunking.py parses, and that is a contract:

    ===== chunk_000.wav | 00:00:00 --> 00:05:00 =====

The header is where every timestamp in the application ultimately comes from —
the start second a citation seeks the player to is read back out of it.

The model is loaded on first use, not at import, so that `from rag import
transcribe_cohere` in a test or a CLI that only wants `timestamp()` does not
pull several gigabytes off Hugging Face.
"""

import argparse
import os
import time
from functools import lru_cache
from pathlib import Path

from rag.audio import CHUNK_SECONDS, chunks_from_dir


MODEL_ID = "CohereLabs/cohere-transcribe-arabic-07-2026"

SAMPLE_RATE = 16000

DEFAULT_CHUNKS_DIR = "data/audios/chunks"
DEFAULT_OUTPUT = "data/transcripts/transcript.txt"

# Where RunPod mounts a Cached Model. The endpoint is configured with the model
# and RunPod's own Hugging Face token, and the weights arrive on the volume --
# so this image ships no token, downloads nothing at build, and downloads
# nothing at runtime. The layout is the standard Hugging Face cache:
#
#   /runpod-volume/huggingface-cache/hub/
#       models--CohereLabs--cohere-transcribe-arabic-07-2026/
#           refs/main                  -> a revision hash
#           snapshots/<hash>/          -> config.json, weights, processor
#
RUNPOD_MODEL_CACHE = "/runpod-volume/huggingface-cache/hub"

# A snapshot directory that exists but holds no config.json is not a model --
# it is an interrupted or partial mount, and loading from it fails deep inside
# transformers with something that does not mention the cache at all.
SNAPSHOT_MARKER = "config.json"


class ModelNotCached(RuntimeError):
    """The worker is offline and the cached model is not on the volume.

    Separate from every other load failure because it is the one that will
    never fix itself by retrying: the endpoint's Cached Model configuration has
    to change. Nothing about a restart makes the weights appear.
    """


def model_id():
    """Which checkpoint to load. Env first so the GPU image needs no rebuild."""

    return os.getenv("COHERE_TRANSCRIBE_MODEL", "").strip() or MODEL_ID


def cache_root():
    """The hub cache to look in. Overridable so tests need no /runpod-volume."""

    return Path(os.getenv("RUNPOD_MODEL_CACHE", "").strip() or RUNPOD_MODEL_CACHE)


def repo_dir_name(checkpoint):
    """`org/model` -> `models--org--model`, the hub cache's directory name."""

    return "models--" + checkpoint.replace("/", "--")


def _is_snapshot(path):
    """A directory that actually holds a model, not an empty or partial one."""

    return path.is_dir() and (path / SNAPSHOT_MARKER).exists()


def cached_snapshot(checkpoint=None, root=None):
    """The local snapshot directory for `checkpoint`, or None if not cached.

    refs/main is preferred: it is the revision the hub resolved, and on a repo
    with several snapshots on the volume it is the one that was asked for.
    Anything else is a fallback -- a mount that never wrote refs/main, or one
    whose refs/main names a snapshot that is not there -- and in that case the
    most recently written usable snapshot is a better answer than failing,
    because it is a model and the alternative is no transcription at all.
    """

    checkpoint = checkpoint or model_id()
    root = Path(root) if root is not None else cache_root()

    repo = root / repo_dir_name(checkpoint)
    snapshots = repo / "snapshots"

    if not snapshots.is_dir():
        return None

    ref = repo / "refs" / "main"

    if ref.is_file():
        try:
            revision = ref.read_text(encoding="utf-8").strip()
        except OSError:
            revision = ""

        if revision and _is_snapshot(snapshots / revision):
            return snapshots / revision

    usable = [path for path in snapshots.iterdir() if _is_snapshot(path)]

    if not usable:
        return None

    return max(usable, key=lambda path: path.stat().st_mtime)


def offline_only():
    """Whether this process is forbidden from reaching Hugging Face.

    Set in gpu/Dockerfile. It is what separates the RunPod worker, which must
    never download weights mid-job, from a workstation run of this module,
    which still pulls the model from the hub the way it always did.
    """

    return "1" in {
        os.getenv("HF_HUB_OFFLINE", "").strip(),
        os.getenv("TRANSFORMERS_OFFLINE", "").strip(),
    }


def resolve_checkpoint():
    """What to hand `from_pretrained`: a local snapshot path, or the model id.

    Returns a filesystem path whenever the model is on the volume, so nothing
    is resolved against the hub. Offline with no cached model is an error here
    rather than a download attempt that would fail later and less clearly.
    """

    explicit = os.getenv("COHERE_TRANSCRIBE_MODEL_PATH", "").strip()

    if explicit:
        if not _is_snapshot(Path(explicit)):
            raise ModelNotCached(
                f"COHERE_TRANSCRIBE_MODEL_PATH={explicit} is not a model snapshot "
                f"(no {SNAPSHOT_MARKER} in it)."
            )
        return explicit

    snapshot = cached_snapshot()

    if snapshot is not None:
        return str(snapshot)

    if offline_only():
        checkpoint = model_id()
        raise ModelNotCached(
            f"{checkpoint} is not in the RunPod model cache. Expected a snapshot "
            f"under {cache_root() / repo_dir_name(checkpoint) / 'snapshots'}. "
            "Configure this endpoint with the Cached Model (Settings -> Models) "
            "and a Hugging Face token with access to the gated repository; the "
            "worker runs offline and will not download it."
        )

    # Workstation path: no RunPod volume, no offline flag, so this is somebody
    # running `python -m rag.transcribe_cohere` and the hub is fine.
    return model_id()


@lru_cache
def load_model():
    """Processor and model, once per process.

    `lru_cache` is what makes a warm RunPod worker cheap: the second job on the
    same container gets the model already in VRAM, and only a cold start pays
    the several-gigabyte load.

    The checkpoint is resolved to a local snapshot first -- see
    `resolve_checkpoint` -- so on RunPod this reads from the cached model on
    the volume and never from the hub.

    bfloat16 on CUDA, matching the benchmarked configuration — ~3.85 GB idle
    and ~4.90 GB peak on a 20 GB card, against roughly double that in fp32 for
    no measured accuracy gain on this model. Left at the checkpoint's own dtype
    off CUDA, because bfloat16 on CPU is slower than fp32, not faster.
    """

    import torch
    from transformers import AutoProcessor, CohereAsrForConditionalGeneration

    checkpoint = resolve_checkpoint()

    # True whenever `checkpoint` is a directory on the volume, which is always
    # the case on the RunPod worker. It makes a download impossible rather than
    # merely unnecessary: without it, a cache that is missing one file falls
    # back to fetching that file mid-job, from a worker holding no token.
    local_only = Path(checkpoint).is_dir()

    processor = AutoProcessor.from_pretrained(checkpoint, local_files_only=local_only)

    kwargs = {"device_map": "auto", "local_files_only": local_only}

    if torch.cuda.is_available():
        kwargs["dtype"] = torch.bfloat16

    model = CohereAsrForConditionalGeneration.from_pretrained(checkpoint, **kwargs)

    model.eval()

    return processor, model


def timestamp(seconds):
    """Seconds -> HH:MM:SS, so each chunk can be located in the video."""

    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)

    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def transcribe_chunk(chunk_path):
    """One chunk of audio -> (text, duration in seconds).

    `language="ar"` on both the processor and the decode is what keeps Arabic
    speech as Arabic. This model can be asked to translate, and left to infer
    the target it sometimes answers an Egyptian lecture in English — which
    would be retrieved against an Arabic question and cited back to a student
    in the wrong language.
    """

    import torch
    from transformers.audio_utils import load_audio

    processor, model = load_model()

    audio = load_audio(str(chunk_path), sampling_rate=SAMPLE_RATE)
    duration = len(audio) / SAMPLE_RATE

    inputs = processor(
        audio=audio,
        sampling_rate=SAMPLE_RATE,
        return_tensors="pt",
        language="ar",
    )

    audio_chunk_index = inputs.get("audio_chunk_index")

    inputs.to(model.device, dtype=model.dtype)

    # No autograd graph: this is inference only, and building one costs VRAM
    # that the measured 4.90 GB peak does not include.
    with torch.inference_mode():
        outputs = model.generate(**inputs, max_new_tokens=256)

    text = processor.decode(
        outputs,
        skip_special_tokens=True,
        audio_chunk_index=audio_chunk_index,
        language="ar",
    )[0]

    return text, duration


def transcribe_chunks(chunks, output_path=DEFAULT_OUTPUT, verbose=True):
    """Transcribe AudioChunks in order into one transcript file.

    Consumed lazily, one at a time. `chunks` is normally the generator from
    rag.audio.iter_audio_chunks, which writes the next chunk only once this has
    finished with the last and deletes each as it goes — so the loop below is
    what keeps disk usage at one chunk rather than one lecture. Materialising
    the generator into a list here would quietly undo that.

    Truncated on entry rather than appended to. Appending was the old behaviour
    and it silently doubled the transcript on a re-run, which downstream reads
    as a lecture where every passage occurs twice at two different timestamps.

    Each chunk's position comes from the chunk itself, not from a running total
    of what has been transcribed, so one failed chunk cannot shift every
    timestamp after it.
    """

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("", encoding="utf-8")

    count = 0

    for chunk in chunks:

        if verbose:
            print(f"\nTranscribing chunk {chunk.index}: {chunk.path.name}")

        started = time.time()
        text, _ = transcribe_chunk(chunk.path)
        elapsed = time.time() - started

        if verbose:
            rtfx = chunk.duration_seconds / elapsed if elapsed else 0
            print(
                f"  {timestamp(chunk.start_seconds)} --> "
                f"{timestamp(chunk.end_seconds)} in {elapsed:.1f}s (RTFx {rtfx:.1f})"
            )

        # Written as each chunk finishes rather than at the end: an ASR failure
        # on chunk 12 should leave the first twelve on disk, not nothing.
        with open(output_path, "a", encoding="utf-8") as file:
            file.write(
                f"\n\n===== {chunk.path.name} | "
                f"{timestamp(chunk.start_seconds)} --> "
                f"{timestamp(chunk.end_seconds)} =====\n\n"
            )
            file.write(text)
            file.write("\n")

        count += 1

    if not count:
        raise RuntimeError("no audio chunks to transcribe — the source had no audio")

    if verbose:
        print(f"\nDone. {count} chunks -> {output_path}")

    return output_path


def parse_args(argv=None):

    parser = argparse.ArgumentParser(
        description="Transcribe prepared audio chunks with the Arabic model."
    )
    parser.add_argument("--chunks-dir", default=DEFAULT_CHUNKS_DIR)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--chunk-seconds", type=int, default=CHUNK_SECONDS)

    return parser.parse_args(argv)


def main(argv=None):
    """Transcribe whatever chunks are already on disk.

    `python -m rag.transcribe` is the one to use for a lecture that is not
    prepared yet — it fetches from Bunny and cuts the chunks first. This entry
    point stays for re-running the ASR over chunks that already exist.
    """

    args = parse_args(argv)

    # Every chunk, in order. The previous version looped `range(5, 15)`, which
    # skipped the first five and assumed there were exactly fifteen — true of
    # the one sample lecture and of nothing else.
    #
    # These chunks are left where they are: somebody put them there on purpose,
    # and this entry point exists to re-run the ASR over them.
    transcribe_chunks(
        chunks_from_dir(args.chunks_dir, chunk_seconds=args.chunk_seconds),
        output_path=args.output,
    )


if __name__ == "__main__":
    main()
