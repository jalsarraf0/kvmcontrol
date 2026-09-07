# Claude Code — resume here

Read [HANDOFF.md](HANDOFF.md) first. This is an unfinished LOCAL checkpoint,
not the deployed release. Do not deploy or describe it as finished.

Highest-priority integration gap: `atx_console.py` now references many new
ConsoleTools handlers that do not exist. Existing ConsoleTools still routes
requests locally. Finish selected-target routing before exposing fleet actions.
Then finish the TUI and v2 browser controls; preserve line-buffered SSH input,
clear target confirmations, NO_COLOR and reduced-motion behavior.

If Grok is working alongside you, agree on ownership: suggested split is
Claude frontend/ConsoleTools/TUI, Grok backend/deployment review. Neither
agent should overwrite the other's uncommitted changes. The user has deferred
feature tests. Latest request was handoff notes and a local checkpoint commit.
