<div align="center">

<img src="https://raw.githubusercontent.com/paperfoot/clinstagram/main/assets/logo.png" alt="Clinstagram" width="128">

# Clinstagram

**The Instagram CLI that AI agents actually use.**

<br />

[![Star this repo](https://img.shields.io/github/stars/paperfoot/clinstagram?style=for-the-badge&logo=github&label=%E2%AD%90%20Star%20this%20repo&color=yellow)](https://github.com/paperfoot/clinstagram/stargazers)
&nbsp;&nbsp;
[![Follow @longevityboris](https://img.shields.io/badge/Follow_%40longevityboris-000000?style=for-the-badge&logo=x&logoColor=white)](https://x.com/longevityboris)

<br />

[![PyPI](https://img.shields.io/pypi/v/clinstagram?style=for-the-badge&logo=pypi&logoColor=white)](https://pypi.org/project/clinstagram/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-3776ab?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green?style=for-the-badge)](https://github.com/paperfoot/clinstagram/blob/main/LICENSE)
[![Tests](https://img.shields.io/badge/tests-156%20passing-brightgreen?style=for-the-badge)](https://github.com/paperfoot/clinstagram/tree/main/tests)

---

One CLI. Two backends. Zero browser automation. Clinstagram wraps the official Meta Graph API and the instagrapi private API behind a single command-line interface with structured JSON output. Policy-driven routing picks the safest path for every command automatically.

[Install](#install) | [How It Works](#how-it-works) | [Commands](#commands) | [Contributing](#contributing)

</div>

## Why This Exists

Every Instagram automation tool makes you choose: official API (safe but limited) or private API (full-featured but risky). Clinstagram gives you both.

You run one command. The router checks your compliance mode, inspects your configured backends, and picks the safest path. No Playwright. No Selenium. No headless browsers. Just structured JSON output with exit codes that AI agents can parse.

```bash
$ clinstagram --json dm inbox
{"exit_code":0,"data":[{"thread_id":"839201","thread_title":"alice", ...}],"backend_used":"graph_fb"}
```

## Install

Requires **Python 3.10+**.

```bash
pip install clinstagram
```

Or from source:

```bash
git clone https://github.com/paperfoot/clinstagram.git
cd clinstagram
pip install -e ".[dev]"
```

### Quick Start

```bash
# 1. Log in (username, email, or phone — locale auto-detected)
clinstagram auth login -u your_username

# 2. Optional: connect official Graph API tokens
clinstagram auth connect-ig --token <instagram-login-token>
clinstagram auth connect-fb --token <facebook-login-token>

# 3. Check what is configured
clinstagram --json auth status

# 4. Start using it
clinstagram --json dm inbox
clinstagram --json analytics profile
clinstagram --json post photo cat.jpg --caption "via clinstagram"
```

> **Note:** `--json`, `--proxy`, and `--account` are **global flags** and go **before** the command name.

## How It Works

Clinstagram routes every command through a policy engine that picks the best backend based on your compliance mode and available credentials.

```
CLI Command
    ↓
Policy Router (capability matrix x compliance mode)
    ↓
┌──────────┬──────────┬────────────┐
│ graph_ig │ graph_fb │  private   │
│ (OAuth)  │ (OAuth)  │(instagrapi)│
│ Post     │ Post+DM  │ Everything │
│ Comments │ Stories  │ + proxy    │
│Analytics │ Webhooks │ + keychain │
└──────────┴──────────┴────────────┘
```

### Three Backends

| Backend | Auth | Best For |
|---------|------|----------|
| `graph_ig` | Instagram Login token | Posting, comments, analytics |
| `graph_fb` | Facebook Login token | Above + DMs, story publishing |
| `private` | Username/password/2FA | Everything. Cold DMs. Personal accounts. |

### Three Compliance Modes

| Mode | Official API | Private API | Risk |
|------|-------------|-------------|------|
| `official-only` | Full | Disabled | Zero |
| `hybrid-safe` | Full | Read-only | Low |
| `private-enabled` | Full | Full | High |

Default is `hybrid-safe`. You get official API for everything it supports, plus private API for read-only operations like viewing stories.

## Commands

### Posting

```bash
clinstagram --json post photo <path|url> --caption "..." --tags "@user"
clinstagram --json post video <path|url> --caption "..."
clinstagram --json post reel <path|url> --caption "..."
clinstagram --json post carousel img1.jpg img2.jpg --caption "..."
```

### Direct Messages

```bash
clinstagram --json dm inbox --unread --limit 10
clinstagram --json dm thread @alice --limit 20
clinstagram --json dm send @alice "Thanks for reaching out!"
clinstagram --json dm send-media @alice photo.jpg
clinstagram --json dm search "project"
```

### Stories

```bash
clinstagram --json story list
clinstagram --json story list @alice
clinstagram --json story post-photo photo.jpg --mention @alice
clinstagram --json story post-video clip.mp4 --link "https://..."
clinstagram --json story viewers <story_id>
```

### Analytics

```bash
clinstagram --json analytics profile
clinstagram --json analytics post <media_id>
clinstagram --json analytics hashtag "photography"
```

### Comments

```bash
clinstagram --json comments list <media_id|shortcode|url> --limit 50
clinstagram --json comments reply <comment_ref> "Great point!"
clinstagram --json comments delete <comment_ref>
```

### Followers

```bash
clinstagram --json followers list --limit 100
clinstagram --json followers following
clinstagram --json --enable-growth-actions followers follow @user
clinstagram --json --enable-growth-actions followers unfollow @user
```

### User Lookup

```bash
clinstagram --json user info @username
clinstagram --json user search "alice" --limit 10
clinstagram --json user posts @username --limit 10
```

### Auth & Config

```bash
clinstagram agent-info               # Machine-readable manifest (ACF)
clinstagram doctor                   # Health check (sessions, environment)
clinstagram update --check           # Check for CLI updates
clinstagram auth status              # Show configured backends
clinstagram auth probe               # Live-check token validity
clinstagram auth login -u user       # Private API login
clinstagram auth connect-ig          # Store Instagram Login token
clinstagram auth connect-fb          # Store Facebook Login token
clinstagram auth logout --yes        # Clear all sessions

clinstagram config mode hybrid-safe  # Set compliance mode
clinstagram config set proxy socks5://localhost:1080
```

### Global Flags

| Flag | Description |
|------|-------------|
| `--json` | JSON output (auto-enabled when piped) |
| `--account <name>` | Switch between stored accounts |
| `--backend auto\|graph_ig\|graph_fb\|private` | Force a specific backend |
| `--proxy <url>` | SOCKS5/HTTP proxy for private API |
| `--dry-run` | Preview without executing |
| `--enable-growth-actions` | Unlock follow/unfollow |

## For AI Agents

Clinstagram is built for AI agents like [OpenClaw](https://github.com/openclaw/openclaw). Every command returns structured JSON with a `backend_used` field so your agent knows which path was taken.

Exit codes tell the agent exactly what happened:

| Exit Code | Meaning | Agent Action |
|-----------|---------|--------------|
| 0 | Success | Parse JSON |
| 1 | Bad arguments | Fix command |
| 2 | Auth error | Run `auth login` |
| 3 | Rate limited | Retry after `retry_after` seconds |
| 4 | API error | Retry or report |
| 5 | Challenge required | Prompt user (2FA) |
| 6 | Policy blocked | Change compliance mode |
| 7 | Capability unavailable | Connect additional backend |

Every error includes a `remediation` field with the exact fix:

```json
{"exit_code": 2, "error": "session_expired", "remediation": "Run: clinstagram auth login"}
```

### OpenClaw Integration

```bash
pip install clinstagram
# The included SKILL.md tells OpenClaw what commands are available
```

## Configuration

```toml
# ~/.clinstagram/config.toml
[rate_limits]
graph_dm_per_hour = 200       # Meta's hard limit
private_dm_per_hour = 30      # Conservative default
private_follows_per_day = 20  # Below Instagram's threshold
request_delay_min = 2.0       # Seconds between private API writes
request_delay_max = 5.0
request_jitter = true         # Randomized delays
```

Secrets are stored in your OS keychain (macOS Keychain, Linux Secret Service, Windows Credential Manager). No plaintext tokens on disk.

## Contributing

Contributions are welcome. See [CONTRIBUTING.md](https://github.com/paperfoot/clinstagram/blob/main/CONTRIBUTING.md) for guidelines.

```bash
git clone https://github.com/paperfoot/clinstagram.git
cd clinstagram
pip install -e ".[dev]"
pytest tests/ -v   # 120 tests
```

## License

[MIT](https://github.com/paperfoot/clinstagram/blob/main/LICENSE)

---

<div align="center">

Built by [Boris Djordjevic](https://github.com/longevityboris) at [199 Biotechnologies](https://github.com/199-biotechnologies) | [Paperfoot AI](https://paperfoot.ai)

<br />

**If this is useful to you:**

[![Star this repo](https://img.shields.io/github/stars/paperfoot/clinstagram?style=for-the-badge&logo=github&label=%E2%AD%90%20Star%20this%20repo&color=yellow)](https://github.com/paperfoot/clinstagram/stargazers)
&nbsp;&nbsp;
[![Follow @longevityboris](https://img.shields.io/badge/Follow_%40longevityboris-000000?style=for-the-badge&logo=x&logoColor=white)](https://x.com/longevityboris)

</div>
