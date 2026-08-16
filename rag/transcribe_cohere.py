from transformers import AutoProcessor, CohereAsrForConditionalGeneration
import time
from transformers.audio_utils import load_audio
import os


MODEL_ID = "CohereLabs/cohere-transcribe-arabic-07-2026"

# Load processor and model once
processor = AutoProcessor.from_pretrained(MODEL_ID)

model = CohereAsrForConditionalGeneration.from_pretrained(
    MODEL_ID,
    device_map="auto"
)


# Paths
chunks_dir = "data/audios/chunks"
output_file = "data/transcripts/transcript.txt"


# Every chunk is 5 minutes except the last one
# (full video is 1:14:42 = 14 x 5:00 + 4:42)
chunk_duration_s = 5 * 60


def timestamp(seconds):
    """Seconds -> HH:MM:SS, so each chunk can be located in the video."""

    hours, remainder = divmod(int(seconds), 3600)
    minutes, secs = divmod(remainder, 60)

    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


# Loop over all 15 chunks
for i in range(5,15 ):

    chunk_name = f"chunk_{i:03d}.wav"
    chunk_path = os.path.join(chunks_dir, chunk_name)

    print(f"\n{'=' * 50}")
    print(f"Transcribing {i + 1}/15: {chunk_name}")
    print(f"{'=' * 50}")

    # Load audio
    audio = load_audio(
        chunk_path,
        sampling_rate=16000
    )

    sr = 16000
    duration_s = len(audio) / sr

    # Position of this chunk inside the full video
    start_s = i * chunk_duration_s
    end_s = start_s + duration_s

    print(
        f"Audio duration: {duration_s / 60:.1f} minutes "
        f"({timestamp(start_s)} --> {timestamp(end_s)})"
    )

    # Prepare inputs
    inputs = processor(
        audio=audio,
        sampling_rate=sr,
        return_tensors="pt",
        language="ar"
    )

    audio_chunk_index = inputs.get("audio_chunk_index")

    inputs.to(
        model.device,
        dtype=model.dtype
    )

    # Transcribe
    start = time.time()

    outputs = model.generate(
        **inputs,
        max_new_tokens=256
    )

    text = processor.decode(
        outputs,
        skip_special_tokens=True,
        audio_chunk_index=audio_chunk_index,
        language="ar"
    )[0]

    elapsed = time.time() - start
    rtfx = duration_s / elapsed

    print(
        f"Transcribed in {elapsed:.1f}s — RTFx: {rtfx:.1f}"
    )

    # Save transcript
    with open(
        output_file,
        "a",
        encoding="utf-8"
    ) as file:

        file.write(
            f"\n\n===== {chunk_name} | "
            f"{timestamp(start_s)} --> {timestamp(end_s)} =====\n\n"
        )

        file.write(text)

        file.write("\n")

    print(f"Saved {chunk_name}")


print("\nDone! All 15 chunks have been transcribed.")