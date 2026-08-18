# Goal: atx-console-optimize

status: complete
phase: 6
started: 2026-08-18
completed: 2026-08-18

## Result
atx_console.py optimized: data-driven MENU_ITEMS table (replaces
hand-written menu() strings + separate dispatch dict), cached
gradient color tables (rule()/progress() no longer recompute RGB
interpolation per character per call), bracket-style risk badges
([READ]/[START]/[ACPI]/[DANGER]/[HARD]/[RAW]/[RAW-HOLD])
dynamically right-aligned to MENU_WIDTH=72 regardless of hint
length, POWER / RAW-INFO section headings. 27 unittest cases, all
passing. Verified on real amarillokvm hardware (real pty via tmux
and piped/no-pty SSH), including a live status query against the
real board.

Two independent AI passes (mine + Codex/gpt-5.6-sol from the same
brief) converged on the same core architecture; merged the
better-engineered pieces of each (Codex's gradient-table caching and
dynamic badge alignment; kept my simpler section-based number
coloring over Codex's per-badge coloring, which mixed signals within
a section).

/code-review (high effort, non-ultra — ultra requires direct user
invocation, it's billed) caught two real bugs before this was called
done: badge alignment was off by 2 columns (leading indent omitted
from the width calculation) and heading/number section colors were
duplicated expressions that could drift apart. Both fixed and
covered by new regression tests (test_every_menu_row_aligns_to_menu_width,
test_section_heading_and_item_numbers_share_one_color).

Mirrored to kvmcontrol (public) and kvmcontrol-private, both currently
uncommitted per the user's explicit instruction — they will commit and
redeploy to amarillokvm/dominus-kvm themselves.
