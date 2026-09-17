"""
shadowos_app -- implementation package for ShadowOS.

Module map (each owns one concern; core.py is the only one that knows the
overall shape of a session):

    config.py       constants, .env loading, on-disk paths, personas,
                     provider registry, retry/context settings
    providers.py     provider adapters (Anthropic, OpenAI, Groq, Gemini,
                      Ollama): buffered + streaming, retries, tool-calling
    memory.py         conversation history + context-compaction summary
    storage.py        saved-chat JSON files (save/load/list/delete/rename)
    productivity.py   notes/tasks/reminders -- separate local JSON store
    tools.py           shell/file/note/task/reminder tool schemas + execution
    dashboard.py       /dashboard snapshot view
    commands.py        slash-command parsing and dispatch
    ui.py               terminal output (Rich, incl. the launch banner)
                        and input (prompt_toolkit)
    core.py             main application loop
"""
