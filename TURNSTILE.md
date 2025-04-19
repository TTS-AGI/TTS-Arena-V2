# Cloudflare Turnstile Integration

TTS Arena supports Cloudflare Turnstile for bot protection. This guide explains how to set up and configure Turnstile for your deployment.

## What is Cloudflare Turnstile?

Cloudflare Turnstile is a CAPTCHA alternative that provides protection against bots and malicious traffic while maintaining a user-friendly experience. Unlike traditional CAPTCHAs, Turnstile uses a variety of signals to detect bots without forcing legitimate users to solve frustrating puzzles.

## Setup Instructions

### 1. Register for Cloudflare Turnstile

1. Create a Cloudflare account or log in to your existing account
2. Go to the [Turnstile dashboard](https://dash.cloudflare.com/?to=/:account/turnstile)
3. Click "Add Site" and follow the instructions
4. Create a new site key
   - Choose "Managed" or "Invisible" mode (Managed is recommended for better balance of security and user experience)
   - Set an appropriate domain policy
   - Create the site key

Once created, you'll receive a **Site Key** (public) and **Secret Key** (private).

### 2. Configure Environment Variables

Add the following environment variables to your deployment:

```
TURNSTILE_ENABLED=true
TURNSTILE_SITE_KEY=your_site_key_here
TURNSTILE_SECRET_KEY=your_secret_key_here
TURNSTILE_TIMEOUT_HOURS=24
```

| Variable | Description |
|----------|-------------|
| `TURNSTILE_ENABLED` | Set to `true` to enable Turnstile protection |
| `TURNSTILE_SITE_KEY` | Your Cloudflare Turnstile site key |
| `TURNSTILE_SECRET_KEY` | Your Cloudflare Turnstile secret key |
| `TURNSTILE_TIMEOUT_HOURS` | How often users need to verify (default: 24 hours) |

### 3. Implementation Details

When Turnstile is enabled:
- All routes and API endpoints require Turnstile verification
- Users are redirected to a verification page when they first visit
- Verification status is stored in the session
- Re-verification is required after the timeout period
- API requests receive a 403 error if not verified

## Customization

The Turnstile verification page uses the same styling as the main application, providing a seamless user experience. You can customize the appearance by modifying `templates/turnstile.html`.

## Troubleshooting

- **Verification Loops**: If users get stuck in verification loops, check that cookies are being properly stored (ensure proper cookie settings and no browser extensions blocking cookies)
- **API Errors**: If API clients receive 403 errors, they need to implement Turnstile verification
- **Missing Environment Variables**: Ensure all required environment variables are set correctly 