# Running a news channel with clinstagram

How an agent runs an Instagram channel for a niche news site (built around AgingResearch.News) with clinstagram and the god-of-sales skills (`gos-news-media`, `gos-shortform-organic`, `gos-cold-outbound`). Every step below is a CLI call, so a scheduler or an agent can run the loop unattended.

## Setup, once

```bash
clinstagram auth login -u <channel_account>
clinstagram config mode private-enabled        # writes (posting, DMs, replies) need it
clinstagram --json profile edit --name "Aging Research News" \
  --bio "Peer-reviewed aging research, every day. Follow a subject ↓"
clinstagram --json profile picture assets/arn-avatar.png
clinstagram --json profile show                # confirm
```

Add the website link in the Instagram app (Edit profile → Links): Instagram ignores the website field when it's set through the API. Use `https://agingresearch.news/?utm_source=instagram&utm_medium=bio`.

Insights (`analytics insights`, `analytics account`) need a Business or Creator account.

Run the channel from an account Instagram already trusts. A brand-new account with no posts gets profile feeds withheld (`user posts` then fails with "Instagram returned 0 of N posts"), and writes from it draw challenges. Warm it up for a week first: a few posts and stories, normal browsing, no bulk actions.

## The daily loop

| When | Step | Command |
|---|---|---|
| Morning | Publish the day's lead story as a carousel: the claim, organism and n, the effect size, the caveat, then the one ask | `clinstagram --json post carousel s1.png s2.png s3.png s4.png --caption "..."` |
| Morning | Story with a link sticker to that story's page, which carries the Follow for its subject | `clinstagram --json story post-photo card.png --link "https://agingresearch.news/s/...?utm_source=instagram&utm_medium=story"` |
| Midday | Second story as a reel (a 20–40 s cut of the hook stat) | `clinstagram --json post reel hook.mp4 --caption "..."` |
| Every 2–3 h | Read the activity feed, reply to every real comment, like the good ones, pin the best | `clinstagram --json activity` · `comments list <post>` · `comments reply <ref> "..."` · `comments like <ref>` · `comments pin <ref>` |
| Every 2–3 h | Clear message requests and reply | `clinstagram --json dm pending` · `dm approve <thread>` · `dm thread <thread>` · `dm send <thread> "..."` · `dm seen <thread>` |
| Evening | Score the day's posts | `clinstagram --json analytics insights <post>` · `analytics account` |

Captions follow the `gos-news-media` content spec: the answer in the first line, the organism and sample size stated ("in mice", "n=18, not yet replicated"), the paper's DOI in the caption, and one ask at the end: "Follow <subject> on AgingResearch.News, link in bio." One ask per post, never a menu.

## Prospecting: the covered-author play on Instagram

The best measured channel for AgingResearch.News is a note to each covered paper's author (6.9% of sends signed up, Oct 2026). Instagram adds a second touch to that note:

1. Find the author or their lab: `clinstagram --json user search "<lab or name>" --limit 5`, then `user info @handle`.
2. Tag them in the story about their paper: `story post-photo card.png --mention @lab --link <story URL>`. A mention reaches them through notifications, which a cold DM doesn't.
3. Reply to their reshare or comment, then DM one ask: "We covered your paper: <link>. Make a free account and follow <subject> to get the next one."

Audience mining for paid and organic targeting (`gos-meta-ads`, `gos-cold-outbound`):

```bash
clinstagram --json followers list --user @hubermanlab --limit 200    # who follows the big accounts
clinstagram --json media likers <their-post-url> --limit 200         # who engaged with a paper post
clinstagram --json user tagged @buckinstitute --limit 50             # posts the institute is tagged in
clinstagram --json hashtag recent longevity --limit 50               # fresh posts to engage with
```

## Volume limits

Instagram flags accounts by burst, not by daily totals. clinstagram already spaces writes (2–5 s jitter) and caps follows and likes in `config.toml` (`private_likes_per_hour = 20`, `private_follows_per_day = 20`, `private_dm_per_hour = 30`). Ramp the way the ARN email desk does: hold a volume for 7 clean days (no challenge, no "action blocked") before stepping it up, and drop back one step on the first block. A channel account that gets locked out costs weeks of warm-up; that is the price of pushing too fast.

Route the private API through a residential proxy (`--proxy` or `config set proxy`) once you run more than one account, one proxy per account.

## Measuring

Judge each format on its median post and on sign-ups per 1,000 reach, never on the best post. Tag every link with UTM parameters (`utm_source=instagram&utm_medium=bio|story|dm`) and read sign-ups by source on the site. A format wins only when a second batch agrees.
