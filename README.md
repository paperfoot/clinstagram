<div align="center">

<img src="https://raw.githubusercontent.com/paperfoot/clinstagram/main/assets/brand/logo.png" alt="clinstagram" width="720">

### Let your AI agent run your Instagram.

**Post, reply, answer DMs and read the numbers, from one command line your agent already knows how to use.**

<br />

[![Star this repo](https://img.shields.io/github/stars/paperfoot/clinstagram?style=for-the-badge&logo=github&label=%E2%AD%90%20Star%20this%20repo&color=yellow)](https://github.com/paperfoot/clinstagram/stargazers)
&nbsp;&nbsp;
[![Follow @longevityboris](https://img.shields.io/badge/Follow_%40longevityboris-000000?style=for-the-badge&logo=x&logoColor=white)](https://x.com/longevityboris)

[![PyPI](https://img.shields.io/pypi/v/clinstagram?style=for-the-badge&logo=pypi&logoColor=white)](https://pypi.org/project/clinstagram/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-3776ab?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green?style=for-the-badge)](https://github.com/paperfoot/clinstagram/blob/main/LICENSE)

</div>

---

Running an Instagram account is a hundred small jobs a day: publish the post, put up the story, answer the comments, clear the message requests, check what worked. Each is easy. Together they eat the day.

clinstagram hands those jobs to an AI agent. Claude Code, Codex, Cursor, OpenClaw or a plain cron script calls it like any other command and gets short, clean JSON back. No browser to drive, no screen to scrape. The agent reads, decides and acts; you set the rules.

## What your agent can do

- **Publish.** Photos, carousels, reels and stories with link stickers and mentions. Fix a caption after the fact, or take a post down.
- **Keep up with people.** Read and answer comments, pin the best one, clear message requests, reply to DMs, mark threads read.
- **Look after the profile.** Name, bio and profile picture, a switch to a Creator or Business account, and the activity feed of new followers, likes and mentions.
- **Read the numbers.** Reach, saves and shares per post, account insights, follower counts.
- **Find people.** Search users and hashtags, see who follows an account, who liked a post, where someone is tagged. Download any post or reel.
- **Run several accounts.** Each keeps its own login, device identity and proxy. Switch with one word.

## Getting started

Ask your agent:

> Install clinstagram with `pip install clinstagram`, run `clinstagram agent-info`, then log me in to Instagram.

That's the whole setup. `agent-info` tells the agent every command and option, so it needs no manual. To do it by hand:

```bash
pip install clinstagram
clinstagram auth login -u your_username
clinstagram profile show
```

clinstagram needs Python 3.10 or newer, and ffmpeg if you post video.

## Safe by default

Instagram punishes accounts that act like bots, so clinstagram is careful out of the box:

- **Reading is open, writing is opt-in.** The default mode uses Meta's official API wherever it can and the unofficial one only to read. Posting and messaging through the unofficial API wait until you switch on `private-enabled`.
- **Follows and likes need a second key.** Every such call must carry `--enable-growth-actions`, so an agent can't start mass-liking by accident.
- **It paces itself.** Writes are spaced out with random delays, and hourly and daily caps on DMs, follows and likes live in `~/.clinstagram/config.toml`.
- **Secrets stay in your keychain.** Logins and tokens go to the macOS Keychain, Secret Service on Linux or Windows Credential Manager. Nothing sits on disk in plain text.

Give a new account a week of normal use before you automate it. Instagram trusts new accounts least: they see fewer posts and hit limits sooner.

## Made for agents

Every command answers in compact JSON: the fields an agent needs, without the long image links that waste its context. `--full` brings everything back; `--fields id,caption` returns only what you ask for.

When something goes wrong, the exit code says what kind of problem it is (bad input, expired login, rate limit, blocked by your settings) and the error carries the exact fix, so the agent can recover without asking you.

clinstagram reaches Instagram two ways and picks one per command: Meta's official Graph API for Business and Creator accounts, and [instagrapi](https://github.com/subzeroid/instagrapi) for everything the official API can't do. `--backend` forces either.

## The official API

For Business and Creator accounts, connect Meta's official API as well. Posts, reels, comments and DMs then go through it, and the unofficial API is kept for the few things Meta doesn't offer, such as link stickers on stories. That is far less likely to get an account restricted.

You need a Meta developer app with the Instagram API use case, and the account added to it as an Instagram tester. The app's dashboard then gives you a token for that account. Hand it to clinstagram with the account's Instagram ID, which the dashboard shows next to it:

```bash
pbpaste | clinstagram --account brand auth connect-fb --token-stdin --ig-user-id <id>
clinstagram account list
```

`account list` shows every account, its username, and in one line what it can and can't do, so an agent always knows which account to use for what.

## Running a channel

[The news-channel playbook](https://github.com/paperfoot/clinstagram/blob/main/docs/news-channel-playbook.md) lays out a full day for a niche news account: the morning post and story, the comment and DM rounds, the evening numbers, and how to find the people worth reaching. It's written around [AgingResearch.News](https://agingresearch.news), and the routine carries over to any channel.

## Contributing

Bug reports and pull requests are welcome. See [CONTRIBUTING.md](https://github.com/paperfoot/clinstagram/blob/main/CONTRIBUTING.md).

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
