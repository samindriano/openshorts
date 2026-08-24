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

TikTok scheduling requires an explicit timezone, a time 20 minutes to 10 days
ahead, and a five-minute boundary. Product IDs are forwarded when supplied;
because upstream does not provide an independent product-link confirmation,
that live result is conservatively classified as `unknown` instead of claiming
success.

The existing `/api/social/post` Upload-Post route is unchanged for hosted mode
and for existing Instagram/YouTube workflows. This local adapter is TikTok
only and does not use Upload-Post.
