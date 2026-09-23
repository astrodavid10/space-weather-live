# Engine snapshot — NOT YET VENDORED

This directory will hold a Digistar-free snapshot of the dome's `Engine/` (magneto/, dsexport/,
livecore/, webexport/, tools/, vendor/, tests/, docs/). Requested from the magneto-stormy session
on 2026-09-23; the request (contents, strip list, 14 questions) is section 9 of the project plan.

Until it arrives `python -m pipeline lookback` exits cleanly with
`last_attempt_status: engine_failed` and the published look-back keeps serving with its true age.

When it lands, record here:

- source repo + commit + date
- `physics_version` / `physics_tag` as render.py stamps them
- what was stripped
- local diffs (should be none)
