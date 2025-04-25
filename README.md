# TTS Arena V2

- [Vote on the latest TTS models!](https://huggingface.co/spaces/TTS-AGI/TTS-Arena-V2)
- [Join the Discord server](https://discord.gg/HB8fMR6GTr)

This is the source code for the new version of the TTS Arena. It is built on Flask, rather than Gradio (which was used in the previous version).

## V2 Migration

* Delete `TTS-AGI/TTS-Arena-V2` Space.
* Rename `TTS-AGI/TTS-Arena` to `TTS-AGI/TTS-Arena-V2` (preserves likes and discussions).
* Deploy Arena V2 to `TTS-AGI/TTS-Arena-V2`.
* Rename `TTS-AGI/TTS-Arena-Legacy` to `TTS-AGI/TTS-Arena`.

Result:

* `TTS-AGI/TTS-Arena` is the read-only legacy leaderboard.
* `TTS-AGI/TTS-Arena-V2` is the new Arena.

Resource groups:

* Remove `TTS-AGI/TTS-Arena-V2` and `TTS-AGI/TTS-Arena-Legacy` from resource groups to make them public.

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

# Top Voters Leaderboard Feature

This feature adds a new leaderboard tab that displays the top 10 most active voters on the TTS Arena platform. It also allows users to opt out of appearing on this leaderboard if they prefer to keep their voting activity private.

## Features

- New "Top Voters" tab in the leaderboard page
- User privacy control via toggle switch 
- Data migration script for adding the new database field

## Database Migration

Before using this feature, you need to run the database migration to add the new field. There are two ways to do this:

### Option 1: Using Flask-Migrate (Recommended)

If you have Flask-Migrate set up (which TTS Arena does), run:

```
flask db upgrade
```

This will automatically apply all pending migrations including the one for this feature.

### Option 2: Using Standalone Migration Script

Alternatively, you can use the standalone migration script:

```
python migrations/add_user_leaderboard_visibility.py
```

This script adds the `show_in_leaderboard` column to the User model in the database. It can be safely removed after the migration is complete.

## How It Works

1. The leaderboard page now includes a "Top Voters" tab
2. When a user is logged in, they can toggle whether they appear in the top voters leaderboard
3. The toggle setting is saved immediately via an AJAX call
4. Only users who have opted in (default) will appear in the top voters list

## Privacy Considerations

- Users are included in the leaderboard by default (opt-out approach)
- The toggle is only available to logged-in users
- The leaderboard only shows usernames, total vote counts, and join dates
- No personal information or specific voting patterns are revealed