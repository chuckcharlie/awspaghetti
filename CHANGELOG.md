# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0] - 2026-06-15

### Added
- Optional MQTT trigger gate via the `MQTT_TRIGGER_TOPIC` environment variable.
  When set, analysis only runs while the topic carries an "enabled" payload
  (e.g. `{"enabled": true}` or a truthy scalar like `true`/`1`/`enabled`/`yes`/`on`).
- `.gitignore` for Python bytecode and common editor/OS artifacts.

### Changed
- The MQTT client now subscribes to the trigger topic on connect and resubscribes
  on reconnect so retained messages are picked up.
- `process_frame()` short-circuits when the trigger gate is off, so `trigger.py`
  manual runs honor the same rule.
- README accuracy fixes: corrected the Test Mode container name and command (now
  uses `trigger.py`), and clarified the `TEST_MODE` description.

### Notes
- The trigger gate is fail-safe: if the broker is disconnected or no payload has
  been seen, analysis does not run.
- Behavior is unchanged when `MQTT_TRIGGER_TOPIC` is unset.

## [1.1.0]

### Added
- Multi-image series analysis: capture multiple frames at configurable intervals
  and analyze them together for better failure detection.
- Rapid verification pass (4 series, 3-of-4 confirmation) to reduce false positives.
- 15-minute cooldown after a confirmed failure to limit Bedrock API usage.
- Optional Discord notifications and optional MQTT status publishing.
- Automatic AWS credential reload and role re-assumption on token expiry.
