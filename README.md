# TTS Arena V2

- [Vote on the latest TTS models!](https://huggingface.co/spaces/TTS-AGI/TTS-Arena-V2)
- [Join the Discord server](https://discord.gg/HB8fMR6GTr)

This is the source code for the new version of the TTS Arena. It is built on Flask, rather than Gradio (which was used in the previous version).

## Development

```bash
pip install -r requirements.txt
python app.py
```

Note that you will need to setup the `.env` file with the correct credentials. See `.env.example` for more information.

You will need an active deployment of the [TTS Router V2](https://github.com/TTS-AGI/tts-router-v2) as well, hosted on a Hugging Face Space.

## Deployment

The app is deployed on Hugging Face Spaces. See the `.github/workflows/sync-to-hf.yaml` file for more information.

## Citation

If you use or reference the TTS Arena in your work, please cite it as follows:

```
@misc{tts-arena-v2,
        title        = {TTS Arena 2.0: Benchmarking Text-to-Speech Models in the Wild},
        author       = {mrfakename and Srivastav, Vaibhav and Fourrier, Clémentine and Pouget, Lucain and Lacombe, Yoach and main and Gandhi, Sanchit and Passos, Apolinário and Cuenca, Pedro},
        year         = 2025,
        publisher    = {Hugging Face},
        howpublished = "\url{https://huggingface.co/spaces/TTS-AGI/TTS-Arena-V2}"
}
```

## License

This project is dual-licensed under the MIT and Apache 2.0 licenses. See the LICENSE.MIT and LICENSE.APACHE files respectively for details.