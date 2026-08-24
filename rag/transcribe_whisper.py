"""SUPERSEDED — kept for reference only. Do not run this on a real lecture.

Use `python -m rag.transcribe` instead.

This is the original script, from before the videos moved to Bunny Stream. It
reads one hardcoded file out of data/videos/, writes the entire lecture out as
a wav under data/audios/, then cuts every chunk of it before transcribing any —
for the sample lecture that is 143 MB of audio plus another 137 MB of chunks,
none of it deleted afterwards. It is exactly the behaviour rag/audio.py's
streaming path exists to replace.

It is also still wrong in a smaller way: the block after the transcription loop
writes the last chunk's text a second time, and would raise NameError on a
lecture with no chunks at all.

Left in the tree because it documents the HF/fal Whisper call, which nothing
else does. Nothing imports it.
"""

import os
import subprocess
from pathlib import Path

from dotenv import load_dotenv
from huggingface_hub import InferenceClient


# =========================
# Configuration
# =========================

BASE_DIR = Path(__file__).resolve().parent.parent

VIDEO_PATH = BASE_DIR / "data/videos/sample1.mp4"
AUDIO_PATH = BASE_DIR / "data/audios/sample1.wav"
CHUNKS_DIR = BASE_DIR / "data/audios/chunks"
OUTPUT_PATH = BASE_DIR / "data/transcripts/sample1.txt"

CHUNK_DURATION = 5 * 60  # 5 minutes


# =========================
# Load environment variables
# =========================

load_dotenv(BASE_DIR / ".env")

HF_TOKEN = os.getenv("HF_TOKEN")

if not HF_TOKEN:
    raise RuntimeError("HF_TOKEN is not set.")


# =========================
# Hugging Face client
# =========================

client = InferenceClient(
    provider="fal-ai",
    api_key=HF_TOKEN,
)


# =========================
# Create directories
# =========================

CHUNKS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)


# =========================
# Extract audio
# =========================

if not AUDIO_PATH.exists():

    print("Extracting audio...")

    subprocess.run([
        "ffmpeg",
        "-i", str(VIDEO_PATH),
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        str(AUDIO_PATH),
        "-y"
    ], check=True)

else:
    print("Audio already exists. Skipping extraction.")


# =========================
# Split audio into chunks
# =========================

print("Splitting audio into chunks...")

subprocess.run([
    "ffmpeg",
    "-i", str(AUDIO_PATH),
    "-f", "segment",
    "-segment_time", str(CHUNK_DURATION),
    "-c", "copy",
    str(CHUNKS_DIR / "chunk_%03d.wav"),
    "-y"
], check=True)


# =========================
# Transcribe each chunk
# =========================

chunk_files = sorted(CHUNKS_DIR.glob("chunk_*.wav"))

print(f"Found {len(chunk_files)} chunks.")

all_text = []

for i, chunk_file in enumerate(chunk_files):

    print(f"\nTranscribing {i + 1}/{len(chunk_files)}:")
    print(chunk_file.name)

    result = client.automatic_speech_recognition(
        str(chunk_file),
        model="openai/whisper-large-v3"
    )

    text = result.text.strip()

    all_text.append(
        f"\n--- Chunk {i + 1} ---\n{text}\n"
    )

with open(OUTPUT_PATH, "a", encoding="utf-8") as f:
    f.write(f"\n--- Chunk {i+1} ---\n")
    f.write(text)
    f.write("\n")
# =========================
# Save transcript
# =========================

with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
    f.write("\n".join(all_text))


print("\nTranscription completed!")
print(f"Saved to: {OUTPUT_PATH}")