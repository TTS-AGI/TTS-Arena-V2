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

load_dotenv()


model_mapping = {
    "eleven-multilingual-v2": {
        "provider": "elevenlabs",
        "model": "eleven_multilingual_v2"
    },
    "playht-2.0": {
        "provider": "playht",
        "model": "PlayHT2.0"
    },
    "styletts2": {
        "provider": "styletts",
        "model": "styletts2"
    },
    "kokoro-v1": {
        "provider": "kokoro",
        "model": "kokoro_v1"
    },
    "cosyvoice-2.0": {
        "provider": "cosyvoice",
        "model": "cosyvoice_2_0"
    },
    "papla-p1": {
        "provider": "papla",
        "model": "papla_p1"
    },
    "hume-octave": {
        "provider": "hume",
        "model": "octave"
    },
}

url = 'https://tts-agi-tts-router-v2.hf.space/tts'
headers = {
    'accept': 'application/json',
    'Content-Type': 'application/json',
    'Authorization': f'Bearer {os.getenv("HF_TOKEN")}'
}
data = {
    'text': 'string',
    'provider': 'string',
    'model': 'string'
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
    return requests.get(result['audio']['url']).content

def predict_playdialog(script):
    # Initialize the PyHT client
    pyht_client = PyhtClient(
        user_id=os.getenv("PLAY_USERID"),
        api_key=os.getenv("PLAY_SECRETKEY"),
    )
    
    # Define the voices
    voice_1 = 's3://voice-cloning-zero-shot/baf1ef41-36b6-428c-9bdf-50ba54682bd8/original/manifest.json'
    voice_2 = 's3://voice-cloning-zero-shot/e040bd1b-f190-4bdb-83f0-75ef85b18f84/original/manifest.json'
    
    # Convert script format from CSM to PlayDialog format
    if isinstance(script, list):
        # Process script in CSM format (list of dictionaries)
        text = ""
        for turn in script:
            speaker_id = turn.get('speaker_id', 0)
            prefix = "Host 1:" if speaker_id == 0 else "Host 2:"
            text += f"{prefix} {turn['text']}\n"
    else:
        # If it's already a string, use as is
        text = script
    
    # Set up TTSOptions
    options = TTSOptions(
        voice=voice_1,
        voice_2=voice_2,
        turn_prefix="Host 1:",
        turn_prefix_2="Host 2:"
    )
    
    # Generate audio using PlayDialog
    audio_chunks = []
    for chunk in pyht_client.tts(text, options, voice_engine="PlayDialog"):
        audio_chunks.append(chunk)
    
    # Combine all chunks into a single audio file
    return b''.join(audio_chunks)

def predict_tts(text, model):
    global client
    # Exceptions: special models that shouldn't be passed to the router
    if model == "csm-1b":
        return predict_csm(text)
    elif model == "playdialog-1.0":
        return predict_playdialog(text)
    
    if not model in model_mapping:
        raise ValueError(f"Model {model} not found")
    
    result = requests.post(url, headers=headers, data=json.dumps({
        "text": text,
        "provider": model_mapping[model]["provider"],
        "model": model_mapping[model]["model"]
    }))
    
    response_json = result.json()
    audio_data = response_json["audio_data"]
    audio_type = response_json["mime_type"]
    
    # Save audio to a temporary file
    import tempfile
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=f".{audio_type}")
    temp_file.write(audio_data.encode('utf-8') if isinstance(audio_data, str) else audio_data)
    temp_file.close()
    
    return result.content

if __name__ == "__main__":
    print("Predicting PlayDialog")
    print(predict_playdialog([{"text": "Hey how are you doing.", "speaker_id": 0}, {"text": "Pretty good, pretty good.", "speaker_id": 1}, {"text": "I'm great, so happy to be speaking to you.", "speaker_id": 0}]))