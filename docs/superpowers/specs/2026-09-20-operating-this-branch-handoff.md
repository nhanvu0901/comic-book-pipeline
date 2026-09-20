# Handoff — operating this branch: two machines, one loop

Date: 2026-09-20
Branch: `feat/scout-single-selection-flow`, head `143f075`, pushed, tree clean.
**35 commits ahead of `main`, none reviewed, none merged.**

This is the operational half of the handoff set: how to run the thing, deploy it, and
avoid the traps that cost time in the session that built it. The design handoffs are
listed in section 9 — read this one first if you have never touched the two-machine setup.

---

## 1. The two machines and what each is for

| | macOS | Windows |
|---|---|---|
| Path | `~/Documents/code/comic-book-pipeline` | `D:\code\comic-book-pipeline` |
| Role | write code, run the suite, push | run the Flet app, hold the real `.env` and API keys |
| Python | `python3` (system) | `.venv\Scripts\python.exe`, Python 3.14 |
| Git remote | HTTPS (push works) | SSH (`git@github.com:…`) |

**Write code on the Mac.** The Windows checkout is for running the app and for tests
against Python 3.14. Both have run the suite green; the Mac is faster and has no CRLF
complications (section 5).

The Windows box also holds sessions and projects you cannot get anywhere else —
`research_sessions/`, `projects/` — so it is where you reproduce a reported failure.

## 2. Reaching the Windows box

Three SSH aliases in `~/.ssh/config` on the Mac:

```
Host winbox 100.125.22.186     # VPN (NetBird wt0) -> lands in WSL Ubuntu
Host winbox-cmd                # VPN -> Windows cmd
Host winbox-lan  192.168.2.63  # same LAN -> Windows cmd
```

`winbox` sets `RemoteCommand wsl.exe ~`, so a bare `ssh winbox` drops into Ubuntu. That
also means **`ssh winbox 'some command'` fails** with *"Cannot execute command-line and
remote command"*. Pass `-o RemoteCommand=none` or use `winbox-cmd`.

### The trap that will cost you fifteen minutes

**The VPN drops.** NetBird runs on the Mac but is not always assigned a `100.x` address;
Tailscale is usually stopped. When that happens `ssh winbox` dies with `Network is
unreachable` and it looks like the Windows box is down. It is not.

Check before you debug anything else:

```bash
ifconfig | grep "inet " | grep -E "100\.|192\.168"   # does the Mac have a 100.x at all?
ping -c2 192.168.2.63                                 # LAN reachable?
```

Both machines sit on the same Wi-Fi (`BELL997`, `192.168.2.0/24`). **The Mac is
`192.168.2.60`** — so `winbox-lan` works whenever the VPN does not. Use it by default; it
is also faster.

## 3. The deploy loop

Every deployment in this session followed the same five steps. Do not skip the test step
on Windows: Python 3.14 has caught things macOS did not.

```bash
# 1. Mac — commit and push
cd ~/Documents/code/comic-book-pipeline
python3 -m pytest -q                      # see section 6 for the expected result
git add -A && git commit && git push

# 2. Windows — pull, through WSL git (it has the SSH key; Windows git does not)
ssh winbox-lan 'wsl -e bash -c "cd /mnt/d/code/comic-book-pipeline && git fetch origin --prune && git merge --ff-only origin/feat/scout-single-selection-flow"'

# 3. Windows — run the affected tests under 3.14
ssh winbox-lan 'cd /d D:\code\comic-book-pipeline && .venv\Scripts\python.exe -m pytest tests/test_research_scout_workflow.py -q'

# 4. Windows — stop the old server (the port will not free itself)
ssh winbox-lan 'netstat -ano | findstr ":8550 "'     # read the PID off the LISTENING row
ssh winbox-lan 'taskkill /PID <pid> /F'

# 5. Windows — start it again
ssh winbox-lan 'cd /d D:\code\comic-book-pipeline && .venv\Scripts\python.exe -m ui --lan --port 8550'
```

Quoting through `ssh → wsl → bash` is miserable. For anything longer than one line, write
the script locally and pipe it base64-encoded:

```bash
B64=$(base64 < script.sh | tr -d '\n')
ssh winbox-lan "wsl -e bash -c \"echo $B64 | base64 -d | bash\""
```

For PowerShell, the same idea with `-EncodedCommand` and UTF-16LE:

```bash
B64=$(python3 -c "import base64;print(base64.b64encode(open('s.ps1','rb').read().decode('utf-8').encode('utf-16-le')).decode())")
ssh winbox-lan "powershell -NoProfile -EncodedCommand $B64"
```

### Windows git cannot reach GitHub

`C:\Users\ADMIN\.ssh\` holds only `known_hosts`. The SSH key lives in WSL at
`/home/nhan/.ssh/id_ed25519` and is registered on the GitHub account. So **all git
operations on the Windows box go through WSL git**, as step 2 does. Copying the key to the
Windows side would work and was never done.

## 4. Running the server

```
python -m ui --lan --port 8550     # binds 0.0.0.0, no authentication
D:\code\comic-book-pipeline\run_ui_lan.bat
```

Reachable at `http://192.168.2.63:8550` from anything on `BELL997`.

**It has no authentication** — the module docstring says so plainly. Anyone who can reach
the port can edit narration, approve the render gate, and read every file under
`projects/` over HTTP. Fine on a home network, not fine anywhere else.

Already configured, do not redo:

- Firewall rule `Comic UI 8550`, inbound TCP, **Private profile only**
- The `Wi-Fi 6` / `BELL997` network category is set to **Private** (it defaulted to Public,
  which is why the firewall rule did nothing at first)

### A server started over SSH dies with the session

Windows OpenSSH kills the process tree when the session ends, so `Start-Process` from an
SSH command does not survive. Two things that do:

- Run it in a **foreground SSH command held open** by a background job on the Mac. This is
  what the session did; it lasts as long as that job.
- Run `run_ui_lan.bat` in a PowerShell window **on the machine itself** and leave it open.

A Scheduled Task would be the proper answer. It was attempted and refused by the
permission layer as unauthorized persistence; if you want it to survive reboots, a human
has to create it.

### Restarting always means killing first

Starting a second instance gives `[Errno 10048] only one usage of each socket address` —
a full uvicorn traceback that reads like a crash but means "port busy". If you see it,
`netstat -ano | findstr :8550` and kill the PID.

## 5. The CRLF trap in WSL

Read the same checkout with Windows git and WSL git and you get different answers:

```
Windows git:  ~73 changed entries
WSL git:      501 changed entries
```

WSL git has no `core.autocrlf` and reads `/mnt/d` through drvfs, so every file looks
modified (line endings) and executable (filemode). It is an illusion — **no source file is
actually modified**. The repo config on the Windows box has been set to match:

```
core.autocrlf = true
core.filemode = false
```

After that WSL git agrees with Windows git. If you ever see ~500 modified files there,
check these two settings before believing it. **Never commit from WSL on `/mnt/d` without
them** — you will commit tens of thousands of lines of line-ending churn.

## 6. Tests

```bash
python3 -m pytest -q          # Mac, from the repo root. Do NOT activate .venv-chatterbox.
```

**Expected: 3 failed, ~1536 passed, 11 skipped.** The three are pre-existing, ffmpeg-driven
and unrelated to anything in this branch:

- `tests/art/test_assemble.py::test_render_chapter_card`
- `tests/art/test_assemble.py::test_overlay_chapter_cards_preserves_duration`
- `tests/test_outro_card.py::test_build_outro_card_makes_clip_of_right_duration`

Do not fix them. Do report if the count moves.

Runtime swings between about 40s and 150s. Four tests own most of it and none touch the
scout:

```
20.5s  tests/art/test_narrate_longform.py::test_new_narration_deletes_stale_hunt_manifest
12.6s  tests/test_micro_moment.py::...parses_all_three_ending_styles[thesis-...]
10.5s  tests/test_anchor_over_emit.py::test_landing_keeps_writers_last_scene_when_over_emitted
 5.7s  tests/test_review_gate.py::test_build_candidates_schema
```

### Nothing stops a test opening a socket

Two test files were silently making real network calls once gating started fetching cited
URLs. **They still passed** — just slower, failing DNS. It was caught from a jump in run
time, not a red test. Four files now carry an autouse fixture closing off
`cited_sources.urllib.request.urlopen`, but that is one patch per file. A global guard in
`conftest.py` would end the class of bug and does not exist.

### Watch for environment-dependent assertions

`test_specific_audit_records_evidence_model_and_prompt_hash` hardcoded the model name. It
was green on the Mac and red on Windows the moment a `.env` named a different model. It
now reads `config.SCOUT_EVIDENCE_MODEL`. If a test is green on one machine and red on the
other, suspect a hardcoded config value before suspecting the platform.

## 7. Configuration

Live values on the Windows box:

```
SCOUT_PLANNER_MODEL   = z-ai/glm-flash-latest        (OpenRouter)
SCOUT_EVIDENCE_MODEL  = z-ai/glm-flash-latest        (OpenRouter)
YOUCOM_DISCOVER_EFFORT= standard
YOUCOM_GENERAL_EFFORT = deep
YOUCOM_VERIFY_EFFORT  = deep
```

Effort is per phase, following the three stages `stages/youcom_scout.py` has documented
since the 2026-08-05 pilot: discovery is a cheap broad sweep, enumerating answers and
confirming one item each take the deep pass. Defaults live in `config.py`; nothing needs to
be in `.env` unless you are overriding.

**Deep is expensive.** One session is now one deep `/v1/research` for the general round
plus one deep `/v1/research` per selected candidate — three to five more. If cost bites,
drop either knob to `standard` in `.env`; no code change.

`.env` lives only on the machines and is gitignored. Both edits made in this session left
timestamped backups beside it (`.env.bak.*`, also ignored). The You.com key was rotated
into it on 2026-09-20; it was pasted through a chat log first, so it should be rotated
again.

### The endpoints in play

```
api.you.com/v1/research    general round + Tier B question discovery + per-candidate verification
api.you.com/v1/search      nothing any more — the scout stopped using it on 2026-09-20
openrouter.ai/api/v1       planner, and the evidence gate verdict
r.jina.ai                  reads a cited page as markdown; free tier, no key, rate limited
comicvine.gamespot.com/api cross-check in build_contexts; never blocks, currently broken for QA
```

`YouComClient.search()` still exists and now has no caller in the scout. Left in place as
part of the client's surface.

## 8. Repo conventions worth knowing before your first commit

**`docs/` is in `.gitignore`, but `docs/superpowers/**` files are tracked.** Adding a new
one needs `git add -f`. This catches everyone once.

**Prompt files are versioned, not edited in place**, when the change is structural — a new
placeholder or a different shape. `general_qa.v2.md`, `discover_qa.v2.md`,
`evidence_gate.v2.md`, `specific_qa.v2.md` all exist beside their v1. The mapping lives in
`_TEMPLATE_FILES` in `stages/research_scout/policies.py`. A pure wording tweak is edited in
place.

**Placeholders are validated.** `_ALLOWED_PLACEHOLDERS` in the same file rejects any
`{name}` a template uses that is not on the list, and `render()` rejects a call that omits
one the template needs. Adding a placeholder means touching that set.

**Commit messages** are lowercase `type(scope): summary`, and the body says *why* rather
than what. Read `git log --oneline -20` before writing one.

## 9. What is outstanding

Design handoffs in `docs/superpowers/specs/`, in the order they were written:

- `2026-09-19-stage1-scout-flow-design.md` — the single-selection flow. **Done.**
- `2026-09-19-scout-rescout-keeping-confirmed-design.md` — re-scout keeping confirmed. **Done.**
- `2026-09-19-evidence-gate-feed-it-the-cited-sources.md` — fetch what the candidate cited. **Done.**
- `2026-09-19-scout-error-reporting-handoff.md` — three UI error defects. **Done.**
- `2026-09-19-override-should-carry-through-to-creation-handoff.md` — **§7 is the important
  one and is only partly done.** Read it.

### Still open

**The issue number is still never verified in Stage 1.** `series_issue_year` is a free-text
string; `_has_exact_issue_and_year` only regex-checks that the text *looks* like it has a
`#N` and a year. The new `claim_citation` binding requires a verbatim quote from the cited
page, but **nothing requires that quote to mention the issue number** — so a real sentence
from a real article about a different issue still passes. That is exactly the candidate-4
failure the whole §7 work was aimed at, and it survives. The fix is cheap and
deterministic: require the issue number to appear in the quoted sentence.

**`verify_issue` is broken for QA.** All three items of the last real run came back
`verified: False`: two because `entity` holds a description of the cost rather than a
character name, one because `Vol. 3` is not stripped before the Comic Vine lookup —
`resolve_reader_url` learned to strip it, `verify_issue` did not. The flag is currently
meaningless for QA; do not lean on it.

**Fetched cited pages are not written to the session artifact.** `search.<id>.v1.json`
records only the You.com call, so a verdict that turned on a fetched CBR article cannot be
reconstructed from disk.

**`stages/youcom_scout.py` hardcodes its own eight-domain list** including `reddit.com`,
now inconsistent with `research_policies/source_profiles.v1.json`, where it was removed
from the verification profile. That script is standalone legacy and was left alone.

**No global socket guard in `conftest.py`** (section 6).

**Thirty-five commits, no review.** A `/code-review` pass before merging to `main` is worth
the time.
