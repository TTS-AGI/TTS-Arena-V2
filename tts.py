# TODO: V2 of TTS Router
# Currently just use current TTS router.
from gradio_client import Client
import os

client = Client("TTS-AGI/tts-router", hf_token=os.getenv("HF_TOKEN"))
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


def predict_tts(text, model):
    global client
    if not model in model_mapping:
        raise ValueError(f"Model {model} not found")
    result = client.predict(
        text=text, model=model_mapping[model], api_name="/synthesize"
    )  # returns path to audio file
    return result
