# Command-line facts onboarding relies on

Status: checked 2026-10-24. Every later task of the onboarding launch copies its constants from
the Constants section below; nothing in it is guessed without saying so.

Checked on Windows 11 with Claude Code 2.1.303 (the native install on PATH) and gh 2.92.0, and
on Linux in WSL (Ubuntu, x86_64, glibc) with the official linux-x64 build of 2.1.303. macOS was
not available; its items are in the last section with the command that settles each.

How it was checked:

- Every `claude` run was started with every `CLAUDE*` and `ANTHROPIC*` variable removed, as the
  OS scheduler starts a routine (the session it ran from sets about thirty of them), in an empty
  scratch folder, with `--no-session-persistence`, and with `--model haiku` where the model did
  not matter. The prompt went in on stdin, as `routine.py` sends it.
- Signed-out output came from an empty `CLAUDE_CONFIG_DIR` and an empty `GH_CONFIG_DIR`. The real
  sign-ins were only read, never changed.
- What cannot be triggered on purpose (a usage limit, extra usage, an expired connector) was read
  from the CLI's own bundled source in the 2.1.303 binary. Those texts are quoted and marked
  UNVERIFIED-live; the patterns match the quoted text.
- Eight `claude -p` runs reached the API, each one tiny. The report of this task lists them. (Task 5 added three more init probes, each one turn on haiku: section 2.)

## 1. Refusing instead of prompting

`--permission-prompts none` exists and is accepted (2.1.303; it is also in 2.1.296). From
`claude --help`:

```
--permission-prompts <target>   Who answers permission prompts with --print: "host" (the SDK
                                host or --permission-prompt-tool) or "none" (nobody: anything
                                that would prompt is denied automatically; the permission mode
                                still decides everything else) (choices: "host", "none",
                                default: "host")
--permission-mode <mode>        (choices: "acceptEdits", "auto", "bypassPermissions", "manual",
                                "dontAsk", "plan")
```

Run (the gate run of question 2; the model tried a command from the person's own global
instructions file):

```
echo "Reply OK." | claude -p --permission-mode acceptEdits --permission-prompts none \
  --output-format stream-json --verbose --model haiku --max-turns 1 --no-session-persistence
```

The command was refused at once, without waiting:

```json
{"type": "system", "subtype": "permission_denied", "tool_name": "Bash",
 "decision_reason_type": "asyncAgent",
 "decision_reason": "no approval surface in this session; permission request denied automatically",
 "message": "Permission for this tool use was denied. It requires approval, and this session has no approval surface - nobody can answer a permission prompt here - so it was denied automatically. The action was NOT performed; do not claim it succeeded, and do not retry it: ..."}
```

The refusal shows three ways: this `system`/`permission_denied` event (stream-json only), an
`is_error` tool result the model sees, and the result's `permission_denials` list. The run goes
on.

A claude.ai connector tool that is not allowed is refused the same way (run 8, the same flags
plus `--disallowedTools Bash PowerShell --allowedTools mcp__claude_ai_Claude_Docs__guide`, so
nothing from Gmail is allowed; the prompt asked for one `mcp__claude_ai_Gmail__search_threads`
call with a query that matches nothing):

```json
{"type": "system", "subtype": "permission_denied", "tool_name": "mcp__claude_ai_Gmail__search_threads",
 "decision_reason_type": "asyncAgent",
 "decision_reason": "no approval surface in this session; permission request denied automatically",
 "message": "Permission for this tool use was denied. ... What required approval: Claude requested permissions to use mcp__claude_ai_Gmail__search_threads, but you haven't granted it yet."}
```

The result listed it in `permission_denials` (`"tool_input": {"query": "from:nobody@lanternworks.example"}`),
`is_error: false`, exit 0; the search did not run. The model first loaded the tool with
`ToolSearch` (`{"query": "select:mcp__claude_ai_Gmail__search_threads"}`): in `-p` connector
tools can be deferred, as in a session, though init lists them all.

- Without the flag (`--permission-mode acceptEdits` alone, run 6), plain `-p` with no SDK host
  also refused rather than hung (`"decision_reason": "This command requires approval"`). The flag
  stays: it is explicit, and its message tells the model not to retry.
- `--permission-mode dontAsk` is accepted too (run 5: `"permissionMode": "dontAsk"` in init).
  The CLI's own description of it is "Don't prompt for permissions, deny if not pre-approved",
  which would take in the routine's own edits; no edit was tried under it (run 5 had no tools).
  `acceptEdits` accepts edits in the working folders (run 4 wrote a file there with no allow
  rule). So `REFUSE` keeps `acceptEdits` plus `--permission-prompts none`: exactly today's
  `routine.py:32` minus `-p`.
- Two things the CLI approves by itself, so a routine profile must not rely on a prompt for
  them: read-only shell commands (`echo hi` ran in run 6 with no allow rule), and anything a bare
  tool name in `--allowedTools` covers (run 4: `--allowedTools Read` let the model read a file
  outside both working folders). Never put a bare `Read`, `Glob` or `Grep` in an allow list.

## 2. The init event lists the tools, claude.ai connectors included (the gate)

Yes. With the person's real sign-in (a claude.ai Team account), the `system`/`init` event of
`claude -p --output-format stream-json --verbose` lists `tools` and `mcp_servers`, and the
claude.ai connectors are in both. Run 3, the command in section 1. Excerpt of the init event
(paths shortened):

```json
{"type": "system", "subtype": "init", "cwd": "<scratch>\\ws", "model": "claude-haiku-5-5",
 "permissionMode": "acceptEdits", "apiKeySource": "none", "claude_code_version": "2.1.303",
 "additional_directories": ["<scratch>/ws"],
 "mcp_servers": [
  {"name": "claude.ai Gmail", "status": "connected", "source": "claudeai"},
  {"name": "claude.ai Google Calendar", "status": "connected", "source": "claudeai"},
  {"name": "claude.ai Google Drive", "status": "connected", "source": "claudeai"},
  {"name": "claude.ai Slack", "status": "connected", "source": "claudeai"},
  {"name": "claude.ai Notion", "status": "connected", "source": "claudeai"},
  {"name": "claude.ai Notetaker", "status": "connected", "source": "claudeai"},
  {"name": "claude.ai Microsoft 365", "status": "needs-auth", "source": "claudeai"},
  {"name": "claude.ai Linear", "status": "needs-auth", "source": "claudeai"},
  {"name": "plugin:azure:azure", "status": "connected", "source": "plugin"},
  "... 28 claude.ai entries and 18 plugin entries in all"],
 "tools": ["Task", "Bash", "Edit", "Glob", "Grep", "PowerShell", "Read", "Write", "...",
  "mcp__claude_ai_Gmail__search_threads", "mcp__claude_ai_Gmail__get_thread",
  "mcp__claude_ai_Gmail__send_message", "mcp__claude_ai_Gmail__reply",
  "mcp__claude_ai_Gmail__forward", "mcp__claude_ai_Gmail__trash_thread",
  "mcp__claude_ai_Google_Calendar__list_events", "mcp__claude_ai_Slack__slack_read_channel",
  "mcp__claude_ai_Slack__slack_send_message", "... 450 tools in all"]}
```

Facts the later tasks need:

- **Naming.** A connector shown as `claude.ai <Name>` gives tools `mcp__claude_ai_<Name>__<tool>`.
  From the CLI's source:
  `function Tn(e){let r=e.replace(/[^a-zA-Z0-9_-]/g,"_");if(e.startsWith("claude.ai "))r=r.replace(/_+/g,"_").replace(/^_|_$/g,"");return r}`.
  So `claude.ai Google Calendar` is `claude_ai_Google_Calendar`, `claude.ai Microsoft 365` is
  `claude_ai_Microsoft_365`, `claude.ai monday.com` is `claude_ai_monday_com`. Map a server part
  back to its app through the init event's `mcp_servers` names, not by turning `_` into spaces.
- **Only connected connectors have tools.** A `needs-auth` connector is listed in `mcp_servers`
  and contributes no tool (Microsoft 365 and Linear above). Statuses in the CLI's schema:
  `connected`, `failed`, `needs-auth`, `pending`, `disabled`.
- **The list is what the run may call.** `--disallowedTools Bash PowerShell` removed both from
  `tools` (run 4); `--tools ""` removed every built-in tool and kept the connectors (run 5: 417
  tools, none built in).
- **Init is not the first line.** The person's plugins' `SessionStart` hooks print
  `hook_started`/`hook_response` events first (eight lines in run 3). Find init by
  `type == "system" and subtype == "init"`.
- **On Windows there is a `PowerShell` tool** beside `Bash` (on by default for claude.ai accounts
  when Git for Windows is installed, per the setup docs). A list that disallows `Bash` must
  disallow `PowerShell` too.
- **The desktop app names the same connectors differently** (`mcp__<uuid>__send_message` inside
  a desktop session). Tool names for `claude -p` come only from a `claude -p` init event.
- **A routine run loads the person's own setup:** their global instructions file, user settings,
  plugins and their hooks. In runs 3 and 6 the model tried to run a command from the person's
  global instructions file; it was refused.
- **`--setting-sources project,local` isolates from that setup, and no connector shows up in the
  run, so no run that needs them uses it** (Task 5, one run, 2026-10-24). `claude --help` lists
  `--setting-sources <sources>` ("Comma-separated list of setting sources to load (user, project,
  local)"). The run, in the way of section 1 (every `CLAUDE*` and `ANTHROPIC*` variable removed,
  cwd a folder inside the template's checkout):

  ```
  echo "Reply OK." | claude -p --permission-mode acceptEdits --permission-prompts none \
    --output-format stream-json --verbose --setting-sources project,local \
    --model haiku --max-turns 1 --no-session-persistence
  ```

  Five lines: `init`, `commands_changed`, one `assistant` ("OK"), `rate_limit_event`, `result`
  (`success`, 1 turn). No `hook_started` or `hook_response` line, no tool call attempted (run 3
  tried the global instructions' command). The init event, trimmed:

  ```json
  {"type": "system", "subtype": "init", "model": "claude-haiku-5-5", "permissionMode": "acceptEdits",
   "apiKeySource": "none", "claude_code_version": "2.1.303", "mcp_servers": [],
   "tools": ["Task", "Bash", "CronCreate", "CronDelete", "CronList", "Edit", "EnterWorktree",
             "ExitWorktree", "Glob", "Grep", "ListAgents", "Monitor", "NotebookEdit", "PowerShell",
             "PublishPlugin", "PushNotification", "Read", "ReportFindings", "ScheduleWakeup",
             "SendMessage", "Skill", "TaskStop", "ToolSearch", "WebSearch", "Workflow", "Write"],
   "plugins": ["cc-plugin-sec-default@builtin", "cc-plugin-agents-md@builtin",
               "cc-plugin-telemetry@builtin", "cc-plugin-plugin-authoring@builtin"], "...": "..."}
  ```

  Against section 2's run 3 (28 claude.ai entries, 18 plugin entries, 450 tools, eight hook
  lines): the person's plugins, their hooks and the global instructions' command are gone, and
  the init event lists no claude.ai connector (`mcp_servers` empty, no `mcp__` tool); no
  connector showed up later in the stream either (unlike the `--restricted` run below). So a
  reader or the probe cannot use it. Why was not established. Two more flags in `claude --help`,
  **not run**: `--safe-mode` (disables "MCP servers" among everything else, so probably the
  connectors too) and `--bare` (never reads the claude.ai sign-in).
- **`--restricted` keeps the connectors, but its init event does not list them** (Task 5, one
  run, 2026-10-24: the command above with `--restricted` in place of `--setting-sources
  project,local`). The help text: "ignores user, project and local settings files (managed
  settings and --settings still apply; add --strict-mcp-config to skip MCP servers too)",
  removes the built-in tools that run commands or code and WebFetch unless `--tools` names
  them, "confines the file tools to the working directories (--add-dir included)", and refuses
  `bypassPermissions`. Six lines: `init`, two `commands_changed`, one `assistant` ("OK"),
  `rate_limit_event`, `result` (`success`). No hook line; `mcp_servers: []`; 23 tools, none
  `mcp__`; Bash, PowerShell, Monitor, Workflow and CronCreate absent, Read, Write, Edit, Glob and
  Grep still there; `plugins` only the four built-in ones; only the 19 built-in skills; empty
  `memory_paths`; no command attempted. The second `commands_changed`, after init, added
  `claude.ai Notion:make-this-a-notion-page (MCP)` and `...-database (MCP)`: the claude.ai
  connectors do load under `--restricted`, a moment after init. Two consequences. The person's
  setup is gone and `--settings` still applies, so `--restricted` isolates a reader. But under
  `--restricted` init is printed before the connectors connect: a probe that reads init alone
  sees no app with it.
- **Without `--restricted`, init lists the connectors, plugin hooks or not** (Task 5, the
  control, one run, 2026-10-24: section 1's command with `--settings <file>` holding
  `{"disableAllHooks": true}`, nothing else changed). Seven lines: `init`, two
  `thinking_tokens`, two `assistant`, `rate_limit_event`, `result` (`success`); no hook line, no
  `commands_changed` after init. Init: 46 `mcp_servers`, of them 28 `claudeai` (12 `connected`,
  16 `needs-auth`; none `pending` or `failed`) and 18 `plugin` (9 `needs-auth`, 7 `failed`, 2
  `pending`); 376 tools, 33 built-in and 343 `mcp__claude_ai_*`, none from a plugin server. Every
  `connected` claude.ai connector has tools in the list and no `needs-auth` one has any. So the
  init event of the default probe is complete when it is printed (the connection attempts have
  ended; `pending` shows only on plugin servers that were still starting), the hook lines are not
  what gives the connectors time, and `probe_tools` needs nothing but the init event. The
  `--restricted` run is the one whose init came early.

## 3. Usage, limits and extra usage

**Usage** (run 2, `--output-format json`; the last line of stream-json is the same `result`
object):

```json
{"type": "result", "subtype": "success", "is_error": false, "num_turns": 1,
 "total_cost_usd": 0.1357575, "terminal_reason": "completed", "result": "OK.",
 "usage": {"input_tokens": 2, "cache_creation_input_tokens": 33504,
           "cache_read_input_tokens": 16875, "output_tokens": 5, "...": "..."},
 "modelUsage": {"claude-sonnet-5-5": {"inputTokens": 2, "outputTokens": 5,
   "cacheReadInputTokens": 16875, "cacheCreationInputTokens": 33504, "costUSD": 0.1357575,
   "costBasis": "list", "...": "..."}}}
```

- The CLI's own schema describes `usage` as "MAIN AGENT LOOP ONLY - excludes Task subagent,
  sidechain, and auxiliary model calls ... Prefer modelUsage for token/cost accounting." Sum
  `modelUsage` over the models: `input_tokens = inputTokens`, `output_tokens = outputTokens`,
  `cache_read_tokens = cacheReadInputTokens`, `cache_creation_tokens = cacheCreationInputTokens`.
- `total_cost_usd` is a list price (`"costBasis": "list"`), also on a subscription: a size, not a
  bill.
- A one-line prompt already costs about 50,000 input tokens (system prompt plus 450 tools, mostly
  cached).

**The usage reading in stream-json.** Each run printed one `rate_limit_event` (runs 3 and 4):

```json
{"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "resetsAt": 1792879200,
 "rateLimitType": "five_hour", "overageStatus": "rejected",
 "overageDisabledReason": "org_level_disabled_until", "isUsingOverage": false,
 "unifiedWindows": {"five_hour": {"utilization": 0.02, "resetsAt": 1792879200},
                    "seven_day": {"utilization": 0.2, "resetsAt": 1793246400}}}}
```

`status` is `allowed`, `allowed_warning` or `rejected`; `resetsAt` is Unix seconds. This is the
structured form of the limits and the only place `-p` shows extra usage starting.

**Limit reached: UNVERIFIED-live.** From the 2.1.303 source (`AS`, `RS`, `lt`, `hie`): the
limit names are `five_hour: "session limit"`, `seven_day: "weekly limit"`,
`seven_day_opus: "Opus limit"`, `seven_day_sonnet: "Sonnet limit"`,
`seven_day_overage_included: "Fable limit"`, and the text is built as
``return`You've hit your ${e}${r}${h}` `` with `r = " \xB7 resets " + time` when a reset is known
and `h = " \xB7 progress saved"` on some accounts. So:

```
You've hit your session limit · resets 11:20pm (America/New_York)
You've hit your weekly limit · resets Oct 27, 1pm (America/New_York)
You've hit your session limit                                          (no reset time)
You've hit your usage limit · contact your admin to increase it        (no reset time)
```

The time comes from `eP(resetsAt, true)`: within 24 hours `toLocaleTimeString("en-US",
{hour: "numeric", minute: <"2-digit" unless :00>, hour12: true})` with ` PM` turned into `pm`
(`11pm`, `11:20pm`); further out `toLocaleString` with month and day (`Oct 27, 1pm`), the year
added when it differs (`Jan 2, 2027, 1:30pm`); then ` (<local IANA time zone>)`. The time is the
machine's local time; read it as local time (Python on Windows has no IANA zone data without
`tzdata`, which the standard-library rule excludes).

In `-p` the turn ends with an API-error message: on HTTP 429 the source returns
`Os({content: <that text>, error: "rate_limit", apiError: "usage_limit_reached", apiErrorParams:
{rate_limit_info}})`. By analogy with the signed-out run, which was observed (section 5), the
stream-json `assistant` event carries `"error": "rate_limit"` and `"is_api_error_message": true`,
and the result has `"is_error": true`, `"terminal_reason": "api_error"` and the text as
`result`. **The text is on stdout, in the result, not on stderr.** The assistant `error` kinds
in the CLI's schema: `authentication_failed`, `oauth_org_not_allowed`, `account_on_hold`,
`verification_required`, `billing_error`, `rate_limit`, `overloaded`, `invalid_request`,
`model_not_found`, `server_error`, `unknown`, `max_output_tokens`, `cloud_credential_error`.

**Exit codes are not reliable.** Observed: a result with `is_error: true` exits 1 on Windows
(signed out; `error_max_turns` in run 3) but the same signed-out run exited 0 on Linux (WSL,
same version). Classify an outcome from `is_error`, the assistant `error` field and the result
text, never from the exit code alone.

**Extra usage: UNVERIFIED-live.** No flag or setting keeps `-p` off extra usage: the binary's
only switch is `DISABLE_EXTRA_USAGE_COMMAND`, which hides a command. When a plan limit is reached
with extra usage on, requests go on and draw usage credits. The interactive notice for that
("You're now using usage credits · Your session limit resets 3pm (...)", from `Tyo`) is a
terminal notification, not `-p` output. In `-p` the signal is the `rate_limit_event` with
`"isUsingOverage": true`. The CLI's own list of these notices (the array `f$o`, which its
`wyo()` uses to tell a limit message by its start) is:

```
"You're now using usage credits", "You're now using your usage allocation",
"Now using your usage allocation", "Now using usage credits",
"You're now using extra usage", "Now using extra usage"
```

The same source has the limit openings (`t$r`: "You've hit your", "You've reached your",
"You're out of usage credits", "Your org is out of usage · add funds to continue", ...) and the
warnings (`p$o`: "You've used", "You're close to"). When credits are off or used up, the turn ends like a limit, with texts
from the source such as:

```
You're out of usage credits · resets 3pm (America/New_York)
You've hit your monthly spend limit · raise it at <link>
You've hit your team's shared budget · ask your admin to raise it at <link>
Your org is out of usage · add funds to continue
You've reached your Fable limit.        Fable 5 requires usage credits.
```

On the command line's account extra usage is off at the organisation
(`overageStatus: "rejected"`, `overageDisabledReason: "org_level_disabled_until"`). The desktop
app's `get_usage` also said `extraUsage.enabled: false`, but that reading was not the same
account's (section 8, "Two meters").

## 4. `--add-dir`, `--allowedTools`, `--disallowedTools`, `--model`, `--settings` hooks in `-p`

All five work in `-p`, and a `PreToolUse` hook's exit code 2 blocks the call. Run 4 (cwd
`<scratch>/ws4`; the prompt on stdin asked to read `other/allowed.txt`, `other/blocked.txt` and
`outside/secret.txt`, write `ws4/out.txt`, and run `echo hi`):

```
claude -p --permission-mode acceptEdits --permission-prompts none --output-format stream-json \
  --verbose --model haiku --max-turns 10 --no-session-persistence --add-dir <scratch>/other \
  --allowedTools Read --disallowedTools Bash PowerShell --settings <scratch>/runs/settings4.json
```

with `settings4.json` = `{"hooks": {"PreToolUse": [{"matcher": "Read", "hooks": [{"type":
"command", "command": "python -I <scratch>/scripts/hook.py <log>"}]}]}}`, a hook that exits 2
when the path contains `blocked`.

| Flag | Works in `-p` | What showed it |
|---|---|---|
| `--add-dir` | yes | init `additional_directories` holds the folder; `other/allowed.txt` read: `ALPHA` |
| `--allowedTools Read` | yes, and widely | `outside/secret.txt`, outside every working folder, read: `CHARLIE` |
| `--disallowedTools Bash PowerShell` | yes | both missing from init `tools`; the model said it had no shell |
| `--model haiku` | yes | init `"model": "claude-haiku-5-5"` |
| `--settings` with `hooks.PreToolUse` | yes | the hook ran for all three reads (its log has three lines) |
| hook exit 2 | blocks | `blocked.txt` not read; tool result `is_error: true`, `PreToolUse:Read hook error: [<command>]: spike hook: this file is blocked`; listed in `permission_denials` |
| hook cut off at its `timeout` | does not block | 2.1.303, dry run A (2026-10-25), probe 3: a hook with `"timeout": 5` that ran longer got `hook_response` `outcome: "cancelled"`, `exit_code: 1`, and `echo hi` then ran, no denial. So each guard ends itself with exit 2 after 10 s (`LIMIT_SECONDS`), under a hook `timeout` of 30 s (`routine.GUARD_TIMEOUT`) |
| hook `matcher` | a pattern | 2.1.303, live (2026-10-25, one run in a scratch folder): on one Glob call the matchers `Gl.*`, `Read\|Glob` and `Glob` all fired, so `Task\|Agent` and `mcp__.*` match as written |
| path rules on Windows | match regardless of case | 2.1.303, live (2026-10-25, one run in a scratch folder): `Edit(//**/_meta/state/backfill.yaml)` refused a Write to `_META\STATE\BACKFILL.YAML` ("File is in a directory that is denied by your permission settings") |
| `acceptEdits` write in cwd | allowed | `out.txt` written with no allow rule |

The hook on a claude.ai connector tool works the same (run 7): matcher
`mcp__claude_ai_Claude_Docs__guide`, hook exits 2, the call is blocked
(`PreToolUse:mcp__claude_ai_Claude_Docs__guide hook error: ...: spike guard: blocked`) and listed
in `permission_denials`. The hook's stdin JSON has the keys `cwd`, `effort`, `hook_event_name`,
`mcp_server`, `permission_mode`, `prompt_id`, `session_id`, `tool_input`, `tool_name`,
`tool_use_id`, `transcript_path`; `tool_input` is the tool's arguments as given
(`{"items": ["topic.index"]}`).

For the send guard: the Gmail connector's `send_message` takes `to`, `cc` and `bcc` as lists of
plain addresses, but also `draftId`, which "sends the draft as is" and ignores the recipient
fields, so a call with only `draftId` has no recipients in its input and must be refused (an
empty recipient list is refused). `reply` (recipients default to the original message's) and
`forward` also send mail; they are refused unless guarded. Schemas read from the same connector
in a desktop session.

`--disallowedTools` and `--allowedTools` take several values: put another flag after them, and
the prompt on stdin.

## 5. Installing without admin rights, pinned and checksummed; signing in

**Install.** The official installers (`https://claude.ai/install.sh` and `install.ps1` redirect
to `https://downloads.claude.ai/claude-code-releases/bootstrap.sh` and `bootstrap.ps1`) read
`$DOWNLOAD_BASE_URL/$version/manifest.json`, take `platforms.<platform>.checksum`, download
`$DOWNLOAD_BASE_URL/$version/$platform/claude` (`claude.exe` on Windows) into
`~/.claude/downloads`, check its SHA-256 and run `<binary> install [version]`, which places the
launcher in `~/.local/bin` and the versions in `~/.local/share/claude/versions/`. Platforms:
`darwin-arm64`, `darwin-x64`, `linux-x64`, `linux-arm64`, `linux-x64-musl`, `linux-arm64-musl`,
`win32-x64`, `win32-arm64`. The setup docs: "You do not need to run as Administrator." Nothing
goes outside the user's folders.

Checks:

- The 2.1.303 manifest's detached signature, verified in WSL with the published key
  (`https://downloads.claude.ai/keys/claude-code.asc`, fingerprint
  `31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE`, as the docs give it):
  `gpg: Good signature from "Anthropic Claude Code Release Signing <security@anthropic.com>"`.
- Windows x64: the installed 2.1.303 binary's SHA-256 is
  `1787c60619bad78543c67e4d9d8a868869c35a904f2a85eb9ea46c4808af196b`, the manifest's
  `win32-x64` checksum; `Get-AuthenticodeSignature` says `Valid`, signer `CN="Anthropic, PBC"`.
- Linux x64: downloaded `.../2.1.303/linux-x64/claude` in WSL; `sha256sum` gave
  `375988f457756524234695cf2d8f64ace99c5f68682e47ea24862a843c191450`, the manifest's;
  `./claude --version` printed `2.1.303 (Claude Code)`; then deleted.
- The asset is the bare executable, not an archive: the doctor's install path must skip
  unpacking, write `claude` or `claude.exe`, and `chmod 755` it. There is also a `claude.zst`;
  zstd is not in the standard library before Python 3.14, so use the plain binary.
- `doctor.ASSETS` is keyed by `(system, arch)`. Two cases the official script handles that key
  does not: musl Linux (it checks `/lib/libc.musl-x86_64.so.1`, `/lib/libc.musl-aarch64.so.1`
  or `ldd /bin/ls` mentioning musl, then uses `linux-<arch>-musl`) and an x86_64 Python under
  Rosetta on an Apple silicon Mac (it checks `sysctl -n sysctl.proc_translated` = 1, then uses
  `darwin-arm64`). The musl checksums are in the Constants.
- Channels on 2026-10-24: `stable` = 2.1.308 (the files `.../claude-code-releases/stable` and
  `.../latest`), `latest` = 2.1.310. The desktop app (3.6556.1) runs its sessions with its own
  copy, 2.1.308 (the session's `CLAUDE_CODE_EXECPATH` is under `claude-code\2.1.308\`). The pin is 2.1.303 because every fact here was checked on it. Native installs update
  themselves in the background; `DISABLE_AUTOUPDATER=1` (settings `env`) stops that,
  `minimumVersion` sets a floor.

**Sign-in status.** `claude auth status` prints JSON by default (`--json`; `--text` for prose).
Signed in (real config, read only; the address and organisation replaced):

```json
{"loggedIn": true, "authMethod": "claude.ai", "apiProvider": "firstParty",
 "analyticsDisabled": false, "projectsDirectory": "C:\\Users\\<you>\\.claude\\projects",
 "configDirectory": "C:\\Users\\<you>\\.claude", "email": "ada@lanternworks.example",
 "orgId": "<org uuid>", "orgName": "ada@lanternworks.example's Organization",
 "subscriptionType": "team"}
```

Signed out (empty `CLAUDE_CONFIG_DIR`), exit 1 on Windows:

```json
{"loggedIn": false, "authMethod": "none", "apiProvider": "firstParty",
 "analyticsDisabled": false, "projectsDirectory": "...", "configDirectory": "<scratch>/claude-empty2"}
```

`--text` says `Not logged in. Run claude auth login to authenticate.` The source ends with
`process.exit(k?0:1)` (k = logged in), but the signed-out Linux run exited 0: read `loggedIn`.
`authMethod` values in the source: `claude.ai`, `api_key`, `api_key_helper`, `oauth_token` (a
long-lived token in `CLAUDE_CODE_OAUTH_TOKEN`), `third_party` (Bedrock, Vertex, Foundry), `none`.
An `ANTHROPIC_API_KEY` in the environment shows as `apiKeySource` and takes over `-p` runs, which
is why routines drop it.

A signed-out `claude -p` (no API call is made) prints, on stdout:

```
Not logged in · Please run /login
```

and in stream-json the assistant event has `"error": "authentication_failed"`,
`"is_api_error_message": true`, `"model": "<synthetic>"`; the result has `"is_error": true`,
`"terminal_reason": "api_error"`, `"api_error_status": null`. Exit 1 on Windows, 0 on Linux.
The separator is U+00B7 (bytes `c2 b7`).

**The other sign-in errors: UNVERIFIED-live** (only "Not logged in · Please run /login" was
seen). Quoted from the 2.1.303 binary, each with where it sits; `AUTH_PATTERNS` matches each:

| Text | Where in the binary |
|---|---|
| `Not logged in · Please run /login` | the API-error table (byte 128937824), also `"Not logged in \xB7 Please run /login"` in the JS (249950564); observed live |
| `Not logged in · Run /login` | a status-line notice next to `showLoginNotice` (124100960) |
| `OAuth token revoked · Please run /login` | the API-error table (128939068) and the JS (249951812) |
| `Login expired · Please run /login` | the API-error table (128939116) |
| `Login expired · Run /login to sign in again, or re-authenticate your Anthropic profile` | the API-error table (128939708) |
| `Authentication required · Sign in again to continue` | the API-error table (128937868) |
| `Invalid API key · Fix external API key` | the API-error table (128937928) |
| `Invalid auth token · Fix external auth token` | the API-error table (128937976) |
| `Your organization has disabled API key authentication · Unset ANTHROPIC_API_KEY to use your claude.ai account instead` (and four more endings) | the API-error table (128938424-128938960) |
| `Your organization has disabled Claude subscription access for Claude Code · Use an Anthropic API key instead, or ask your admin to enable access` | the API-error table (128940732) |
| `Session expired. Please run /login to sign in again.` | the session fetch on HTTP 401 (`if(y.status===401)throw new ap("Session expired. Please run /login to sign in again."`, 243085083) |
| `Your session has expired. Please run /login to sign in again.` | the session-events fetch on HTTP 401 (248722696) |

Not sign-in errors, so not in the patterns: `Authentication error · This may be a temporary
network issue, please try again` and `Could not refresh your login because another Claude Code
process is refreshing it ...` (both transient).

**Starting the sign-in: UNVERIFIED-live** (not run: it opens a real browser sign-in).
`claude auth login [--claudeai] [--email <address>]`; `--claudeai` is the default and means the
claude.ai subscription. From the 2.1.303 source, it opens the browser itself and writes to
stdout:

```
Opening browser to sign in…
If the browser didn't open, visit: <authorize URL>
Paste code here if prompted > 
```

It reads stdin lines as a pasted `code#state`, prints `Login successful.` and exits 0, or
`Login failed: <why>` on stderr and exits 1. The claude.ai authorize URL starts with
`https://claude.com/cai/oauth/authorize`. The link is printed through a terminal-link helper
(`assumeSupport: true`), so it may come wrapped in OSC 8 escapes; the pattern below allows that.

## 6. The GitHub device flow with stdin closed

```
GH_CONFIG_DIR=<scratch>/gh-empty GH_BROWSER=<no-op> BROWSER=<no-op> \
  timeout 18 gh auth login --web --git-protocol https --hostname github.com </dev/null
```

gh 2.92.0. stdout: nothing. stderr (code masked):

```

! First copy your one-time code: XXXX-XXXX
Open this URL to continue in your web browser: https://github.com/login/device
```

- It kept polling until killed (exit 124 from `timeout` after 18 s).
- With stdin closed it did **not** open a browser: a second run with `GH_BROWSER` and `BROWSER`
  set to a script that records its arguments left no record. The tool must show or open the link
  itself.
- The code is 4 + 4 characters from `A-Z0-9` joined by `-`, on stderr. The device URL is
  `https://github.com/login/device`.
- Nothing was written to the empty `GH_CONFIG_DIR`: no approval, no sign-in.
- Checked on gh 2.92.0 (on PATH); the doctor pins 2.101.0, whose output was not checked.

## 7. An expired connector

**At the start of a run (observed):** the init event lists the connector with
`"status": "needs-auth"` and it brings no tools (section 2). This is the reliable signal: the
probe sees it before any call.

**During a call: UNVERIFIED-live.** From the 2.1.303 source: on a 401 from a connector the CLI
logs `Tool call returned 401 Unauthorized - token may have expired` and throws `McpAuthError`
with one of these texts (`gz` in the source), which reaches the model as an `is_error` tool
result:

```
MCP server "claude.ai Gmail" needs you to sign in again (run /mcp to re-authenticate)
MCP server "claude.ai Gmail" needs to be connected in claude.ai (run /mcp to connect it)
MCP server "claude.ai Gmail" needs additional permissions (scope: "<scope>") - run /mcp to re-authenticate
The MCP server "claude.ai Gmail" needs you to sign in; run /mcp to authenticate.
```

The parentheses vary by where the CLI runs (a claude.ai chat session gets "the user can
reconnect it in their claude.ai connector settings"; some hosts get no hint). In `-p` these texts
appear only in stream-json `user` events (tool results); the final `result` is the model's own
prose. Detect an expired connector from the init status first, and from `CONNECTOR_PATTERNS` over
the tool results second.

## 8. The desktop app's own tools

Captured by the controller on 2026-10-24 in the Claude desktop app (Code tab, Windows 11; app
3.6556.1 with its bundled Claude Code 2.1.308, per the session's `CLAUDE_CODE_DESKTOP_APP_VERSION`
and `CLAUDE_CODE_EXECPATH`). Copied as given, UNVERIFIED marks kept; "Two meters" at the end of
this section is this task's own check.

All four are deferred MCP tools in a desktop-app session: their names are listed in the
session's deferred-tool list and must be loaded with ToolSearch (`select:<name>`) before the
first call. Outside the desktop app (the plain `claude` CLI, other harnesses) none of them exist.

**get_session**
- Exact name: `mcp__ccd_session_mgmt__get_session`, argument `{"session_id": "self"}`.
- Loaded through tool search first: yes (deferred).
- Returns a JSON object. Fields the skill uses: `permissionMode` (one of `default`,
  `acceptEdits`, `plan`, `auto`, `bypassPermissions`; reported for this session and sessions it
  started only), `cwd`, `worktreePath`, `isRemote`, `model`, `effort`. Other fields:
  `sessionId`, `title`, `branch`, `createdAt`, `lastActivityAt`, `parentSessionId`, `link`,
  `remoteControlState`, `fastMode`, `outputStyle`, `group`, `detached`.
- Declined card: not applicable (read-only, no card).
- Older app without it: the tool is absent from the deferred list; the skill treats the mode as
  unknown and gives the one-line "how to switch" (Shift+Tab) instead.

**set_session_permission_mode**
- Exact name: `mcp__ccd_session_mgmt__set_session_permission_mode`, arguments
  `{"session_id": "self", "mode": "bypassPermissions"}` (enum: default, acceptEdits, plan, auto,
  bypassPermissions). `_consent` is set by the app; never set it.
- Loaded through tool search first: yes (deferred).
- Behaviour (from its own description): a switch to a mode that does more without asking
  (plan < default < acceptEdits < auto < bypassPermissions) shows the person an approval card in
  every mode and waits for it. Lowering on this session needs no card. "Unavailable modes are
  refused. Unavailable in unattended sessions."
- Declined card: UNVERIFIED (expected from the description: the tool result is a refusal and the
  mode stays as it was; the exact text was not captured). The skill must treat any result other
  than a confirmed switch as declined. The skill says one line ("setup will ask for each step
  instead") and goes on. NOT called live by the controller: calling it would put a card in front
  of the person.
- Older app without it: absent from the deferred list; one line on how to switch (Shift+Tab).

**get_usage**
- Exact name: `mcp__ccd_session_mgmt__get_usage`, argument `{}` (optional `session_id`, default
  "self", only changes the `context` block).
- Loaded through tool search first: yes (deferred). Read-only.
- Returns (real shape, values from that machine):

  ```json
  {"plan": {"status": "ok", "plan": "Max",
            "windows": [{"label": "5-hour limit", "percentUsed": 38, "resetsAt": "2026-10-24T17:30:00.359Z", "resetsIn": "31m"},
                        {"label": "Weekly · all models", "percentUsed": 45, "resetsAt": "2026-10-27T11:00:00.359Z", "resetsIn": "2d 18h"},
                        {"label": "Weekly · <model>", "percentUsed": 0, "resetsAt": "...", "resetsIn": "..."}],
            "extraUsage": {"enabled": false, "currency": "USD"}},
   "context": {"session": "self", "status": "ok", "tokensUsed": 255053, "contextWindow": 500000, "percentUsed": 51, "...": "..."}}
  ```

  `plan.status` is `ok`, `not_applicable` (API key, Bedrock, Vertex, a gateway) or
  `unavailable` (could not be read now; a `note` says whether asking again helps). The 5-hour
  window is the entry labelled "5-hour limit"; the week is "Weekly · all models". `resetsAt` is
  UTC ISO with milliseconds.
- Mapping to `backfill.record_reading`: `five_hour_pct` = 5-hour `percentUsed`, `week_pct` =
  all-models weekly `percentUsed`, `five_hour_resets`/`week_resets` = their `resetsAt`,
  `extra_usage` = `extraUsage.enabled`.
- Older app without it: absent; the welcome asks for the plan in one line (Pro by default).

**update_scheduled_task (and list_scheduled_tasks)**
- Exact names: `mcp__scheduled-tasks__update_scheduled_task` (`taskId` required; optional
  `enabled`, `cronExpression`, `fireAt`, `prompt`, `title`, `description`,
  `notifyOnCompletion`) and `mcp__scheduled-tasks__list_scheduled_tasks` (no arguments; returns
  each task's `taskId`, `title`, `description`, `schedule`, `cronExpression`, `fireAt`,
  `enabled`, `nextRunAt`, `lastRunAt`, `path` to its SKILL.md, and `notRead` when its file could
  not be read).
- Loaded through tool search first: yes (deferred).
- Turning a harness task off: `update_scheduled_task {"taskId": <id>, "enabled": false}` - pauses
  automatic runs; does not delete; manual runs still possible. Deleting is a separate tool
  (`delete_scheduled_task`) the skill never uses.
- Declined card: UNVERIFIED whether it shows a card at all (its description names none). If the
  update is refused the task keeps running; the skill leaves the OS tasks registered (the host
  gate keeps the old runner quiet only if the host moved) and says one line. NOT called live by
  the controller.
- Older app without it: absent; the skill gives the click path (the app's Scheduled tab, the
  task, the switch) for the person.

**Two meters, which can be two accounts.** `get_usage` reads the desktop app's account.
Routines run, and draw usage, under the command line's own sign-in (`claude auth status`). The
two can differ, and on this machine they did. Read at the same moment (2026-10-24 18:15 UTC):

| | Command line (`rate_limit_event`, run 8) | Desktop app (`get_usage`) |
|---|---|---|
| plan | `subscriptionType: "team"` (`claude auth status --json`) | `"plan": "Max"` |
| 5-hour window | utilization 0.03, resets 1792879200 = 2026-10-24T22:00:00Z | 4 %, resets 2026-10-24T22:29:59.816Z |
| weekly window | utilization 0.2, resets 1793246400 = 2026-10-29T04:00:00Z | 47 %, resets 2026-10-27T10:59:59.816Z |
| extra usage | `overageStatus: "rejected"` (off at the organisation) | `extraUsage.enabled: false` |

Two weekly windows that end two days apart cannot be one account's. The controller's earlier
`get_usage` sample (the 5-hour window resetting at 17:30Z) fits the desktop column too.

What each side exposes to check that both are the same account:

- Command line: `claude auth status --json` gives `email`, `orgId`, `orgName` and
  `subscriptionType` (for `authMethod: "claude.ai"`), and every stream-json run gives the
  `rate_limit_event` windows.
- Desktop tools: **none of them exposes an account identity.** `get_usage` gives only the plan
  name and the windows; `get_session` gives no account field (section 8 lists its fields).
- A desktop session's environment does carry `CLAUDE_CODE_USER_EMAIL`,
  `CLAUDE_CODE_ORGANIZATION_UUID` and `CLAUDE_CODE_ACCOUNT_UUID` (undocumented). Here the first
  two equalled the command line's `email` and `orgId` (compared without printing them), yet
  `get_usage` showed another plan and other windows. So those variables do not identify the
  account `get_usage` reports either.

The usable comparison is the windows: trust `get_usage` as the routines' meter only when its
"5-hour limit" and "Weekly · all models" `resetsAt` equal the probe run's
`unifiedWindows.five_hour.resetsAt` and `seven_day.resetsAt` (to the minute; how close two
readings of one account land was not observed, since no matching pair was seen) and its plan
agrees with `subscriptionType`. Otherwise pace from the routines' own `rate_limit_event`, which
every stream-json run prints and which always describes the account the routines draw on.

## 9. The schedulers: what was checked, what is only documented (Task 9)

Routines and the sync task run through the scheduler of the system that catches up after sleep:
the Task Scheduler on Windows (a task XML, `StartWhenAvailable`), a launch agent on macOS, a
systemd user timer on a Linux where `systemctl --user show-environment` exits 0, else cron, which
does not catch up. Checked 2026-10-25 in WSL (Ubuntu, systemd 255.4): `systemctl --user
show-environment` exits 0 and `crontab` exists, so a Linux with both uses the timer.

**Unit files, read by systemd itself.** `systemd-analyze --user verify` was run on unit files that
`onboard.systemd_units` generated into a scratch folder (read only: nothing was linked into
`~/.config/systemd`, enabled or started). Five folder names: a plain one; one with spaces,
accents, `&` and `%`; one with `$` and a backtick; one with an apostrophe; and one with a
backslash, a double quote and `$` (given as text, since the drive cannot hold it). The first
version of the unit text failed three of them, and that shaped the text:

- The first word of `ExecStart=` (the program) must not hold a backslash, a double quote or an
  apostrophe: `Executable name contains special characters`, then `Unit configuration has fatal
  error, unit will not be started`. An argument may hold any of them. Nothing else notices:
  `systemctl --user enable --now` would still succeed and `is-active` read `active`; the unit
  would fail at its first run. The unit therefore starts such a program as the first argument of
  `/bin/sh -c 'exec "$0" "$@"'`.
- In the program word `$` is no variable: written `$$`, `verify` looked for a file named with two
  dollar signs. In arguments `$$` is the escape (systemd.service(5)). `%` is a specifier in both:
  `%%`.
- Spaces, accents, `&`, `%%`, a single `$` and a backtick in the program path pass.
- `verify` reads and never runs, so it does not show that the arguments arrive as intended; that
  rests on the rules in systemd.syntax(7) (quotes, `\\`, `\"`) and on `test_routines.py`'s parser.

  First version: `plain` and `accents` exit 0; `dollar` exit 1 (`Command .../Ada $$HOME `id`/bin dir/python3 is
  not executable: No such file or directory`); `apostrophe` and `backslash` exit 1 (`Executable name contains
  special characters`, `Unit configuration has fatal error, unit will not be started`). Final text: all five
  exit 0.

**Refused, not escaped.** Some folder names are turned away with a plain line, because no escape for them
was seen to work: a defined `%NAME%` in a Windows task's arguments (the Task Scheduler has no documented escape
for a percent sign, and it is not known whether it expands one at all: last section); on a Mac, a control
character anywhere in the words (`plistlib` raises on most of them, so the plist is not built); for systemd, a
control character likewise and a folder name that ends in a backslash
(`WorkingDirectory=/tmp/ws\` continues on the next line and swallows `Environment=`, and `verify` still exits
0 and says nothing; a tab in the program's path is `Executable name contains special characters`, fatal, as
a quote is). Both seen in WSL on units written straight from `systemd_units`.

**cron.** A `%` in a crontab line is a newline unless it is `\%`; the line is read by `sh`, so the
folder names are double-quoted with `\`, `"`, `$` and the backtick escaped. Run for real with Git's `sh` on
Windows and with the odd names `José Pérez`, `Ada & Co's 100% $HOME` + a backtick, `(a;b) {d} ! #x ~`: the program
started in the right folder and got each argument unchanged.

**Documented, not seen** (launchd.plist(5), launchctl(1), systemd.timer(5); the macOS items are in the
last section with their commands). One thing below is not in those pages at all: a `launchctl bootstrap`
straight after a `bootout` of the same label is said, by people who run launch agents, to fail now and then
with `Input/output error` (exit 5) because the bootout lets go a moment later. That is hearsay, not seen here;
`register_launchd` tries up to three times, a second apart, on that guess, and the loop in the last section
settles it.

- launchd starts a calendar job it missed while the Mac slept as soon as the Mac wakes, once for any
  number of missed times; cron skips them. `StartInterval` is held over a sleep the same way.
- `StandardOutPath` and `StandardErrorPath` are opened by launchd, which does not make their folder
  (`~/.spaces/logs` is made before the agent is loaded) and does not expand `~`.
- A launch agent in `gui/<uid>` runs in the person's graphical login session only.
- `Persistent=true` makes a timer run a start missed while the machine was off or asleep on the
  next start of the user manager. A user timer needs the person logged in unless `loginctl
  enable-linger` is on.
- A oneshot service's `systemctl start` waits until the run ends (a routine runs up to an hour):
  `run_now` uses `--no-block`.

## Constants

Copy these verbatim. Regexes are Python `re` patterns for `re.search`, case-sensitive; `\u00b7`
is the ` · ` separator the CLI prints. Check overage before limit before auth: an out-of-credits
text names a reset time too.

```python
# routine.py
# claude -p refuses anything that would ask (section 1); acceptEdits still lets a routine write
# in its working folders.
REFUSE = ["--permission-mode", "acceptEdits", "--permission-prompts", "none"]
# One JSON object per line: hook events, then system/init (tools, mcp_servers), the messages,
# rate_limit_event (plan windows, extra usage), and last the result (usage, is_error, result).
# Find init by type/subtype, the result as the last line whose type is "result".
JSON_OUT = ["--output-format", "stream-json", "--verbose"]

LIMIT_PATTERNS = (
    # "You've hit your session limit · resets 11:20pm (America/New_York)",
    # "You've hit your weekly limit · resets Oct 27, 1pm (America/New_York)"
    r"You[\u2019']ve hit your (?:(?P<limit>session|weekly|Opus|Sonnet|Fable|usage) )?limit \u00b7 resets "
    r"(?P<resets>(?:[A-Z][a-z]{2} \d{1,2}, (?:\d{4}, )?)?\d{1,2}(?::\d{2})?(?:[ap]m)?)(?: \((?P<tz>[^)]+)\))?",
    # no reset time: "You've hit your session limit". This one has only the "limit" group: read
    # the reset with m.groupdict().get("resets"), which is None here.
    r"You[\u2019']ve hit your (?:(?P<limit>session|weekly|Opus|Sonnet|Fable|usage) )?limit\b",
)
OVERAGE_PATTERNS = (
    # f$o in the source: "You're now using usage credits", "You're now using your usage
    # allocation", "You're now using extra usage" ...
    r"You[\u2019']re now using (?:usage credits|your usage allocation|extra usage)",
    # ... "Now using your usage allocation", "Now using usage credits", "Now using extra usage"
    r"\bNow using (?:usage credits|your usage allocation|extra usage)",
    r"You[\u2019']re out of usage credits",
    r"Your org is out of usage",
    r"You[\u2019']ve hit your (?:monthly spend limit|(?:channel|org)[\u2019']s monthly (?:spend|usage) limit"
    r"|individual (?:spend|usage) limit|team[\u2019']s shared budget|usage credit limit)",
    r"You[\u2019']ve reached your Fable limit|requires usage credits",
)
AUTH_PATTERNS = (
    # "Not logged in \u00b7 Please run /login" (observed), "Not logged in \u00b7 Run /login"
    r"Not logged in \u00b7 (?:Please run|Run) /login",
    # "OAuth token revoked \u00b7 Please run /login", "Login expired \u00b7 Please run /login",
    # "Login expired \u00b7 Run /login to sign in again, or re-authenticate your Anthropic profile"
    r"(?:OAuth token revoked|Login expired) \u00b7 (?:Please run|Run) /login",
    # "Authentication required \u00b7 Sign in again to continue"
    r"Authentication required \u00b7 Sign in again",
    # "Invalid API key \u00b7 Fix external API key", "Invalid auth token \u00b7 Fix external auth token"
    r"Invalid (?:API key|auth token) \u00b7 Fix external",
    # "Your organization has disabled API key authentication \u00b7 ...",
    # "Your organization has disabled Claude subscription access for Claude Code \u00b7 ..."
    r"Your organization has disabled (?:API key authentication|Claude subscription access)",
    # "Session expired. Please run /login to sign in again.",
    # "Your session has expired. Please run /login to sign in again."
    r"[Ss]ession (?:has )?expired\. Please run /login",
)
# Only the first text was seen live; the rest are UNVERIFIED-live, quoted from the 2.1.303
# binary in section 5.

# PROPOSAL, not observed: the same outcomes in structured form (stream-json), preferred over the
# text when present: the assistant event's "error" and the rate_limit_event. billing_error is
# labelled "usage limit reached - check plan" in the CLI's own source.
ERROR_KINDS = {"authentication_failed": "auth", "oauth_org_not_allowed": "auth",
               "rate_limit": "limit", "billing_error": "limit"}
# rate_limit_info.isUsingOverage is True -> "overage"; status "rejected" -> "limit", with
# rate_limit_info.resetsAt (Unix seconds) as the reset time.

# Connector sign-in (section 7); the init event's mcp_servers status "needs-auth" comes first.
CONNECTOR_PATTERNS = (
    r'MCP server "(?P<server>[^"]+)" needs you to sign in again',
    r'MCP server "(?P<server>[^"]+)" needs to be connected in claude\.ai',
    r'The MCP server "(?P<server>[^"]+)" needs you to sign in; run /mcp to authenticate',
    r'MCP server "(?P<server>[^"]+)" needs (?:authentication|additional permissions)',
)

# signin.py
GH_CODE = r"one-time code: (?P<code>[A-Z0-9]{4}-[A-Z0-9]{4})"      # on stderr
GH_LINK = r"(?P<link>https://\S+/login/device)"                      # https://github.com/login/device
# "claude" stands for the path find_claude() returns.
CLAUDE_LOGIN = ["claude", "auth", "login", "--claudeai"]            # opens the browser itself
CLAUDE_STATUS = ["claude", "auth", "status", "--json"]              # read loggedIn, not the exit code
CLAUDE_LINK = r"If the browser didn't open, visit: (?:\x1b\]8;[^;]*;)?(?P<link>https://[^\s\x07\x1b]+)"
# PROPOSAL: authMethod -> signin.claude_state. Seen live: "claude.ai" and "none" only.
# "oauth_token" is a long-lived subscription token (CLAUDE_CODE_OAUTH_TOKEN, from
# `claude setup-token`, "requires Claude subscription"), so it bills the plan like an account;
# "third_party" bills a cloud provider.
CLAUDE_STATES = {"claude.ai": "account", "none": "signed-out", "api_key": "api-key",
                 "api_key_helper": "api-key", "oauth_token": "account", "third_party": "api-key"}

# doctor.py
VERSIONS["claude"] = "2.1.303"
_CLAUDE = "https://downloads.claude.ai/claude-code-releases/2.1.303/"
# (system, arch) -> (url, sha256), from the release's signed manifest.json. A bare executable,
# not an archive.
ASSETS["claude"] = {
    ("Linux", "x86_64"): (_CLAUDE + "linux-x64/claude", "375988f457756524234695cf2d8f64ace99c5f68682e47ea24862a843c191450"),
    ("Linux", "arm64"): (_CLAUDE + "linux-arm64/claude", "f384f49826336d4ac0484ac6ac9d8d16f64d244d186ce227cbd7cc088c03315d"),
    ("Darwin", "x86_64"): (_CLAUDE + "darwin-x64/claude", "6c5d63b43568c5d60963d40f05b0087cb03ba44a90740d27adc505f4157806db"),
    ("Darwin", "arm64"): (_CLAUDE + "darwin-arm64/claude", "d2da3b003dc08b88b7230469014fe3e1dc6fe94bc8f648e89b34175d30269370"),
    ("Windows", "x86_64"): (_CLAUDE + "win32-x64/claude.exe", "1787c60619bad78543c67e4d9d8a868869c35a904f2a85eb9ea46c4808af196b"),
    ("Windows", "arm64"): (_CLAUDE + "win32-arm64/claude.exe", "a5d5b594a639dab03da1ea0e1a1aedc1885284cc42e14cb65b7527250bc7ea90"),
}
# musl Linux, for when the doctor detects it (see section 5).
CLAUDE_MUSL = {
    ("Linux", "x86_64"): (_CLAUDE + "linux-x64-musl/claude", "8dfd02ad4590c6f4c419ec73af4f5462223a07f64495e2c2fce45990faf1ede6"),
    ("Linux", "arm64"): (_CLAUDE + "linux-arm64-musl/claude", "a0d9d4dcb99a1782cdfc332fed8510467e99efc7ff2f3d7443fea27d9af55e7e"),
}

# apps.py
SERVER_PREFIX = "mcp__claude_ai_"
# A claude.ai connector named "claude.ai <Name>" gives tools "mcp__" + server_part(name) + "__<tool>".
def server_part(name: str) -> str:
    part = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
    if name.startswith("claude.ai "):
        part = re.sub(r"_+", "_", part).strip("_")
    return part

# The desktop app's own tools (Tasks 13 and 14); each is deferred: load with
# ToolSearch "select:<name>" first. Absent outside the desktop app.
DESKTOP_TOOLS = {
    "get_session": "mcp__ccd_session_mgmt__get_session",                              # {"session_id": "self"}
    "set_session_permission_mode": "mcp__ccd_session_mgmt__set_session_permission_mode",  # card; anything but a confirmed switch = declined
    "get_usage": "mcp__ccd_session_mgmt__get_usage",                                  # {}
    "update_scheduled_task": "mcp__scheduled-tasks__update_scheduled_task",           # {"taskId": id, "enabled": False}
    "list_scheduled_tasks": "mcp__scheduled-tasks__list_scheduled_tasks",             # no arguments
}
```

```python
# onboard.py, the schedulers (Task 9; section 9). The label of a launch agent is LAUNCHD_PREFIX + the task.
LAUNCHD_PREFIX = "com.spaces."
# Windows   register  schtasks /Create /F /TN <task> /XML <file>      run now  schtasks /Run /TN <task>
# macOS     (UNVERIFIED live; the domain is the person's graphical session, gui/<uid>)
#           load      launchctl bootout gui/<uid>/<label>      (error when none is loaded: ignored)
#                     launchctl bootstrap gui/<uid> ~/Library/LaunchAgents/<label>.plist
#           read back launchctl print gui/<uid>/<label>
#           run now   launchctl kickstart gui/<uid>/<label>
# Linux     probe     systemctl --user show-environment           (exit 0 = a user session; verified in WSL)
#           load      systemctl --user daemon-reload; systemctl --user enable --now <task>.timer
#           read back systemctl --user is-active <task>.timer
#           run now   systemctl --user start --no-block <task>.service      (UNVERIFIED live)
#           else cron: crontab -l / crontab <file>; run now = `sh -c <the line's command>` in a session of its own
```

Notes for the tasks that copy them:

- Task 5: a reader that "may only read" is better made with `--tools ""` (no built-in tool at
  all, run 5) plus `--allowedTools <the connector read tools>` than with a disallow list, which
  missed `PowerShell` and other built-in tools that run things (`Monitor`, `Workflow`,
  `CronCreate`, `RemoteTrigger`, `ScheduleWakeup`). With `REFUSE`, a connector tool not allowed
  is refused, not prompted (run 8). Keep `ToolSearch` (for instance `--tools ToolSearch`, not
  tried): in run 8 the model loaded the connector tool through it first, and whether a deferred
  connector tool can be called with no `ToolSearch` at all was not tried (UNVERIFIED-live). Parse the last `result`
  line whether the output is `json` or `stream-json`.
- Task 10: the limit and auth texts are in the result on stdout; match stdout and stderr both.
  A fake for "resets 23:20" matches `LIMIT_PATTERNS`, but the real CLI prints `11:20pm`. Real
  stream-json output for an hour-long routine is large (every message); keep the init summary,
  the rate-limit events and the result in the log rather than all of it.
- Task 4: `gh auth login --web` with stdin closed does not open the browser; open or show the
  link. `claude auth login` opens it by itself.
- Task 6: Slack's connector has `slack_send_message` and Gmail's has `reply` and `forward`
  besides `send_message`; the grouping rules leave all of them out of every list, so they are
  refused like any connector tool that is not allowed (run 8).
- Tasks 11, 13 and 14: `get_usage` describes the desktop app's account, which need not be the
  one routines draw on (section 8, "Two meters").

## Unverified, and how to verify

Each item names the command or check that settles it, for the owner.

- **macOS install.** On an Apple silicon Mac: download `_CLAUDE + "darwin-arm64/claude"`, then
  `shasum -a 256 claude` (expect `d2da3b00...0269370`), `codesign --verify --verbose ./claude`
  (expect "valid on disk"), `chmod 755 claude && ./claude --version` (expect
  `2.1.303 (Claude Code)`), and `xattr -l claude` (a Python download sets no quarantine flag).
  Same for `darwin-x64` on an Intel Mac. Then delete it.
- **macOS sign-in status.** `claude auth status --json; echo $?` signed in and with an empty
  `CLAUDE_CONFIG_DIR`. The credentials are in the login Keychain, not a file; record the exit
  code and whether `loggedIn` reads true.
- **launchd can use the sign-in.** Load a LaunchAgent for the person (`launchctl bootstrap
  gui/$(id -u) ~/Library/LaunchAgents/spike.claude.plist`) whose program is
  `claude auth status --json > /tmp/spike-status.json`; check `loggedIn` is true with the screen
  locked, and see what happens when the person is logged out (the Keychain is locked then).
  `launchctl bootout gui/$(id -u)/spike.claude` and delete the plist after.
- **macOS `-p` output.** One `echo "Reply OK." | claude -p --permission-mode acceptEdits
  --permission-prompts none --output-format stream-json --verbose --model haiku --max-turns 1`
  on the Mac: the init event lists `mcp__claude_ai_*` tools as on Windows.
- **Linux scheduler can use the sign-in.** On a signed-in Linux machine, a systemd user service
  (`systemd-run --user --wait -p StandardOutput=file:/tmp/spike.json claude auth status --json`)
  and a cron line give `loggedIn: true`. In WSL here, `systemctl --user show-environment` and
  `crontab -l` both exit 0, so both schedulers exist; the WSL distro has no Claude sign-in.
- **Windows Task Scheduler can use the sign-in.** A task that runs `claude auth status --json`
  as the person ("run only when user is logged on") writes `loggedIn: true`. The clean-env runs
  here (no `CLAUDE*`/`ANTHROPIC*` variables) used the credentials in the person's own profile.
- **Exit code of a signed-out run on Linux.** Observed 0 in WSL, against the source's
  `process.exit(k?0:1)` and the observed 1 on Windows. Re-run `claude auth status; echo $?` and
  `claude -p "Reply OK." </dev/null; echo $?` with an empty `CLAUDE_CONFIG_DIR` on a Linux
  machine. The constants do not depend on it.
- **A real usage limit** (UNVERIFIED-live): when one is hit, keep the routine log line and check
  the result text against `LIMIT_PATTERNS`, the assistant `error` (`rate_limit`) and the
  `rate_limit_event` (`status: "rejected"`, `resetsAt`).
- **Extra usage starting** (UNVERIFIED-live): on an account with extra usage on, a run past its
  5-hour limit shows `"isUsingOverage": true` in a `rate_limit_event`; check whether any text
  reaches the result.
- **An expired connector's tool call** (UNVERIFIED-live): disconnect a test connector in claude.ai
  settings while a `-p` run that uses it is going, and match the tool result against
  `CONNECTOR_PATTERNS`.
- **`claude auth login` output** (UNVERIFIED-live): run it with an empty `CLAUDE_CONFIG_DIR` and
  stdout captured, and check the link line against `CLAUDE_LINK` (plain or OSC 8), then close
  the browser tab without signing in.
- **gh 2.101.0** (the pinned version): repeat section 6's command with it; the code line and URL
  should be unchanged.
- **gh device flow, the approved path.** Only "keeps polling until killed" was seen. With a test
  GitHub account: start section 6's command (stdin closed, empty `GH_CONFIG_DIR`), approve the
  code in a browser, and record what gh prints after approval, its exit code, and that
  `GH_CONFIG_DIR=<dir> gh auth status` then shows the account (and where the token went:
  credential store or `hosts.yml`).
- **Desktop and command line on one account.** On a machine where the desktop app and
  `claude auth status` are the same account, read `get_usage` and a probe run's
  `rate_limit_event` within a minute of each other and record how close the 5-hour and weekly
  `resetsAt` are, and which `subscriptionType` goes with each `get_usage` plan name (for
  instance "Max" and `max`). Section 8 has a pair from two different accounts only.
- **A reader with only `ToolSearch` built in.** `claude -p` with `REFUSE`, `--tools ToolSearch`
  and `--allowedTools` one connector read tool calls that tool (run 8 used the default tools).
- **A restricted reader reaching a connector.** `--restricted` printed init before the connectors
  connected (section 2), though they loaded a moment later. Whether they reach the first turn is
  not known: run `--restricted --tools ToolSearch --allowedTools
  mcp__claude_ai_Gmail__search_threads --max-turns 4` with a prompt asking for one Gmail search
  that matches nothing, and see whether the tool is called. If it is, readers can add
  `--restricted` (one line in `reader._command`; the probe stays as it is, without it).
- **The files reader's guard (Task 7, Ruling R23).** `reader.read_files` runs `--restricted --tools
  ToolSearch Read` with a `--settings` file holding a `PreToolUse` hook on `Read` (`read_guard.py`).
  Verified once, 2026-10-24 (one init-event probe, no tool called): `--settings` hooks **do run under
  `--restricted`**. The run was `claude -p` with `REFUSE`, `JSON_OUT`, `--model haiku --max-turns 1
  --no-session-persistence --restricted --settings <file with a SessionStart hook that printed a
  marker> --tools ToolSearch`. It printed `hook_started` and `hook_response` for that hook (exit 0,
  the marker on stdout) before `init`, and no other hook line; init reported `mcp_servers` 28, `tools`
  279, `plugins` 4, and the result was `success`. One observation: with a SessionStart hook in the
  run, the claude.ai connectors were in init even under `--restricted` (Task 5 saw them absent under
  `--restricted` without hooks, so the timing of init decides which). Still not run: the read itself.
  In a scratch folder holding one file, with a user settings file that allows `Read`, ask the run to
  read the file in the folder (expected: read) and a file outside it (expected: refused, listed in
  `permission_denials`, `PreToolUse:Read hook error`). The hook command ends in `|| exit 2`, so a
  guard that cannot start blocks too (checked with Git Bash, Git `sh` and `cmd.exe`, not with the CLI).
  The hooks documentation says hook commands run in `sh -c`, or Git Bash on Windows, and in
  PowerShell only when Git Bash is missing; PowerShell 5.1 and 7 do not parse this command (exit 1,
  which does not block), so on such a machine the guard rests on `--restricted` and the default rules.
- **The doctor's own copy of `claude` and auto-update.** Whether a binary run from
  `~/.spaces/tools/claude/` updates itself, and where to: run it once with
  `DISABLE_AUTOUPDATER` unset and look at `claude doctor`'s install and auto-update lines.
- **The desktop tools' declined cards** (from section 8): `set_session_permission_mode`'s refusal
  text and whether `update_scheduled_task` shows a card at all, both not called live.
- **A launch agent, end to end (Task 9, macOS).** Run from the root of a checkout of this template (the folder
  that holds `_meta/tools`), in the scratch name below, never the person's own. `PY` is the interpreter
  onboarding ran with: it needs PyYAML, which the system `python3` on a Mac does not have. Write each exit
  code down:

  ```sh
  PY=~/.spaces/venv/bin/python
  UIDN=$(id -u); LABEL=com.spaces.space-test-1; AGENT=~/Library/LaunchAgents/$LABEL.plist
  mkdir -p ~/Library/LaunchAgents ~/.spaces/logs     # a fresh Mac has neither; launchd makes no log folder
  make_agent() {   # make_agent HOUR MINUTE: an agent that runs `echo hello` at that time every day
    "$PY" -c 'import sys; sys.path.insert(0, "_meta/tools"); import onboard
  print(onboard.launch_agent("space-test-1", sys.argv[1], "/bin/echo", ["hello"], [(int(sys.argv[2]), int(sys.argv[3]))], None), end="")' "$PWD" "$1" "$2" > "$AGENT"
  }
  make_agent 9 0
  launchctl bootstrap gui/$UIDN "$AGENT"; echo "bootstrap exit $?"                     # expect 0
  launchctl print gui/$UIDN/$LABEL > ~/.spaces/logs/space-test-1.print; echo "print exit $?"   # expect 0
  grep -i -A3 'calendar\|interval' ~/.spaces/logs/space-test-1.print                   # the 09:00 start is listed
  launchctl kickstart gui/$UIDN/$LABEL; echo "kickstart exit $?"; sleep 2             # expect 0
  cat ~/.spaces/logs/space-test-1.log                                                  # expect: hello
  launchctl bootout gui/$UIDN/$LABEL; echo "bootout exit $?"                           # expect 0
  launchctl print gui/$UIDN/$LABEL; echo "print after bootout exit $?"                 # expect non-zero, "Could not find service"
  # the race: a bootstrap straight after a bootout of the same label. Does any exit 5 (Input/output error)?
  for i in $(seq 10); do launchctl bootout gui/$UIDN/$LABEL; launchctl bootstrap gui/$UIDN "$AGENT"; echo "$i bootstrap exit $?"; done
  launchctl bootout gui/$UIDN/$LABEL
  rm -f "$AGENT" ~/.spaces/logs/space-test-1.log ~/.spaces/logs/space-test-1.print
  ```

  `register_launchd` tries a bootstrap three times, a second apart, on the guess that the race is real.
- **launchd runs a missed calendar start on waking** (Task 9, macOS). With the shell functions and variables of
  the item above:

  ```sh
  make_agent $(date -v+5M +"%H %M"); launchctl bootstrap gui/$UIDN "$AGENT"     # a start five minutes ahead
  pmset sleepnow                    # wake the Mac after the start time has passed
  cat ~/.spaces/logs/space-test-1.log   # expect one hello, shortly after waking
  ```

  Then the same with the lid closed on battery, and with the person logged out (expect nothing: `gui/<uid>` is
  gone). Clean up as above (`bootout`, `rm`).
- **The launch agent can use the Claude sign-in** (extends "launchd can use the sign-in" above): the agent of the
  item above running `claude auth status --json` instead of `echo`, while the screen is locked.
- **`systemctl --user start --no-block <task>.service`** (Task 9): with a throw-away oneshot service that runs
  `sleep 30`, the command returns at once and `systemctl --user is-active` reads `activating` for 30
  seconds; without `--no-block` it returns after 30. And `Persistent=true` on a real wake: set a timer five
  minutes ahead, suspend past it, wake: `systemctl --user list-timers` shows it run.
- **`%NAME%` in a Windows task's arguments** (Task 9): not known whether the Task Scheduler expands it, the way
  it expands the `<Command>` the task uses (`%SystemRoot%`). `onboard` assumes it does and refuses a folder name
  with a defined `%NAME%` in it (a lone `100%` is allowed). To settle it, a throw-away task whose arguments are
  `--headless cmd /c echo %USERNAME%>out.txt`: read `out.txt` (the user name = expanded). If it is not
  expanded, `windows_expands` and its refusal can go.
