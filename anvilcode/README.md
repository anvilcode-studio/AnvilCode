# AnvilCode v2

A **ZCode-style terminal coding agent** running **GLM-5.3** and **GLM-5.3-Flash** through the
free HuggingFace router. No account — just a free HF token. Built from scratch in Python
(patterns inspired by Codex CLI / Gemini CLI / OpenCode; no code copied).

The startup banner is fully custom: the hand-drawn anvil icon rendered as **terminal pixel
art** (true blue→white gradient, half-block rendering — generated from `icon.png` by
`make_icons.py`, no runtime image deps) over a **gradient-filled ANVIL wordmark**. `/logo`
shows it again. Model replies may use emojis; the UI chrome uses its own glyphs.

```
 █████╗ ███╗   ██╗██╗   ██╗██╗     ██╗
██╔══██╗████╗  ██║██║   ██║██║     ██║
███████║██╔██╗ ██║██║   ██║██║     ██║
██╔══██║██║╚██╗██║██║   ██║██║     ██║
██║  ██║██║ ╚████║╚██╗ ██╔╝███████╗███████╗
╚═╝  ╚═╝╚═╝  ╚═══╝ ╚═══╝ ╚══════╝╚══════╝
      C O D E · terminal coding agent
```

## Input syntax

| Prefix | Meaning |
|---|---|
| `/` | capabilities — `/help`, `/mode`, `/model`, `/mcp`, … |
| `@` | attach context — `@src/app.py`, `@"my file.txt"` (quotes for spaces) |
| `@cd` | **connect** a folder as the working directory — `@cd "C:\Users\Käyttäjä\Downloads\spotify-icons-png"` — its contents are **never** read or listed unless you (or the model, via tools) explicitly ask afterwards |
| `$` | skills — `$explain src/app.py`, `$review`, `$commit`, `$plan`, … |
| `\` | continue input on the next line |

Paths with `ä`/`ö`/spaces work everywhere; use quotes when the path contains spaces.

## Permission modes (`/mode`)

| Mode | Behavior |
|---|---|
| `ask` (default) | every edit shows a diff and asks; every command asks |
| `auto-edit` | file edits apply instantly (diff still shown); commands still ask |
| `plan` | read-only — the agent investigates and ends with a numbered plan |
| `full` | nothing asks (the old `/yolo` still toggles ask/full) |

The approval prompt understands letters **and** full words:

```
approve?  (y)es   (n)o   (a)lways yes   (v) never this session   [Enter = no]
```

- `a` → auto-approve that tool for the session · `v` → block it for the session
- `/allow` reviews both lists, `/allow clear` resets them
- every applied change renders as a **green/red diff** with clickable buttons —
  `[ open file ]  [ open diff ]` — and every command's full output is logged with an
  `[ output ]` button (clickable in Windows Terminal / most modern terminals)
- `/edits` lists everything the agent changed this session; `/undo` reverts the last change

## Highlights

- **Agent loop** — reads, searches, edits, runs commands, verifies; streaming markdown
  output (throttled rendering — no more laggy textbox), thinking-tail spinner, usage per turn
- **Models & effort** — `flash` (GLM-5.3-Flash) / `glm` (GLM-5.3), effort `low|medium|high|max`;
  `max` = high + an exhaustive-thinking directive; rejected params degrade gracefully
- **MCP built in** — `/mcp add generic` (http_get, clipboard, open_url, sys_info) and
  `/mcp add ida` (bridges into IDA Pro via the free *ida-pro-mcp* plugin on port 13337:
  metadata, function list, decompile, imports, strings). Works with any stdio MCP server
  configured in `~/.anvilcode/config.json` under `mcp_servers` (`"autostart": true` to connect on launch)
- **Plugins** — drop a folder with `plugin.json` + `commands/*.md` + `skills/*.md` + `prompt.md`
  into `~/.anvilcode/plugins/` or `./plugins/`; bundled example: `git-helper` (`/pr`, `/changelog`)
- **Skills** — built-ins (`$explain $review $test $refactor $commit $plan $doc $find`),
  plus a full studio: `/skills edit explain` opens it in your editor (builtin overrides live in
  `~/.anvilcode/skills/`), `/skills new mine`, `/skills copy review`, `/skills show plan`.
  Edit the system prompt with `/system edit`.
- **Sessions** — autosaved after every turn; `/resume` (Enter = latest), `/save`, `/sessions`,
  `/export` to markdown
- **Context care** — `/compact` summarizes the conversation; auto-compacts near the limit;
  `/history` shows what's in context
- **Extras** — `/init` (write AGENTS.md), `/review` (review your diff), `/diff`, `/copy`,
  `/undo`, `/edits`, `/allow`, `/open <path>`, `/logs` (opens the output/diff log folder),
  `/status`, `/attach`, `/image`, vision (`@pic.png`), custom system prompt (`/system`),
  one-shot mode, `AGENTS.md`/`CLAUDE.md` auto-load
- **No emoji** — plain ASCII/box glyphs only; blue → white theme throughout (terminal + icons)

## Setup (once)

```bash
cd Downloads\anvilcode
python -m pip install --user -r requirements.txt
python anvil.py --check        # offline check of every subsystem (no token needed)
```

## Run

```bash
python anvil.py                # or anvil.bat
```

Your saved token is reused automatically — press Enter to keep it, or paste a new one.
First run ever: paste a free token from https://huggingface.co/settings/tokens (read scope
is enough). `HF_TOKEN` env var also works.

```bash
python anvil.py "fix the failing test in tests/test_app.py"
python anvil.py --model glm --effort max --mode auto-edit "refactor src/"
python anvil.py -p "summarize this repo"     # one-shot
```

## Codebase map

```
anvil.py               entry: args, startup, REPL
anvil/
  theme.py  ui.py      palette, glyphs, banner · panels, throttled streaming, prompts
  config.py modes.py   settings/token · ask | auto-edit | plan | full
  client.py            HuggingFace router streaming (with graceful param fallback)
  agent.py             the turn loop + approvals + compaction
  toolkit/             tools: files, search, shell + undo journal + registry
  expander.py          @ mentions and $ skills expansion
  skills.py plugins.py extensibility
  mcp/                 dependency-free MCP stdio client + manager
  servers/             bundled MCP servers: generic.py, ida_bridge.py (IDA Pro)
  commands.py          every /command
  sessions.py          named saves + autosave
plugins/               bundled example plugin
```

## IDA Pro setup (free)

1. Install the free **ida-pro-mcp** plugin into IDA Pro (see its GitHub README)
2. Open your database, start the plugin server (Edit > MCP > Start)
3. In AnvilCode: `/mcp add ida` then `/mcp connect ida`
4. Ask things like *"decompile the function at 0x401000 and find what calls it"* —
   the agent gets `ida_metadata`, `ida_list_functions`, `ida_decompile`, `ida_imports`, `ida_strings`

## Troubleshooting

- **import/aiohttp errors** → `python -m pip install --user --upgrade openai aiohttp`
- **MCP server won't connect** → `/mcp list` shows status; bundled servers only need Python
- **IDA tools fail** → IDA must be open with the plugin server started (connection refused otherwise)
- **garbled output in old cmd.exe** → use Windows Terminal or Git Bash

## License

MIT — do whatever you want. Not affiliated with Z.ai; it's an homage.
