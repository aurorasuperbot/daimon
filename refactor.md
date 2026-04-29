# DAIMON UI Refactor — terminal → pywebview + HTML/CSS

Status: **proposed, not started**
Author: Claude (Opus 4.7) + Santiago
Date: 2026-04-28
Estimated effort: ~11 working days
Estimated LOC delta: −5,700 / +2,500

---

## 1. Why this exists

The current rendering stack — bundled WezTerm + KGP/iTerm2 inline image
protocol + a custom cell-based widget framework (`daimon.ui`) — has hit
a wall:

* **Image rendering is fragile.** WezTerm's KGP path is broken on Windows
  OpenGL fallback; we work around it with iTerm2 inline images, which
  also drop frames when the cursor is hidden, when the alt buffer is
  active, when text overlaps image cells, when a screen resizes, or
  when a child process stomps the terminal.
* **Cross-terminal portability is fiction.** The whole stack only works
  inside the *bundled* WezTerm. Players' own terminals — Windows
  Terminal, default Linux terminals, Apple Terminal — render nothing.
* **Visual ceiling is low.** Even when everything works, it looks like
  a terminal. Gradients, anti-aliasing, smooth animation, particle
  effects, real fonts — all extremely expensive to fake in cells.
* **Bug surface is too wide.** Cell-drift math, cursor visibility
  toggling, per-overlay dedupe, alt-buffer protection, manual
  word-wrapping, sub-cell block characters — every screen change
  trips one of them.
* **Install footprint is heavy.** A bundled ~50 MB WezTerm binary plus
  a locked Lua config is the price of entry, just to render PNGs.

We've spent weeks fighting the medium. The medium is wrong for the
product.

---

## 2. Goals — what success looks like

1. **Card art renders crisply and predictably** on macOS, Windows, and
   Linux with no terminal-specific magic.
2. **Animations are smooth** (60 fps target): card flip, particle
   bursts, scene transitions, focus pulses.
3. **Visual ceiling is "indie card game"**, not "polished TUI".
4. **Install is one command for agents**, two for humans-bootstrapping-from-zero.
5. **Agents can drive the full install → spawn flow** with no
   prompts, no clicks, no human in the loop.
6. **`daimon menu` returns immediately**; the window survives the
   parent shell exiting.
7. **Headless CLI + MCP tools keep working unchanged** — agents can
   continue to play via `dm_pull`, `dm_match`, etc., independent of
   any window.
8. **Future paths stay open**: multiplayer, spectator mode, mobile,
   replay viewer — all become small additions, not rearchitectures.

---

## 3. Non-negotiables — rules we will not break

These are constraints. If a design choice violates one, the choice is
wrong, not the rule.

| # | Rule | Why |
|---|---|---|
| **N1** | Cross-platform on macOS 11+, Windows 10 1809+, Linux (mainstream distros). | DAIMON is for any agent operator; Mac-only or Linux-only is unacceptable. |
| **N2** | Install is **at most two commands** for a clean machine. | Agent UX. Three is too many; one is the limit if uv is already installed. |
| **N3** | Install runs **with zero interactive prompts**. | Agents can't answer Y/N. |
| **N4** | Install is **idempotent**. | Agents will re-run it. So will users. So will CI. |
| **N5** | `daimon menu` **detaches the window from the launching shell**. | Agents must be free to do other work; users must be able to close their terminal without killing the game. |
| **N6** | The MCP server, mining hook, and headless CLI **continue to work unchanged**. | Existing integrations must not break. |
| **N7** | The app **never requires an internet connection at runtime** after first install. | Pull, match, collection — all offline-capable. (Art pack download is the one exception, and only on first run.) |
| **N8** | The app **never depends on a user-installed browser being present**. | Webview engines are bundled with the OS or auto-installable; we never call `webbrowser.open()` as the primary path. |
| **N9** | The Python core (engine, MCP, mining, ledger, identity, catalog) is **untouched** by this refactor. | Risk reduction. Game logic stays correct. |
| **N10** | All existing tests for the Python core **continue to pass** at every phase boundary. | Same. |
| **N11** | The frontend is **vendored as static files** in the wheel. | No npm at install time. No network at install time beyond pip itself. |
| **N12** | The runtime is **single-process** (Python with pywebview window + FastAPI in a thread). | Two-process designs invite race conditions and zombie processes. |
| **N13** | **No telemetry, no analytics, no phone-home** in the new stack. | Privacy posture matches the current product. |

---

## 4. The new stack

### 4.1 Components

| Layer | Choice | Rationale |
|---|---|---|
| **Window shell** | pywebview | Pure Python, ~200 KB, uses OS native webview (Edge WebView2 / WKWebView / GTK WebKit). No bundled Chromium bloat. |
| **Backend** | FastAPI + Uvicorn | Mature, async, low overhead, ~5 MB. Bound to `127.0.0.1` on a free port. Runs in a daemon thread inside the window's process. |
| **Frontend framework** | Vanilla JS + Web Components | Zero build step at install time. ES modules + custom elements ship as static files. Can swap to Svelte/React later if complexity demands it. |
| **Styling** | Modern CSS (variables, nesting, `@layer`) | No preprocessor needed. CSS variables drive the theme. |
| **State sync** | REST (queries + mutations) + WebSocket (live updates) | Simple, well-understood. WebSocket pushes balance/receipt updates from background mining. |
| **Asset delivery** | FastAPI static routes | Card art served from `/art/{card_id}` (existing art pack on disk). Fonts + UI sprites bundled in `daimon/web/static/`. |
| **Distribution** | `uv tool install daimon` | Astral's uv is the modern Python tool installer. Single binary, ~5 MB, bootstraps Python on demand. |

### 4.2 Process model

```
┌────────────── User shell / agent shell ─────────────┐
│  $ daimon menu                                      │
│      → checks lock file at ~/.daimon/run/menu.lock  │
│      → if running: focus window via OS API; exit 0  │
│      → if not:    Popen(daemon, detached); exit 0   │
└──────────────────┬──────────────────────────────────┘
                   │ detached spawn
┌──────────────────▼──────────────────────────────────┐
│  Daemon process (long-running, headless to caller)  │
│   ├─ Bootstrap (silent, idempotent — see §6)        │
│   ├─ FastAPI in daemon thread @ 127.0.0.1:N         │
│   ├─ pywebview window in main thread → URL          │
│   ├─ Lock file: { pid, port, started_at }           │
│   └─ Window close → graceful shutdown → cleanup     │
└─────────────────────────────────────────────────────┘
```

### 4.3 File layout (target)

```
daimon/
├── __init__.py
├── cli.py                          # CLI entry, dispatch
├── bootstrap.py                    # NEW — silent first-run setup
├── daemon/
│   ├── __init__.py
│   ├── spawn.py                    # NEW — detached spawn helpers
│   └── lock.py                     # NEW — single-instance lock + focus
├── web/
│   ├── __init__.py
│   ├── server.py                   # NEW — FastAPI app factory
│   ├── routes.py                   # NEW — REST + WS handlers
│   ├── live.py                     # NEW — WebSocket broadcast layer
│   └── static/                     # NEW — pre-built frontend bundle
│       ├── index.html
│       ├── app.css                 # theme + base styles
│       ├── app.js                  # router + bootstrap
│       ├── components/             # Web Components
│       │   ├── card-art.js
│       │   ├── rarity-chip.js
│       │   ├── stat-bar.js
│       │   ├── pull-reveal.js
│       │   └── …
│       ├── screens/
│       │   ├── menu.js
│       │   ├── shop.js
│       │   ├── collection.js
│       │   ├── loadout.js
│       │   └── pull.js
│       ├── fonts/                  # 1-2 TTF/WOFF2 files
│       └── audio/                  # optional sound effects (deferred)
├── engine/                         # KEEP (untouched)
├── mcp/                            # KEEP (untouched)
├── mining/                         # KEEP (untouched)
├── identity/                       # KEEP (untouched)
├── catalog/                        # KEEP (untouched)
├── pulls/                          # KEEP (untouched)
├── shop/                           # KEEP (data + logic untouched)
├── update/                         # KEEP (art pack download)
└── skills/
    └── install/
        └── SKILL.md                # SHRINKS to ~15 lines
```

### 4.4 What dies

| Path | LOC (approx) | Why it goes |
|---|---|---|
| `daimon/ui/` (entire framework — Frame, HBox, FixedRows, FixedCols, Pad, Panel, Widget, Screen, BindingTable, theme, anim, chips, stats, nav, hero_art, backgrounds, widgets, app, screen, bindings, events, layout, frame) | ~1,700 | Replaced by HTML/CSS/JS components. |
| `daimon/render/` (KGP, iTerm2, wezterm_bundle, art compositor terminal-side) | ~1,200 | Image rendering moves to the browser engine. |
| `daimon/play/menu_ui.py`, `shop_ui.py`, `collection_ui.py`, `loadout_editor.py`, `pull_ui.py` | ~3,000 | Replaced by frontend screens. |
| `daimon/play/hud.py`, `play/hud/`, `play/tile.py`, `play/art_render.py`, `play/tui_style.py`, `play/screenshot.py`, `play/card_tile.py` | ~1,800 | All terminal-specific. |
| `daimon/cli.py` commands: `install`, `onboard`, `init`, `play`, `play-render` | ~400 | Folded into bootstrap or removed. |
| `tests/test_ui_*.py`, `test_kgp_*.py`, `test_*_ui.py`, `test_pull_ui.py`, `test_composited_tile.py`, `test_home_card.py` | ~2,500 | Replaced by API + frontend tests. |
| Bundled WezTerm binary + `wezterm.lua` + install assets | ~50 MB binary, ~400 LOC config | Not needed. |

**Approx total deletion: ~10,600 LOC + 50 MB binary asset.**

### 4.5 What survives

| Path | Notes |
|---|---|
| `daimon/engine/` | Combat resolution, card definitions, loadout validation. |
| `daimon/mcp/` | All `dm_*` tools. Behaviour unchanged. |
| `daimon/mining/` | Hook script, ledger, receipts. |
| `daimon/identity/` | BIP39, ed25519, keystore. |
| `daimon/catalog/` | Card definitions loader. |
| `daimon/pulls/` | `perform_pull()` and friends. |
| `daimon/shop/` | Rotation, listings, purchase logic. |
| `daimon/update/` | Art pack download + version pinning. |
| `daimon/cli.py` (the headless commands) | `pull`, `mine`, `match`, `loadout list`, `loadout new`, `loadout show`, `collection list`, etc. — all keep their `--json` modes. |
| All tests for the above | Continue to pass at every phase boundary (per N10). |

---

## 5. Distribution + install

### 5.1 The install command (agents and humans)

```bash
uv tool install daimon
```

That's the entire install. uv:
- Creates an isolated venv for daimon in `~/.local/share/uv/tools/daimon/`
- Adds a launcher to `~/.local/bin/daimon` (Mac/Linux) or `%USERPROFILE%\.local\bin\daimon.exe` (Windows)
- Adds the launcher dir to PATH on first install (or prints `uv tool ensurepath`)

### 5.2 Bootstrap-uv (only if uv is missing)

```bash
# Mac/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
irm https://astral.sh/uv/install.ps1 | iex
```

Both scripts are signed by Astral and widely used. They install uv to a
known location and add it to PATH. Idempotent.

### 5.3 What `uv tool install daimon` actually ships

- Python source (engine, server, CLI, MCP tools, bootstrap)
- pywebview as a dep (~200 KB)
- FastAPI + Uvicorn + websockets as deps (~5 MB)
- Pre-built frontend bundle in `daimon/web/static/` (~500 KB)
- 1-2 bundled fonts in `daimon/web/static/fonts/` (~200 KB)
- **Card art is NOT bundled.** Downloaded on first run via existing
  art pack mechanism (~50 MB). Bundling would balloon the wheel; the
  art pack changes independently of daimon code anyway.

### 5.4 The skill doc (target — final form)

```markdown
---
name: install
description: Install and launch DAIMON — the agentic card game.
---

# Install DAIMON

```bash
uv tool install daimon    # if uv missing: see Bootstrap below
daimon menu               # opens game window for the user; returns immediately
```

That's it. First `daimon menu` auto-downloads the card art and wires
the Claude Code mining hook silently. Re-running is safe.

## Headless ops (no window — for agents)

```bash
daimon pull --json              # pull a card
daimon mine status --json       # check balance
daimon match <opponent> --json  # run a match
```

## Bootstrap uv (only if missing)

- Mac/Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- Windows:   `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`

## If something is wrong

```bash
daimon doctor          # diagnostic — prints what's broken + how to fix
```
```

15 lines of skill, fully self-contained.

---

## 6. The bootstrap contract

`daimon/bootstrap.py` is invoked at the top of `cli_entry()` for **every**
command. It is silent on success and idempotent.

### 6.1 What it checks/does

| Check | Action if missing |
|---|---|
| `~/.daimon/.bootstrapped` marker file | (none — this is the marker that says "done") |
| `~/.daimon/` directory tree | Create `~/.daimon/{run,cache,art,loadouts,log}` |
| Identity at `~/.daimon/identity.json` | Run silent `init` — generate ed25519 keypair, write keystore |
| Art pack at `~/.daimon/art/v1_alpha/manifest.json` | Run silent `update` — download art pack from GitHub Releases |
| Claude Code MCP server config in `~/.claude/claude_desktop_config.json` | Add `daimon` server entry (additive, never overwrites other entries) |
| Claude Code PostToolUse hook in `~/.claude/settings.json` | Add `daimon mine receipt` hook (additive) |
| Webview engine availability (Linux only) | Detect missing `webkit2gtk-4.0`/`-4.1`; print actionable error with the apt/dnf one-liner for the detected distro |
| Write marker file `~/.daimon/.bootstrapped` with version + timestamp | (final step) |

### 6.2 Bootstrap rules

- **Silent on success.** Never prints anything if everything is fine.
- **Single error message on failure.** Prints what failed + how to fix it.
- **Never fatal for headless ops.** Headless commands (`daimon pull --json`)
  must still work even if MCP wiring failed — those are independent.
- **Re-runs are no-ops** when the marker is present and matches the
  installed daimon version.
- **Version skew triggers re-bootstrap.** If the marker says v1.2.0 but
  installed daimon is v1.3.0, re-run bootstrap to catch any new wiring.

### 6.3 What bootstrap does **NOT** do

- Spawn the window. (That's `daimon menu`.)
- Open the browser. (Never.)
- Make network calls beyond the art pack download.
- Modify any file outside `~/.daimon/` and `~/.claude/`.
- Run any user-provided code.

---

## 7. The detached spawn contract

`daimon menu` is the canonical entry point.

### 7.1 Behaviour

1. Run bootstrap (silent, see §6).
2. Read lock file `~/.daimon/run/menu.lock`.
   - If lock exists and PID is alive: focus that window via OS API,
     print `daimon already running (pid N, port M)`, exit 0.
   - Else: continue.
3. Spawn the daemon process detached:
   - Mac/Linux: `subprocess.Popen(..., start_new_session=True, stdin=DEVNULL, stdout=DEVNULL, stderr=DEVNULL, close_fds=True)`
   - Windows: `subprocess.Popen(..., creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP, ...)`
4. Wait up to 3 s for the daemon to write its lock file (PID + port).
5. Print `daimon running at http://127.0.0.1:N (pid M)`.
6. Exit 0.

### 7.2 Daemon process behaviour (`daimon._daemon_internal`)

1. Pick a free port on `127.0.0.1`.
2. Start FastAPI on that port in a daemon thread.
3. Write lock file: `{ "pid": …, "port": …, "started_at": …, "version": … }`.
4. Open pywebview window pointed at `http://127.0.0.1:N/`.
5. Block on the window event loop.
6. On window close:
   - Send shutdown signal to FastAPI server.
   - Wait up to 5 s for graceful shutdown.
   - Remove lock file.
   - Exit 0.

### 7.3 Window-focus on existing-instance

| Platform | Implementation |
|---|---|
| Windows | `win32gui.FindWindow` + `SetForegroundWindow` (via pywin32, optional dep — falls back to "already running" message) |
| macOS | `osascript -e 'tell application "Daimon" to activate'` (works because pywebview sets the app name) |
| Linux | `wmctrl -a Daimon` if available, else fall back |

If focus fails, print `daimon already running on port M — open http://127.0.0.1:M` and exit 0. Never crash on focus failure.

### 7.4 Crash behaviour

If the daemon process crashes:
- pywebview window disappears (process gone)
- Lock file is stale
- Next `daimon menu` detects stale lock (PID dead), removes it, spawns fresh

---

## 8. Frontend architecture

### 8.1 Stack

- **HTML** — single `index.html`, hash-routed (`#menu`, `#shop`, `#collection`, `#loadout`, `#pull`).
- **CSS** — single `app.css` with CSS variables for the theme, modern features (nesting, `@layer`).
- **JS** — single `app.js` as the router/bootstrap; per-screen modules in `screens/`; reusable components in `components/`.
- **No framework.** Web Components (Custom Elements + Shadow DOM) for encapsulated widgets.
- **No build step at install time.** Files ship as-is in the wheel.
- **Optional dev build step** via Vite for hot-reload during development.

### 8.2 Theme — CSS variables

```css
:root {
  --bg-deep: #0a0c18;
  --bg-panel: #10142a;
  --bg-raised: #181e30;
  --accent: #f0c458;
  --accent-bright: #ffdc78;
  --accent-deep: #b48220;
  --text: #ffffff;
  --text-muted: #9b9b9b;
  --text-dim: #5f5f5f;
  --danger: #d93636;
  --success: #6cd96c;
  --border-rest: var(--text-dim);
  --border-hero: var(--accent);
  --gap-tight: 0.5rem;
  --gap-normal: 1rem;
  --gap-loose: 1.5rem;
}
```

Same palette as the current TUI theme — moved to CSS so it cascades.

### 8.3 Components (Web Components)

| Component | Purpose |
|---|---|
| `<card-art card-id="aegis_lion" size="large">` | Renders a card art image with rarity-tinted border + glow on hover. Lazy-loads the PNG. |
| `<rarity-chip rarity="legendary">` | Glyph + label, color-coded. |
| `<element-chip element="FIRE">` | Same but for elements. |
| `<stat-bar label="ATK" value="7" max="10">` | Sparkline-style bar with iconified label. |
| `<hp-bar current="22" max="30">` | Dual-tone bar with damage tail + danger color. |
| `<page-dots current="2" total="5">` | `●●◌◌◌` + (3 / 5). |
| `<pull-reveal card-data="…">` | The 4-phase animated reveal. CSS keyframes drive it. |
| `<spinner kind="braille">` | Animated loading indicator. |
| `<card-grid layout="3x2">` | Reusable tile grid container. |

### 8.4 Routing

Hash-routed SPA:
- `#menu` → menu screen (default)
- `#shop` → shop
- `#collection` → collection
- `#loadout/<name>` → loadout editor for that name
- `#pull` → pull reveal (after a successful POST /api/pull)
- `#match/<id>` → match flow

Browser back/forward work natively. No history-API gymnastics needed.

### 8.5 Live updates via WebSocket

`/ws` pushes JSON messages:
```json
{ "type": "balance", "value": 1700 }
{ "type": "receipt", "card_id": "aegis_lion", "rarity": "legendary" }
{ "type": "shop_rotated" }
{ "type": "art_pack_progress", "downloaded": 12, "total": 100 }
```

Frontend dispatches these to whichever screen is mounted; off-screen state still updates in the background.

---

## 9. Backend API surface

### 9.1 REST endpoints

| Method | Path | Purpose | Returns |
|---|---|---|---|
| GET | `/api/home` | Menu screen data | `dm_home()` JSON |
| POST | `/api/pull` | Spend currency, pull a card | Pull receipt |
| GET | `/api/shop` | Current rotation + balance | Shop state |
| POST | `/api/shop/buy/{slot_idx}` | Purchase a slot | Receipt + new balance |
| GET | `/api/collection` | Owned cards + counts | Collection state |
| GET | `/api/loadouts` | List saved loadouts | Array of names |
| GET | `/api/loadout/{name}` | One loadout | EditorView JSON |
| POST | `/api/loadout/{name}` | Save a loadout | { ok, message } |
| DELETE | `/api/loadout/{name}` | Delete a loadout | { ok } |
| GET | `/api/match/recommended` | Suggested NPC | NPC payload |
| POST | `/api/match/start` | Start a match | Match ID |
| GET | `/api/match/{id}` | Match state | Match snapshot |
| POST | `/api/match/{id}/action` | Submit a turn action | Updated snapshot |
| GET | `/api/identity` | Public identity info | { pubkey, handle } |

### 9.2 WebSocket

- `/ws` — single bidirectional channel for live updates.
- Server pushes events as they happen (mining receipts, shop rotation, art pack progress).
- Client doesn't push commands here — those go through REST.

### 9.3 Static routes

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | `index.html` |
| GET | `/app.css`, `/app.js`, `/components/*`, `/screens/*` | Frontend assets |
| GET | `/art/{card_id}` | Card art PNG (variant-aware via equipped skin) |
| GET | `/art/{card_id}/{variant_id}` | Specific variant |
| GET | `/fonts/{name}` | Bundled font |

### 9.4 Security

- Bind to `127.0.0.1` only. Never `0.0.0.0`.
- No CORS — the frontend is served from the same origin.
- No auth — local single-user app.
- Optionally generate a session token at startup, frontend reads it
  from a meta tag, sends as header. (Defer; not needed for V1.)

---

## 10. Asset pipeline

### 10.1 Card art

- Existing `daimon update` mechanism stays untouched.
- Art lives at `~/.daimon/art/v1_alpha/<card_id>/variants/<variant>.png`.
- Server route `/art/{card_id}` resolves via existing `art_path_for()` (equipped-skin-aware).
- 24h cache headers; immutable per (card_id, variant_id).

### 10.2 Frontend bundle

- Source files committed under `daimon/web/static/`.
- For dev: served as-is (no minification needed for localhost).
- For release: optional minification step in CI (esbuild). Output committed back to repo so `pip install` always gets a tested bundle.
- Size budget: **5 MB total wheel**, **500 KB frontend bundle excluding fonts**.

### 10.3 Fonts

- Bundle 1-2 TTF files: a body sans-serif and a display weight.
- Candidates: Inter (body), Cinzel or similar (display titles).
- `@font-face` declarations in `app.css`, files in `static/fonts/`.

### 10.4 Sound effects (deferred)

- Optional, opt-in via setting.
- Bundle 5-10 short MP3/OGG samples in `static/audio/`.
- Loaded via Web Audio API.
- Defer to post-V1.

---

## 11. Testing strategy

### 11.1 Untouched (continue to pass at every phase boundary)

- Engine tests
- Mining + ledger tests
- Identity tests
- Catalog tests
- MCP tool tests
- Headless CLI tests (`daimon pull --json` etc.)

### 11.2 New

| Layer | Tool | What it covers |
|---|---|---|
| Bootstrap | pytest | Idempotency, marker file, missing-art-pack recovery, MCP wiring additive merge |
| Spawn / lock | pytest with subprocess + tmpdir | Single-instance enforcement, stale-lock cleanup, focus-existing path |
| FastAPI routes | pytest + httpx test client | Each REST endpoint returns the right shape, error cases |
| WebSocket | pytest-asyncio + websockets test client | Connect, receive a balance event, disconnect cleanly |
| Frontend smoke | Playwright | Open menu URL, screenshot, assert no JS errors, click PULL button, verify pull-reveal mounts |
| Visual regression | Playwright snapshot | One snapshot per screen per OS; regenerate on intentional changes |
| E2E | pytest spawning a real daemon | `daimon menu` → window opens → frontend renders → REST round-trips work → window close → process exits |

### 11.3 CI matrix

- **OS**: ubuntu-latest, macos-latest, windows-latest
- **Python**: 3.10, 3.11, 3.12, 3.13
- **Steps per job**: lint → unit → API → smoke → headless-CLI
- **Frontend smoke + visual regression**: ubuntu-latest only (Playwright Chromium); cross-OS visual checks come later if needed
- Total CI budget: **under 10 min per push**

---

## 12. CI / release pipeline

### 12.1 Per-push CI

1. Lint (ruff, mypy)
2. Unit tests (engine, mining, identity, …)
3. API tests (FastAPI routes, WebSocket)
4. Bootstrap tests
5. Headless CLI tests
6. Frontend smoke + visual regression (Linux only)

### 12.2 Per-tag release

1. All of the above, three OSes
2. Build wheel
3. Optional: minify frontend bundle (esbuild)
4. Publish wheel to PyPI
5. Cut GitHub release with changelog

### 12.3 What gets shipped

- One wheel per release (universal — no per-OS builds; pywebview pulls in OS-specific deps)
- Frontend bundle baked into the wheel
- No bundled binaries (no WezTerm, no PyInstaller `.exe`/`.app` for V1)

### 12.4 Versioning

- Semver. Refactor lands as **v2.0.0** (breaking — terminal commands removed).
- v1.x stays on the legacy branch, no further development.

---

## 13. Phased migration plan

Each phase is **independently shippable** in the sense that the test
suite passes and the codebase compiles. We do NOT delete the legacy
code until Phase 4 — that way every prior phase has a working
fallback.

### Phase 0 — Foundations (1 day)

- Branch: `refactor/web-stack`
- Add deps to `pyproject.toml`: `pywebview`, `fastapi`, `uvicorn`, `websockets`
- Create `daimon/web/`, `daimon/daemon/`, `daimon/bootstrap.py` skeletons
- Wire hidden CLI command `daimon _daemon_internal` (the spawned process entry)
- Implement: pywebview window opens with hardcoded "Hello DAIMON" HTML
- Implement: detached spawn from `daimon menu` (returns immediately)
- Implement: lock file write/read/cleanup
- Tests: spawn test (Unix + Windows), lock test
- **Acceptance**: `daimon menu` opens a window with "Hello DAIMON", returns to shell immediately, window survives shell exit. Tests pass on all 3 OSes in CI.

### Phase 1 — Bootstrap + minimal menu (2 days)

- Implement `daimon/bootstrap.py` (silent setup, idempotent)
- Wire bootstrap into `cli_entry()` for every command
- Implement `/api/home` endpoint (calls existing `dm_home()`)
- Implement static file serving + `/art/{card_id}` route
- Build `index.html` + `app.css` + `app.js` skeleton (router)
- Build menu screen in HTML/CSS:
  - BigText "DAIMON" title (Cinzel font)
  - Identity strip
  - Hero CTA
  - 5 action buttons (PULL/MATCH/LOADOUTS/COLLECTION/SHOP)
  - Currency strip with progress bar
  - Quests + activity panel
  - Footer
- Hero card art panel (right side) — real PNG via `<card-art>` component
- Theme CSS: gold-on-navy palette + typography
- Action buttons fire fetch() calls (placeholder POST endpoints — real logic in Phase 2)
- Tests: bootstrap idempotency, `/api/home` response, frontend smoke (Playwright opens menu, asserts buttons present)
- **Acceptance**: `daimon menu` opens a window with the menu screen, looks at least as good as the current TUI, art renders, buttons hover/click.

### Phase 2 — Shop + Collection + Pull (3 days)

- Endpoints:
  - `GET /api/shop`, `POST /api/shop/buy/{slot}`
  - `GET /api/collection`
  - `POST /api/pull`
- Build shop screen:
  - 6-tile grid (3×2)
  - Hero detail panel (rarity chip, stat bars, cost chip / OWNED badge)
  - BUY button with affordability state
- Build collection screen:
  - Paged grid (4×2)
  - Hero detail panel (chips, stat bars, flavor, RULE)
  - Page dots
  - Sort/filter chips
- Build pull reveal screen — **the headline polish moment**:
  - 4-phase CSS animation:
    - DRAW (0–900ms): face-down silhouette, shimmer, shuffle spinner
    - TENSION (900–1500ms): rarity hint pulses in
    - REVEAL (1500–2200ms): card flips via CSS 3D transform, particle burst via CSS keyframes
    - SETTLED: full hero panel + "PRESS SPACE" CTA
  - Skip on SPACE/ENTER
- WebSocket `/ws` wired for live balance updates after pull/buy
- Tests: shop endpoint round-trip, pull endpoint, frontend smoke for each screen
- **Acceptance**: pull a card → see real animation. Buy from shop → balance updates live. Browse collection.

### Phase 3 — Loadout editor + Match flow (2 days)

- Endpoints:
  - `GET /api/loadouts`, `GET /api/loadout/{name}`, `POST /api/loadout/{name}`, `DELETE /api/loadout/{name}`
  - `GET /api/match/recommended`, `POST /api/match/start`, `GET /api/match/{id}`, `POST /api/match/{id}/action`
- Build loadout editor:
  - Catalog grid (left, paged)
  - 6-slot loadout (right)
  - HTML5 drag-drop (catalog → slot)
  - Live validation chip (READY / NEED 2 / DUPE)
  - Save / Quit buttons
- Build match flow:
  - Opponent picker
  - Match results screen (win/loss + receipt)
- Tests: loadout CRUD round-trip, match start, frontend smoke
- **Acceptance**: full flow from menu → pick loadout → match → return to menu works.

### Phase 4 — Cleanup (1 day)

- Delete `daimon/ui/` entirely.
- Delete `daimon/render/` (KGP, iTerm2, wezterm_bundle).
- Delete `daimon/play/menu_ui.py`, `shop_ui.py`, `collection_ui.py`, `loadout_editor.py`, `pull_ui.py`, `hud.py`, `play/hud/`, `play/tile.py`, `play/art_render.py`, `play/tui_style.py`, `play/screenshot.py`, `play/card_tile.py`.
- Delete CLI commands: `install`, `onboard`, `init`, `play`, `play-render`.
- Delete WezTerm bundle from release artifacts.
- Delete tests for removed modules: `tests/test_ui_*.py`, `tests/test_kgp_*.py`, `tests/test_*_ui.py`, `tests/test_pull_ui.py`, `tests/test_composited_tile.py`, `tests/test_home_card.py`.
- Update `daimon/skills/install/SKILL.md` to the 15-line version.
- Update `README.md` and `pyproject.toml` (description, dependencies).
- Bump major version: `2.0.0`.
- **Acceptance**: full test suite passes, repo is ~5,700 LOC lighter, no terminal-rendering code remains.

### Phase 5 — Polish + ship (2 days)

- HiDPI handling (pywebview supports it; verify on retina + 4K Win)
- Linux fallback (browser via `webbrowser.open()` if no webview engine)
- Windows WebView2 install bootstrapper (auto-install on first run if missing)
- Smooth scene transitions (CSS transitions on hash change)
- Sound effects (optional, opt-in)
- Cross-platform smoke tests in CI matrix (3 OSes)
- Tag `v2.0.0`, push to PyPI, cut GitHub release with migration guide
- **Acceptance**: real users on Mac/Win/Linux can `uv tool install daimon && daimon menu` and it just works.

### Total

**~11 working days.** Significantly less than the alternative of continuing
to fight WezTerm + cell math indefinitely.

---

## 14. Risk register

| # | Risk | Probability | Impact | Mitigation |
|---|---|---|---|---|
| R1 | Linux `webkit2gtk` missing on minimal installs | Medium | Medium | Detect at startup, fall back to `webbrowser.open()` silently, document the apt/dnf one-liner in `daimon doctor` output |
| R2 | Windows WebView2 missing on old Win10 | Low (~5%) | Medium | Auto-launch MS bootstrapper, one-click install |
| R3 | macOS WKWebView feature gaps on macOS 10.x | Low | Low | Min macOS 11 (covers 95%+); print min-version error otherwise |
| R4 | Detached spawn fails on some platforms | Low | High | CI matrix tests on all 3 OSes; fallback: foreground spawn with --foreground flag |
| R5 | FastAPI startup latency makes `daimon menu` feel slow | Low | Low | Measure; should be <200ms; progress indicator if longer |
| R6 | Frontend bundle bloats over time | Medium | Low | Size budget enforced in CI (5MB wheel cap) |
| R7 | Webview engines render differently → visual bugs per OS | Medium | Medium | Stick to widely-supported CSS; visual regression CI catches drift |
| R8 | pywebview maintenance slows / project dies | Low | High | Escape hatch: switch to CEF Python (bundled Chromium); or to Tauri (Rust shell). Both can host the same FastAPI + frontend unchanged |
| R9 | Multi-instance confusion (two `daimon menu` calls) | Medium | Low | Lock file + focus-existing pattern; never spawn second daemon |
| R10 | Mining hook keeps writing while window is open → race conditions | Medium | Medium | Hook writes are atomic file appends; server polls + WebSocket-broadcasts; no shared in-memory state |
| R11 | First-run art pack download blocks UI for ~30s | High | Low | Show "loading…" screen with progress bar from WebSocket events |
| R12 | uv not available + curl-pipe-sh blocked by user policy | Low | Medium | Document `pip install daimon` as fallback (works with system Python) |
| R13 | PyPI publish breaks (wheel format issue, etc.) | Low | High | Test wheel build in CI; install from wheel in CI before publish |
| R14 | Existing v1 users lose access on upgrade | High | Medium | v2 ships as new major; document migration; v1 keystore + ledger files are forward-compatible |
| R15 | Skill doc gets out of sync with reality | Medium | Medium | One source of truth: `daimon/skills/install/SKILL.md`. CI lints it for command examples that don't exist |

---

## 15. Rollback plan

If the refactor stalls or fails before Phase 4:

- The legacy code is still present on `main` — phases 0–3 are pure
  additions, no deletions. Just don't merge `refactor/web-stack`.
- v1.x continues to be released from `main` with bug fixes.

If Phase 4 (deletion) lands and a critical bug surfaces:

- Revert the deletion commit, restoring legacy `daimon.ui` / `*_ui.py`.
- Both stacks coexist in the codebase temporarily (some bloat but
  it's recoverable).
- Fix the new stack, re-delete in a follow-up.

If pywebview itself becomes a blocker post-launch:

- The escape hatches in R8 are real. CEF Python and Tauri can host
  the same FastAPI + frontend with a few hundred LOC of glue.
- The investment in HTML/CSS/JS is preserved either way.

---

## 16. Decision log — locking in choices we already made

These are settled. Re-litigation requires explicit user input, not agent decision.

| # | Decision | Date | Notes |
|---|---|---|---|
| D1 | Stack = pywebview + FastAPI + vanilla JS + Web Components | 2026-04-28 | Beats pygame-ce on layout-heavy UI; beats Tauri/Electron on Python integration; beats CEF on size |
| D2 | Install command = `uv tool install daimon` | 2026-04-28 | Modern Python tool standard; bootstraps Python on demand |
| D3 | Bootstrap is silent, runs on every command, idempotent | 2026-04-28 | Folds in what was previously `install` + `onboard` + `init` |
| D4 | `daimon menu` detaches and returns | 2026-04-28 | Required for agent UX |
| D5 | Frontend ships as static files in the wheel | 2026-04-28 | No npm at install time |
| D6 | No bundled Chromium runtime | 2026-04-28 | Use OS native webview |
| D7 | Card art continues to download via existing art pack mechanism | 2026-04-28 | Wheel stays small |
| D8 | Major version bump to v2.0.0 | 2026-04-28 | Terminal commands removed = breaking |
| D9 | Headless CLI + MCP tools unchanged | 2026-04-28 | N6 |
| D10 | No telemetry | 2026-04-28 | N13 |

---

## 17. Open questions

These need a human decision before Phase 0 starts.

| # | Question | Default if unanswered |
|---|---|---|
| Q1 | Do we keep `daimon doctor` as a diagnostic command, or drop it entirely? | Keep — useful for support |
| Q2 | Sound effects in V1 or defer? | Defer to post-V1 |
| Q3 | System tray icon when daimon is running? | Defer |
| Q4 | Min macOS version: 11 or 12? | 11 (covers more users) |
| Q5 | Min Windows 10 build: 1809 or later? | 1809 (matches WebView2 support floor) |
| Q6 | Bundle the art pack in the wheel (V1) or keep downloading on first run? | Keep downloading (smaller wheel, faster `pip install`) |
| Q7 | Web Components or React/Svelte for V1 frontend? | Web Components (no build step) |
| Q8 | Should `daimon menu` accept `--foreground` for debugging? | Yes — useful when window dies fast |
| Q9 | Should `daimon menu` accept `--port N` for fixed port? | Yes — useful for tests + dev |
| Q10 | What happens to currently bundled WezTerm install on user upgrade? | `daimon doctor --clean` removes it; document in migration notes |

---

## 18. Migration notes for users on v1.x

(Goes in the v2.0.0 release notes when we ship.)

- `daimon menu` now opens a native window instead of running in your terminal.
- The bundled WezTerm install (`~/.daimon/bin/wezterm.exe` etc.) can be deleted; `daimon doctor --clean` does it for you.
- All headless commands (`daimon pull --json`, `daimon mine status`) work exactly as before.
- The MCP server config in Claude Code is automatically updated on first run of v2; no action needed.
- Identity, ledger, collection, and saved loadouts carry over unchanged.

---

## 19. What to read next

- `daimon/bootstrap.py` (once written) — the load-bearing first-run logic
- `daimon/web/server.py` (once written) — the FastAPI app
- `daimon/web/static/app.js` (once written) — the frontend router
- `daimon/skills/install/SKILL.md` (post-Phase-4) — the agent-facing contract
