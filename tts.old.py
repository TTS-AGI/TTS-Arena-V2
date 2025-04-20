# TODO: V2 of TTS Router
# Currently just use current TTS router.
from gradio_client import Client
import os
from dotenv import load_dotenv
import fal_client
import requests
import time
import io
from pyht import Client as PyhtClient
from pyht.client import TTSOptions

load_dotenv()

try:
    client = Client("TTS-AGI/tts-router", hf_token=os.getenv("HF_TOKEN"))
except Exception as e:
    print(f"Error initializing client: {e}")
    client = None

model_mapping = {
    "eleven-multilingual-v2": "eleven",
    "playht-2.0": "playht",
    "styletts2": "styletts2",
    "kokoro-v1": "kokorov1",
    "cosyvoice-2.0": "cosyvoice",
    "playht-3.0-mini": "playht3",
    "papla-p1": "papla",
    "hume-octave": "hume",
}


def predict_csm(script):
    result = fal_client.subscribe(
        "fal-ai/csm-1b",
        arguments={
            # "scene": [{
            #     "text": "Hey how are you doing.",
            #     "speaker_id": 0
            # }, {
            #     "text": "Pretty good, pretty good.",
            #     "speaker_id": 1
            # }, {
            #     "text": "I'm great, so happy to be speaking to you.",
            #     "speaker_id": 0
            # }]
            "scene": script
        },
        with_logs=True,
    )
    return requests.get(result["audio"]["url"]).content


def predict_playdialog(script):
    # Initialize the PyHT client
    pyht_client = PyhtClient(
        user_id=os.getenv("PLAY_USERID"),
        api_key=os.getenv("PLAY_SECRETKEY"),
    )

    # Define the voices
    voice_1 = "s3://voice-cloning-zero-shot/baf1ef41-36b6-428c-9bdf-50ba54682bd8/original/manifest.json"
    voice_2 = "s3://voice-cloning-zero-shot/e040bd1b-f190-4bdb-83f0-75ef85b18f84/original/manifest.json"

    # Convert script format from CSM to PlayDialog format
    if isinstance(script, list):
        # Process script in CSM format (list of dictionaries)
        text = ""
        for turn in script:
            speaker_id = turn.get("speaker_id", 0)
            prefix = "Host 1:" if speaker_id == 0 else "Host 2:"
            text += f"{prefix} {turn['text']}\n"
    else:
        # If it's already a string, use as is
        text = script

    # Set up TTSOptions
    options = TTSOptions(
        voice=voice_1, voice_2=voice_2, turn_prefix="Host 1:", turn_prefix_2="Host 2:"
    )

    # Generate audio using PlayDialog
    audio_chunks = []
    for chunk in pyht_client.tts(text, options, voice_engine="PlayDialog"):
        audio_chunks.append(chunk)

    # Combine all chunks into a single audio file
    return b"".join(audio_chunks)


def predict_tts(text, model):
    global client
    # Exceptions: special models that shouldn't be passed to the router
    if model == "csm-1b":
        return predict_csm(text)
    elif model == "playdialog-1.0":
        return predict_playdialog(text)

    if not model in model_mapping:
        raise ValueError(f"Model {model} not found")
    result = client.predict(
        text=text, model=model_mapping[model], api_name="/synthesize"
    )  # returns path to audio file
    return result


if __name__ == "__main__":
    print("Predicting PlayDialog")
    print(
        predict_playdialog(
            [
                {"text": "Hey how are you doing.", "speaker_id": 0},
                {"text": "Pretty good, pretty good.", "speaker_id": 1},
                {"text": "I'm great, so happy to be speaking to you.", "speaker_id": 0},
            ]
        )
    )
