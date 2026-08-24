# Local TikTok Publisher Integration

## Goal

Replace the paid Upload-Post dependency for the self-hosted workflow with a local TikTok publisher integrated into OpenShorts, while preserving the existing hosted/cloud social path unless intentionally changed later.

## Selected upstream candidate

Primary upstream to evaluate and pin: `wkaisertexas/tiktok-uploader`.

Why this candidate:

- MIT-licensed.
- Playwright-based browser automation.
- Supports cookies/session authentication.
- Supports descriptions/hashtags.
- Supports scheduling.
- Supports optional TikTok product IDs for eligible accounts.
- Has an established package/API surface so OpenShorts can integrate through a thin adapter instead of copying the upstream implementation.

Do not use captcha-bypass or anti-bot-evasion forks as the default integration.

## Integration branch

`feat/local-tiktok-publisher`

This branch is intentionally separate from subtitle/render-lifecycle remediation so the two streams can be tested independently before final integration.

## Target architecture

OpenShorts should own a narrow publisher contract rather than coupling UI/backend code directly to the upstream library.

Suggested flow:

```text
ResultCard / Post UI
        |
        v
OpenShorts backend publish API
        |
        v
Local TikTok publisher adapter
        |
        v
Dedicated Playwright publisher runtime
        |
        v
TikTok upload page
```

Prefer a dedicated publisher service/container if that keeps Chromium/Playwright dependencies isolated from the main OpenShorts backend. The main backend should send only the resolved current clip path plus non-secret posting options.

The publisher adapter should be replaceable. Upstream-specific imports, selectors, exceptions, and option translation must remain behind that adapter.

## Self-host behavior

For self-host mode:

- TikTok publishing must not require an Upload-Post API key.
- The existing Post UI should be adapted to use the local publisher when configured.
- Posting must use the latest authoritative edited MP4 for the clip.
- A stale clean clip, old archive, or pre-subtitle/pre-hook file must never be selected merely because it is easier to locate.
- Posting must never happen automatically as a side effect of clip generation.

Hosted/cloud behavior should remain unchanged unless a separate migration explicitly removes Upload-Post there.

## Authentication and secrets

First-time TikTok authentication should be manual. Persist only the resulting session/cookie material required by the upstream uploader.

Requirements:

- Session/cookie files live in a local mounted data directory outside git.
- Never commit cookies/session IDs.
- Never print cookies/session IDs.
- Never hash or partially reveal them in logs.
- Never return them in API responses.
- Never persist them in job metadata.
- Do not put server-owned TikTok session material into frontend localStorage.
- Publisher status endpoints may return only non-sensitive booleans/state such as `configured`, `session_valid`, or `login_required`.
- Do not implement captcha bypass or anti-bot circumvention.

## Publisher contract

The OpenShorts-owned adapter should expose semantics roughly equivalent to:

```python
publish_video(
    video_path,
    description,
    schedule_at=None,
    product_id=None,
    privacy=None,
    allow_comments=None,
    allow_duet=None,
    allow_stitch=None,
)
```

Do not bind the rest of OpenShorts to this exact Python signature if a cleaner internal model already exists. The important point is to keep one typed/validated boundary.

Return a normalized result such as:

- `success`
- `failed`
- `login_required`
- `rate_limited`
- `unknown`

If the upload outcome is ambiguous, return `unknown`. Do not blindly retry a possibly successful post and create duplicates.

## Product links

The selected upstream supports a TikTok product ID. Keep this optional.

- Only send a product ID when the user explicitly chose one.
- Eligibility is controlled by TikTok/account permissions.
- A product-link failure should surface clearly.
- Do not silently publish without the product when the user explicitly requested a product link unless the UI asks them to continue without it.

## Scheduling

Scheduling must be explicit and timezone-aware.

- Persist normalized scheduled time/state without secrets.
- Validate TikTok/upstream scheduling limits.
- Do not reinterpret local time silently.
- Surface rejected schedule windows clearly.

## Current-video invariant

This integration depends on the clip lifecycle being correct.

The publisher source must be the exact same current durable video that the user sees/downloads after edits:

```text
subtitle / hook / recut / reframe edits
        -> authoritative current server file
        -> Post uses that file
```

The local publisher integration must therefore be validated after the subtitle/current-file remediation is merged.

## Automated tests

Automated tests must not publish to TikTok.

Cover at least:

1. Publisher configured/unconfigured state without credential leakage.
2. Backend request validation.
3. Correct mapping of caption/hashtags/schedule/product ID.
4. Exact latest current MP4 selected for publishing.
5. Stale clean clip cannot win over current edited clip.
6. Adapter exception normalization.
7. `unknown` result does not auto-retry.
8. Login-expired state is surfaced.
9. Self-host Post flow does not require Upload-Post.
10. Hosted/cloud Upload-Post path remains unchanged.
11. Frontend Post UI tests/build.
12. Publisher service health check.

## Dry-run E2E

Before a real TikTok upload, add/use a dry-run adapter mode that records only non-secret metadata:

- basename/resolved identity of the selected current clip
- description
- schedule
- product ID presence/value if not secret

Use a real generated and edited OpenShorts clip and prove that the exact latest file reaches the publisher boundary.

No network publish should occur during this test.

## Real smoke test

A real TikTok upload is a separate explicit step and must only run after user approval.

For the smoke test:

1. Use one disposable clip.
2. Confirm the account/session is the intended test account.
3. Confirm caption and product-link settings before upload.
4. Run one upload only.
5. Verify the result in TikTok manually.
6. Do not implement automatic retries after an ambiguous result.

## Upstream maintenance

Pin a tested upstream release or commit. Document the pin in the integration code/config.

Do not vendor the whole upstream repository unless testing shows the package/dependency approach is insufficient. Keeping it behind one adapter makes future replacement easier when TikTok changes its UI.
