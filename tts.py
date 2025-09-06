# TODO: V2 of TTS Router
# Currently just use current TTS router.
import os
import json
from dotenv import load_dotenv
import fal_client
import requests
import time
import io
from pyht import Client as PyhtClient
from pyht.client import TTSOptions
import base64
import tempfile
import random

load_dotenv()

ZEROGPU_TOKENS = os.getenv("ZEROGPU_TOKENS", "").split(",")


def get_zerogpu_token():
    return random.choice(ZEROGPU_TOKENS)


model_mapping = {
    "eleven-multilingual-v2": {
        "provider": "elevenlabs",
        "model": "eleven_multilingual_v2",
    },
    "async-1": {
        "provider": "async",
        "model": "async-1",
    },
    "eleven-turbo-v2.5": {
        "provider": "elevenlabs",
        "model": "eleven_turbo_v2_5",
    },
    "eleven-flash-v2.5": {
        "provider": "elevenlabs",
        "model": "eleven_flash_v2_5",
    },
    "cartesia-sonic-2": {
        "provider": "cartesia",
        "model": "sonic-2",
    },
    "spark-tts": {
        "provider": "spark",
        "model": "spark-tts",
    },
    "playht-2.0": {
        "provider": "playht",
        "model": "PlayHT2.0",
    },
    "styletts2": {
        "provider": "styletts",
        "model": "styletts2",
    },
    "kokoro-v1": {
        "provider": "kokoro",
        "model": "kokoro_v1",
    },
    "cosyvoice-2.0": {
        "provider": "cosyvoice",
        "model": "cosyvoice_2_0",
    },
    "papla-p1": {
        "provider": "papla",
        "model": "papla_p1",
    },
    "hume-octave": {
        "provider": "hume",
        "model": "octave",
    },
    "megatts3": {
        "provider": "megatts3",
        "model": "megatts3",
    },
    "minimax-02-hd": {
        "provider": "minimax",
        "model": "speech-02-hd",
    },
    "minimax-02-turbo": {
        "provider": "minimax",
        "model": "speech-02-turbo",
    },
    "lanternfish-1": {
        "provider": "lanternfish",
        "model": "lanternfish-1",
    },
    "nls-pre-v1": {
        "provider": "nls",
        "model": "nls-1",
    },
    "chatterbox": {
        "provider": "chatterbox",
        "model": "chatterbox",
    },
    "inworld": {
        "provider": "inworld",
        "model": "inworld-tts-1",
    },
    "inworld-max": {
        "provider": "inworld",
        "model": "inworld-tts-1-max",
    },
    "wordcab": {
        "provider": "wordcab",
        "model": "wordcab",
    },
}
url = "https://tts-agi-tts-router-v2.hf.space/tts"
headers = {
    "accept": "application/json",
    "Content-Type": "application/json",
    "Authorization": f'Bearer {os.getenv("HF_TOKEN")}',
}
data = {"text": "string", "provider": "string", "model": "string"}


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


def predict_dia(script):
    # Convert script to the required format for Dia
    if isinstance(script, list):
        # Convert from list of dictionaries to formatted string
        formatted_text = ""
        for turn in script:
            speaker_id = turn.get("speaker_id", 0)
            speaker_tag = "[S1]" if speaker_id == 0 else "[S2]"
            text = turn.get("text", "").strip().replace("[S1]", "").replace("[S2]", "")
            formatted_text += f"{speaker_tag} {text} "
        text = formatted_text.strip()
    else:
        # If it's already a string, use as is
        text = script
    # Make a POST request to initiate the dialogue generation
    headers = {
        # "Content-Type": "application/json",
        "Authorization": f"Bearer {get_zerogpu_token()}"
    }

    response = requests.post(
        "https://mrfakename-dia-1-6b.hf.space/gradio_api/call/generate_dialogue",
        headers=headers,
        json={"data": [text]},
    )

    # Extract the event ID from the response
    event_id = response.json()["event_id"]

    # Make a streaming request to get the generated dialogue
    stream_url = f"https://mrfakename-dia-1-6b.hf.space/gradio_api/call/generate_dialogue/{event_id}"

    # Use a streaming request to get the audio data
    with requests.get(stream_url, headers=headers, stream=True) as stream_response:
        # Process the streaming response
        for line in stream_response.iter_lines():
            if line:
                if line.startswith(b"data: ") and not line.startswith(b"data: null"):
                    audio_data = line[6:]
                    return requests.get(json.loads(audio_data)[0]["url"]).content


def predict_tts(text, model):
    global client
    print(f"Predicting TTS for {model}")
    # Exceptions: special models that shouldn't be passed to the router
    if model == "csm-1b":
        return predict_csm(text)
    elif model == "playdialog-1.0":
        return predict_playdialog(text)
    elif model == "dia-1.6b":
        return predict_dia(text)

    if not model in model_mapping:
        raise ValueError(f"Model {model} not found")

    result = requests.post(
        url,
        headers=headers,
        data=json.dumps(
            {
                "text": text,
                "provider": model_mapping[model]["provider"],
                "model": model_mapping[model]["model"],
            }
        ),
    )
    response_json = result.json()

    audio_data = response_json["audio_data"]  # base64 encoded audio data
    extension = response_json["extension"]
    # Decode the base64 audio data
    audio_bytes = base64.b64decode(audio_data)

    # Create a temporary file to store the audio data
    with tempfile.NamedTemporaryFile(delete=False, suffix=f".{extension}") as temp_file:
        temp_file.write(audio_bytes)
        temp_path = temp_file.name

    return temp_path


if __name__ == "__main__":
    print(
        predict_dia(
            [
                {"text": "Hello, how are you?", "speaker_id": 0},
                {"text": "I'm great, thank you!", "speaker_id": 1},
            ]
        )
    )
    # print("Predicting PlayDialog")
    # print(
    #     predict_playdialog(
    #         [
    #             {"text": "Hey how are you doing.", "speaker_id": 0},
    #             {"text": "Pretty good, pretty good.", "speaker_id": 1},
    #             {"text": "I'm great, so happy to be speaking to you.", "speaker_id": 0},
    #         ]
    #     )
    # )
