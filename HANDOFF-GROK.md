# Grok — resume here

Read [HANDOFF.md](HANDOFF.md) first. This is an unfinished LOCAL checkpoint,
not the deployed release. Do not deploy or describe it as finished.

Suggested focus: source review of scheduler v2 migration, civil-time/DST
recurrence, cancellation/timeout races, background-task lifecycle, SSH fleet
routing, webhook privacy, backup-preview tokens and deployment rollback.
The current code has not had feature tests or a complete static review.

Claude may own ConsoleTools/TUI and browser integration. Coordinate API
contracts and file ownership before editing concurrently. The browser is
still v1; the new console menu currently points to missing handlers. No real
webhook destination is configured. Do not send test alerts, power commands,
HID input, WOL packets or sample schedules. Latest user request was handoff
notes and a local checkpoint commit; feature testing remains deferred.
