# Changelog

## [1.1.0] - 2026-10-25

Final release. The StrideMate M.Tech project has finished; this version is complete and on hold.

### Fixed
- Replay skips broken lines (for example a recording cut off by Ctrl-C)
  instead of refusing to play, and a missing or empty recording is reported
  before the server starts.
- A replay whose first line has no valid time no longer waits for hours
  before the second frame.
- A reply from the device that is cut short, or is not a JSON object, no
  longer stops the data feed for good.
- Strings, true/false and NaN in a distance are treated as no reading
  instead of crashing the gait tracker.
- NaN values are sent as `null`, so the browser no longer drops those frames.
- `--rate 0`, `--speed 0` and a bad `--record` path give a clear error.
- Dashboard: after the server restarts with a new token, the page says the
  link no longer works instead of showing "reconnecting" forever. It retries
  with a growing delay, reloads history after a reconnect, and flags when no
  data has arrived for a few seconds.
- Dashboard: values that do not exist yet (cadence, stride CV, asymmetry
  before enough strides, missing load) show a dash instead of 0.

### Changed
- Dashboard: a clear connection status bar, round axis numbers, a time
  scale under each chart, an empty-chart message, time in the tooltip,
  per-leg stride counts and swing (excursion), and a table that scrolls on
  a phone instead of overflowing.

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
