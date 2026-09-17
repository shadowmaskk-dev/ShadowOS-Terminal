# ShadowOS

A terminal AI assistant for Termux: multi-provider streaming chat (Anthropic,
OpenAI, Groq, Gemini, Ollama) with retries, context compaction, personas, and
save/load conversations — plus shell/file tool access, notes/tasks/reminders,
a dashboard, and a raw shell mode with zero AI involvement.

This is a full rebuild of ShadowOS's chat system, restructured around the
same architecture as the ShadowChat reference project you shared
(streaming SSE, exponential-backoff retries, safe response parsing, a
clean module-per-concern package layout) — extended to keep ShadowOS's
agentic tool-calling, which that project doesn't have.

## What's new vs. the old single-file version

- **Real streaming** — replies render live, token by token, as Markdown, for
  Anthropic/OpenAI/Groq. (Gemini and Ollama render as one flush — see
  "Streaming" below.)
- **Retries with backoff** — a flaky connection or a provider's brief 5xx
  outage is retried automatically; a bad key or rate limit fails immediately
  instead of retrying pointlessly.
- **Context compaction** — once a conversation crosses a token budget, older
  messages are folded into a running summary automatically, so long chats
  don't grow forever or blow past a provider's limit.
- **Personas** — `default`, `developer`, `research`, `concise` — switch the
  system prompt without losing conversation memory.
- **Save/load named conversations** — `/save`, `/load`, `/chats`, `/rename`,
  `/delete`, stored as plain JSON under `~/.shadowos/chats/`.
- **Tab-completion + persistent input history** via `prompt_toolkit` (arrow
  keys recall past input across sessions, same as before, plus completion
  for commands/providers/personas/colors).
- Tool-calling (shell, files, notes/tasks/reminders), the dashboard, banner,
  raw shell mode, and `/env` key diagnostics all carry over from before.

## Setup

```bash
pkg update && pkg upgrade
pkg install git python
git clone <this-repo-or-copy-the-folder> shadowos
cd shadowos
pip install -r requirements.txt

cp .env.example .env
nano .env   # paste in the API key(s) for whichever provider(s) you'll use
```

Only four dependencies: `requests`, `python-dotenv`, `rich`, `prompt_toolkit`.
Every provider is called directly over REST — never through the official
`anthropic`/`openai`/`google-generativeai` SDKs, which pull in compiled
extensions (`jiter`, `grpcio`) with no prebuilt wheels for Android/Termux and
routinely fail to build from source there.

## Running it

```bash
python main.py
```

## Commands

| Command | Description |
|---|---|
| `/model <provider>` | Switch provider (`anthropic`, `openai`, `groq`, `gemini`, `ollama`), keeping that provider's current model |
| `/model <provider> <model>` | Switch provider and set a specific model for it |
| `/models` | List all providers, the active model for each, and context status |
| `/persona` | List personas, with the active one marked |
| `/persona <name>` | Switch persona (`default`/`developer`/`research`/`concise`); memory preserved |
| `/status` | Active provider/model/persona, message count, context usage, per-provider key status |
| `/context` | Detailed estimated token usage vs. the compaction limit |
| `/new` | Start a fresh, unsaved conversation |
| `/clear` / `/clear --force` | Clear conversation memory (asks to confirm, or immediately with `--force`) |
| `/save <name>` | Save the current conversation to disk |
| `/load <name>` | Load a saved conversation (replaces current) |
| `/chats` | List saved conversations |
| `/delete <name>` | Delete a saved conversation |
| `/rename <name>` | Rename the active saved conversation |
| `/notes [tag]` | List saved notes, optionally filtered by tag |
| `/tasks [all]` | List open tasks (`all` includes completed) |
| `/reminders [all]` | List upcoming reminders (`all` includes past) |
| `/dashboard` | Snapshot: model, disk, battery, notes/tasks/reminders |
| `/color <name>` | Change the banner color, or `random` |
| `/cd <path>` | Change the working directory shell/file tools operate in |
| `/ai` | Switch to AI chat mode (default) |
| `/shell` | Switch to raw shell mode — input runs directly, no AI, no tokens |
| `/env` | Check which API keys are loaded, without making a request |
| `/help` | Show commands |
| `/exit` | Quit |

In any mode, prefixing a line with `!` runs it as a one-off shell command
immediately, without changing your mode — e.g. `!ls -la` while still in AI
mode. Notes/tasks/reminders are also exposed as tools the model can call
directly — just ask in plain language.

## Project structure

```
shadowos/
├── main.py                 # Entry point
├── shadowos_app/
│   ├── config.py            # Constants, .env loading, paths, provider registry, personas
│   ├── providers.py          # Provider adapters: buffered + streaming, retries, tool-calling
│   ├── memory.py              # Conversation history + context-compaction summary
│   ├── storage.py             # Saved-chat JSON files (save/load/list/delete/rename)
│   ├── productivity.py        # Notes/tasks/reminders — separate local JSON store
│   ├── tools.py                # Shell/file/note/task/reminder tool schemas + execution
│   ├── dashboard.py            # /dashboard snapshot view
│   ├── commands.py             # Slash-command parsing and dispatch
│   ├── ui.py                    # Rich console output (incl. the banner) + prompt_toolkit input
│   └── core.py                  # Main application loop
├── requirements.txt
├── .env.example
└── README.md
```

Everything persistent (state, saved chats, notes/tasks/reminders, input
history) lives under `~/.shadowos/`, not the project folder — it survives a
`git pull` or reinstall of the code itself. Only `.env` (your keys) lives in
the project folder.

## Conversation and context management

Messages are kept in memory as you chat. Once the estimated token usage of
the conversation (recent messages plus any existing summary) crosses a
configured limit, ShadowOS folds the older messages into a running summary —
generated by whichever provider is currently active — while always keeping
the most recent messages verbatim. Token usage is estimated with a
lightweight, provider-independent heuristic (~4 chars/token); no extra API
calls are made just to measure it. `/status` and `/context` show the current
numbers.

Only clean `{"role", "content"}` pairs are ever stored in conversation memory
or a saved chat — tool calls and their results that happen *within* a turn
(shell commands run, files read/written, notes created) are collapsed into a
short text summary before being folded back into memory, so saved chats stay
portable and simple while still fully supporting tool use turn-by-turn.

## Streaming

Anthropic, OpenAI, and Groq stream token-by-token over SSE, including
incrementally-assembled tool calls. Gemini and Ollama don't get real
incremental streaming — their tool-calling stream shapes are less uniform to
parse safely without extensive live testing against the real API, so those
two perform the buffered call and render the whole reply at once. Every
provider exposes the same interface either way, so this is invisible to
anything except the pacing of Gemini/Ollama replies.

## Security

API keys are only ever read from environment variables (including ones
loaded from `.env`) — never hardcoded, never written to a saved chat, never
printed to the terminal (`/env` only ever shows a masked preview). A `.env`
value always overrides anything already exported in your shell, so editing
`.env` and restarting is enough; no need to `unset` anything first.

## Troubleshooting

**Getting a 401/API error even though you set your key in `.env`** — run
`/env` first. The two most common causes: the value is still the literal
example placeholder (e.g. `sk-ant-...`) because it wasn't actually replaced,
or there's a typo/extra whitespace. `/env` shows both without spending a
request.

**A note, task, or shell command with `[brackets]` in it used to break output** —
fixed: Rich (the terminal styling library) treats `[...]` as markup syntax,
so previously, a note, task, model id, file path, or shell command
containing a literal `[` could get misparsed or crash the console. All
user/tool-generated content is now either escaped or printed with markup
disabled before display — notes, tasks, reminders, tool output, error
messages, model IDs, and working directories included.
