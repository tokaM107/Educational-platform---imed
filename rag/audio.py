"""Video (local file or URL) -> 16 kHz mono wav -> fixed-length chunks.

This is the stage that used to live as loose statements at the top of
rag/transcribe_whisper.py, where the video was a module-level constant pointing
at data/videos/sample1.mp4. Pulled out into functions so that the source can be
an argument — which is the whole of what "read the video from Bunny instead of
from disk" requires, once the source is something you can pass.

`source` is handed to ffmpeg untouched, and ffmpeg does not care whether it is a
path or an https URL. So the Bunny path costs no extra code here: nothing is
downloaded, nothing is staged, and the only file written is the wav.

The format is what the ASR models want, not what sounds good: 16 kHz, mono,
signed 16-bit PCM. Speech recognition throws away everything above 8 kHz
anyway, and a stereo lecture is two copies of one voice.
"""

import contextlib
import os
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import NamedTuple

from rag import media_url


SAMPLE_RATE = 16000

# Five minutes. Long enough that the chunk boundary rarely lands mid-sentence,
# short enough to stay inside the ASR's context and to retry cheaply when one
# fails. rag/chunking.py and the transcript header format both assume this, so
# it is not a free parameter.
CHUNK_SECONDS = 5 * 60

CHUNK_PATTERN = "chunk_%03d.wav"
CHUNK_GLOB = "chunk_*.wav"

# Mono signed 16-bit PCM: two bytes per sample, one channel.
BYTES_PER_FRAME = 2

# How much of the pipe to move at a time. Small enough that a chunk is never
# assembled in memory — the bytes go from the pipe to the file in 64 KB steps.
READ_SIZE = 64 * 1024


class AudioChunk(NamedTuple):
    """One piece of the lecture's soundtrack, and where it sits in the whole.

    `start_seconds` is derived from the index rather than from a running total,
    and `duration_seconds` from the bytes actually read — so the final short
    chunk reports its real length and nothing after a chunk can be shifted by
    it.
    """

    index: int
    path: Path
    start_seconds: float
    duration_seconds: float

    @property
    def end_seconds(self):
        return self.start_seconds + self.duration_seconds


class AudioError(Exception):
    """ffmpeg could not read the source, or is not installed."""


def _run(command, what):

    try:
        result = subprocess.run(
            command, capture_output=True, text=True, check=False
        )
    except FileNotFoundError as error:
        raise AudioError("ffmpeg is not installed or not on PATH") from error

    if result.returncode != 0:
        # ffmpeg says why on stderr, and the last few lines are the part that
        # matters — a 403 from the CDN, a missing file, an unknown codec.
        tail = "\n".join(result.stderr.strip().splitlines()[-6:])
        raise AudioError(media_url.redact(f"{what} failed:\n{tail}"))

    return result


def _reconnect_options(source):
    """Retry and header flags, but only when the input is fetched over HTTP.

    These belong to ffmpeg's http protocol, not to ffmpeg generally: passing
    them alongside a local path fails the whole command with "Option reconnect
    not found" before it opens anything. So the source decides.

    The reconnect flags matter for the Bunny path, where an hour of audio is a
    long read across a CDN and a single dropped connection would otherwise
    discard all of it.

    BUNNY_MEDIA_REFERER, when set, is sent as the Referer header. Bunny's
    "Block Direct URL File Access" checks the referring domain, and a
    serverless GPU browsing nothing sends no Referer at all — so a worker that
    is otherwise correctly signed can still be refused. Unset means no header
    and exactly the previous behaviour, because a deployment whose pull zone
    does not check referrers should not start sending one.

    `-referer` is ffmpeg's own http option rather than a hand-built `-headers`
    string: it is one value that cannot inject a second header, where
    `-headers` takes raw text and a newline in it would.
    """

    if not str(source).lower().startswith(("http://", "https://")):
        return []

    options = [
        "-reconnect", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "30",
    ]

    referer = media_referer()

    if referer:
        options += ["-referer", referer]

    return options


def media_referer():
    """The Referer to send with a media fetch, or "" for none.

    Read from the environment rather than passed in, because the GPU worker
    that needs it has no application settings — this module is vendored into
    that image on its own. A bare domain is turned into a URL, which is what a
    browser would have sent and what Bunny matches on.
    """

    value = os.getenv("BUNNY_MEDIA_REFERER", "").strip()

    if not value:
        return ""

    # A newline or a space would end up inside a header value; neither belongs
    # in a domain, so the whole value is refused rather than trimmed into
    # something that looks like it worked.
    if any(character.isspace() for character in value):
        raise AudioError(
            "BUNNY_MEDIA_REFERER contains whitespace; it should be a bare "
            "domain such as example.com"
        )

    if "//" not in value:
        value = f"https://{value}"

    return value.rstrip("/") + "/"


def extract_audio(source, destination, sample_rate=SAMPLE_RATE, overwrite=False):
    """Pull the soundtrack out of `source` into a wav at `destination`.

    `source` is a local path or an https URL — a Bunny rendition, for instance.
    Reading straight from the URL means the video is never stored here, which
    for a library of hour-long lectures is the difference between needing disk
    for all of them and needing none.

    Skips the work when the wav already exists, because extraction over the
    network is the slow part of the pipeline and re-running for a failed
    transcription should not pay for it twice. `overwrite=True` forces it.
    """

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    if destination.exists() and not overwrite:
        return destination

    _run(
        [
            "ffmpeg",
            *_reconnect_options(source),
            "-i", str(source),
            "-vn",                          # the picture is not wanted at all
            "-acodec", "pcm_s16le",
            "-ar", str(sample_rate),
            "-ac", "1",
            "-y",
            str(destination),
        ],
        "audio extraction",
    )

    return destination


def split_into_chunks(audio_path, chunks_dir, chunk_seconds=CHUNK_SECONDS):
    """Cut the wav into fixed-length pieces and return them in order.

    The directory is emptied first. Chunk names are positional — chunk_007 is
    the eighth five minutes — and transcribe_cohere.py works out each chunk's
    place in the lecture by multiplying its index by the length. Leaving a
    longer lecture's chunks behind would silently mix two videos together and
    stamp the result with times from neither.
    """

    audio_path = Path(audio_path)
    chunks_dir = Path(chunks_dir)
    chunks_dir.mkdir(parents=True, exist_ok=True)

    for stale in chunks_dir.glob(CHUNK_GLOB):
        stale.unlink()

    _run(
        [
            "ffmpeg",
            "-i", str(audio_path),
            "-f", "segment",
            "-segment_time", str(chunk_seconds),
            "-c", "copy",
            "-y",
            str(chunks_dir / CHUNK_PATTERN),
        ],
        "audio segmentation",
    )

    return sorted(chunks_dir.glob(CHUNK_GLOB))


def chunk_start_seconds(index, chunk_seconds=CHUNK_SECONDS):
    """Where chunk `index` begins in the lecture.

    Positional arithmetic, which holds because -segment_time cuts every piece to
    the same length bar the last. It is also why the chunks directory has to be
    cleared: the name is the only record of where a chunk came from.
    """

    return index * chunk_seconds


def existing_chunks(chunks_dir):

    return sorted(Path(chunks_dir).glob(CHUNK_GLOB))


# =========================
# Streaming: one chunk on disk at a time
# =========================
#
# The pipeline reads an hour-long lecture off a CDN and hands it to an ASR model
# five minutes at a time. Done naively that means a full-length wav on disk plus
# every chunk of it — for one lecture here, 143 MB and another 137 MB — none of
# which is wanted once the transcript exists.
#
# So ffmpeg is asked for raw PCM on stdout instead of files, and this module
# cuts the stream into chunks itself. That gives three things at once:
#
#   one pass       the source is read start to finish exactly once, so nothing
#                  is re-downloaded per chunk and seeking never enters into it
#
#   backpressure   the pipe is what limits ffmpeg. While the ASR is busy nobody
#                  is reading, the pipe buffer fills, and ffmpeg blocks — so it
#                  cannot run ahead and pile up chunks that have to go somewhere
#
#   exact bounds   at 16 kHz mono 16-bit the arithmetic is fixed: 32,000 bytes a
#                  second, 9.6 MB per five minutes. Chunk N starts at N x 300s
#                  by construction rather than by measurement
#
# Everything lives in a TemporaryDirectory, and each chunk is deleted as soon as
# the consumer has finished with it.


def bytes_per_second(sample_rate=SAMPLE_RATE):

    return sample_rate * BYTES_PER_FRAME


def _pcm_command(source, sample_rate):
    """ffmpeg reading `source` and writing raw mono PCM to stdout."""

    return [
        "ffmpeg",
        *_reconnect_options(source),
        "-i", str(source),
        "-vn",                     # the picture is never decoded to begin with
        "-f", "s16le",             # headerless PCM: the frame size is knowable
        "-acodec", "pcm_s16le",
        "-ar", str(sample_rate),
        "-ac", "1",
        "-loglevel", "error",
        "pipe:1",
    ]


def _write_wav(stream, path, limit, sample_rate):
    """Move up to `limit` bytes of PCM from `stream` into a wav file.

    Returns the number of bytes written, which is short only at the end of the
    audio — that is how the final chunk's real length is known.

    Copied in READ_SIZE steps: a chunk is 9.6 MB and there is no reason for any
    of it to be resident, so the bytes go pipe -> file without being assembled.
    """

    written = 0

    with wave.open(str(path), "wb") as wav:

        wav.setnchannels(1)
        wav.setsampwidth(BYTES_PER_FRAME)
        wav.setframerate(sample_rate)

        while written < limit:

            block = stream.read(min(READ_SIZE, limit - written))

            if not block:
                break

            # A partial frame cannot be written and would desynchronise every
            # sample after it. Only possible on a truncated stream.
            usable = len(block) - (len(block) % BYTES_PER_FRAME)

            wav.writeframes(block[:usable])
            written += usable

            if usable < len(block):
                break

    return written


def iter_audio_chunks(
    source,
    chunk_seconds=CHUNK_SECONDS,
    sample_rate=SAMPLE_RATE,
    workspace=None,
):
    """Yield the lecture's audio one chunk at a time, deleting as it goes.

    `source` is whatever ffmpeg can open: a Bunny rendition URL, an HLS
    playlist, or a local file. Nothing is downloaded and nothing outlives the
    generator — the video stays on Bunny, and the only audio that exists is the
    chunk currently being transcribed.

    Each chunk is removed once the consumer asks for the next one, so at most
    one is on disk at any moment. Abandoning the generator part-way, or an
    exception from whatever is consuming it, still runs the cleanup: the
    temporary directory goes with the `with` block and ffmpeg is stopped in the
    `finally`.

    Yields AudioChunk. The last one is shorter than the rest whenever the
    lecture does not divide evenly, and says so in `duration_seconds`.
    """

    rate = bytes_per_second(sample_rate)
    chunk_bytes = rate * chunk_seconds

    # A caller can supply its own directory (a test, mostly). Otherwise a
    # temporary one that removes itself and everything inside it.
    keeper = (
        contextlib.nullcontext(workspace)
        if workspace is not None
        else tempfile.TemporaryDirectory(prefix="lecture-audio-")
    )

    with keeper as directory:

        directory = Path(directory)

        # stderr goes to a file rather than a pipe nobody is draining. An
        # unread stderr pipe fills and deadlocks ffmpeg part-way through a long
        # read, which looks exactly like a hung network.
        errors = directory / "ffmpeg.log"

        try:
            with open(errors, "wb") as error_log:
                process = subprocess.Popen(
                    _pcm_command(source, sample_rate),
                    stdout=subprocess.PIPE,
                    stderr=error_log,
                )
        except FileNotFoundError as error:
            raise AudioError("ffmpeg is not installed or not on PATH") from error

        current = None

        try:
            index = 0

            while True:

                current = directory / (CHUNK_PATTERN % index)

                written = _write_wav(
                    process.stdout, current, chunk_bytes, sample_rate
                )

                if not written:
                    # End of the audio, landing exactly on a boundary.
                    current.unlink(missing_ok=True)
                    current = None
                    break

                yield AudioChunk(
                    index=index,
                    path=current,
                    start_seconds=index * chunk_seconds,
                    duration_seconds=written / rate,
                )

                # The consumer is done with it. Deleted before the next chunk is
                # written, which is what keeps the ceiling at one.
                current.unlink(missing_ok=True)
                current = None

                if written < chunk_bytes:
                    break

                index += 1

            process.stdout.close()

            if process.wait() != 0:
                raise AudioError(
                    "audio streaming failed:\n"
                    + _tail(errors)
                )

        finally:
            if current is not None:
                current.unlink(missing_ok=True)

            _stop(process)


def _tail(path, lines=6):

    try:
        text = Path(path).read_text(errors="replace").strip()
    except OSError:
        return "(no ffmpeg output)"

    # Redacted here rather than at each caller: everything that reads ffmpeg's
    # stderr goes through this, and the stderr of a failed fetch contains the
    # signed URL verbatim.
    return media_url.redact(
        "\n".join(text.splitlines()[-lines:])
    ) or "(no ffmpeg output)"


def _stop(process):
    """Make sure ffmpeg is not left running when nobody is reading it.

    A generator abandoned half way leaves ffmpeg blocked on a pipe with no
    reader. Closing stdout and terminating is what stops an orphan holding a
    CDN connection open for the rest of the process's life.
    """

    if process.poll() is not None:
        return

    with contextlib.suppress(Exception):
        if process.stdout and not process.stdout.closed:
            process.stdout.close()

    with contextlib.suppress(Exception):
        process.terminate()
        process.wait(timeout=10)

    if process.poll() is None:
        with contextlib.suppress(Exception):
            process.kill()
            process.wait(timeout=5)


def chunks_from_dir(chunks_dir, chunk_seconds=CHUNK_SECONDS, sample_rate=SAMPLE_RATE):
    """AudioChunks for wavs already on disk, and left there.

    The re-run path: `python -m rag.transcribe_cohere --chunks-dir ...` over
    chunks somebody kept deliberately. Nothing here deletes anything, which is
    the difference between this and iter_audio_chunks.
    """

    rate = bytes_per_second(sample_rate)

    for index, path in enumerate(existing_chunks(chunks_dir)):

        # Header aside, a wav of PCM is its own duration.
        duration = max(0.0, (path.stat().st_size - 44) / rate)

        yield AudioChunk(
            index=index,
            path=path,
            start_seconds=index * chunk_seconds,
            duration_seconds=duration,
        )
