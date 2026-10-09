# Changelog

## [1.0.0] - 2026-10-07

First release.

### Added
- Live gait dashboard for the StrideMate exoskeleton: stride time, cadence,
  stride-time variability (CV), left/right asymmetry and excursion.
- Three data sources: a built-in demo (no hardware needed), a live StrideMate
  right leg over HTTP, and replay of a `.jsonl` recording.
- `--record` saves every frame so a session can be played back later.
- `tunnel.sh`: one command to share the dashboard on a public HTTPS link
  through a Cloudflare quick tunnel. Every request needs a random access token.
- Read-only by design: there is no route that can control the motors.
- Unit tests, an end-to-end smoke test, and CI on every push.
- `GUIDE.md`: a plain-English tour of the code.

### Fixed
- `tunnel.sh` now works on Linux as well as macOS (`mktemp`).
