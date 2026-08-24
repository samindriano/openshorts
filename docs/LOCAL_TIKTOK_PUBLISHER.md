# Local TikTok publisher

Self-hosted OpenShorts can publish TikTok clips through the isolated
`publisher` container. The backend remains the only caller of this service;
the browser never receives TikTok cookies or session IDs.

## Setup

1. Start the normal stack with `docker compose up -d --build`.
2. Export the TikTok session as a Netscape-format `cookies.txt` file (for
   example with a local cookies-export extension while logged in) to
   `publisher-data/tiktok-cookies.txt`. It must include the HttpOnly
   `sessionid` cookie; a browser JSON/session export is not accepted by the
   pinned adapter. Keep that file local; it is ignored by Git and must never
   be pasted into logs, issues, or the UI.
3. Recreate only the publisher service after adding or replacing the session:
   `docker compose up -d --build publisher`.

To logout or re-authenticate, stop the publisher, replace or remove the local
`publisher-data/tiktok-cookies.txt` file manually, then recreate the publisher.
The next live attempt reports `login_required`; no cookie is copied into the
backend or browser UI.

The service reports `ready/configured`, `login_required`, `busy`, `failed`, or
`unknown` through `/api/local/tiktok/status`. A configured cookie file does not
prove that TikTok still accepts the session; `session_valid` stays unknown
until a real TikTok interaction establishes it.

## Safety contract

The Post dialog resolves the clip's current `result.clips[index].video_url`
inside OpenShorts and sends that exact file's identity and SHA-256 to the
publisher. The publisher re-checks the file is under the read-only `/output`
mount and refuses a changed hash. It never selects the newest file by mtime.

Dry-run is the default. It verifies file identity, caption/hashtag mapping,
schedule/options, and durable state without opening TikTok. Live attempts use
the pinned `wkaisertexas/tiktok-uploader` `build-uv-v1.7` commit, with no
automatic retry. A browser exception or an upstream result that cannot confirm
requested options is `unknown`; inspect TikTok before any manual retry.

The adapter passes one initial upload attempt to the upstream API. In this
upstream version, `num_retries=0` means zero attempts, so it is deliberately
not used; any second attempt requires an explicit user action.

Dry-run responses/state contain safe evidence for the selected filename, SHA,
normalized caption, hashtags, schedule, product ID, and options. Cookie/session
contents are never recorded.

TikTok scheduling requires an explicit timezone, a time 20 minutes to 10 days
ahead, and a five-minute boundary. Product IDs are forwarded when supplied;
because upstream does not provide an independent product-link confirmation,
that live result is conservatively classified as `unknown` instead of claiming
success.

The existing `/api/social/post` Upload-Post route is unchanged for hosted mode
and for existing Instagram/YouTube workflows. This local adapter is TikTok
only and does not use Upload-Post.

## Common states and maintenance

- `ready`: a local session file exists and no request is busy. TikTok may still
  reject an expired session; that live result becomes `login_required`.
- `login_required`: add or replace the Netscape cookie file before a live
  attempt.
- `busy`: one request is in flight; the UI does not start another one.
- `failed`: the uploader reported a known failure.
- `unknown`: TikTok may have accepted the upload or an option may be
  unconfirmed. Inspect TikTok before any manual retry.

To update the upstream uploader safely, change the commit in both
`publisher/requirements.txt` and the compose pin, rebuild only the isolated
publisher image, run publisher tests, Docker health, and a dry-run with a
retained edited clip. Review the upstream release/source and keep the package
behind this adapter; do not vendor the repository or use anti-bot bypass forks.
