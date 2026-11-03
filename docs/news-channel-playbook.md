# Running a news channel with clinstagram

How an agent runs an Instagram channel for a niche news site (built around AgingResearch.News) with clinstagram and the god-of-sales skills (`gos-news-media`, `gos-shortform-organic`, `gos-cold-outbound`). Every step below is a CLI call, so a scheduler or an agent can run the loop unattended.

## Setup, once

```bash
clinstagram auth login -u <channel_account>
clinstagram config mode private-enabled        # writes (posting, DMs, replies) need it
clinstagram --json profile edit --username agingresearch.news --name "AgingResearch.News" \
  --bio "🧬 Aging research, the minute it lands
📚 Every journal, every language
💬 In plain words
👇 Follow any topic, free"
clinstagram --json profile picture app-icon-1080.jpg   # the site's mark: white A, teal bar, #1f1f1f
clinstagram --json profile convert --to creator         # unlocks insights
clinstagram --json profile show                # confirm
```

Do the identity changes (username, name, picture, bio, links) by hand in the Instagram app, or spread them over several days. Through the private API, all at once, they look like an account takeover. On 2 Nov 2026 that pattern (a dormant account renamed, rebranded and probed for bio links within an hour, after a 429) drew a link-sharing restriction for "prohibited commercial practices". It was lifted on review the next day.

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

## How to write for Instagram

People on Instagram scroll; they don't read. A post has about two seconds to earn the tap. These rules come from how the accounts that already own this audience post (studied live on 2 Nov 2026: ScienceAlert 903K, New Scientist 1.57M, IFLScience 703K, Science 593K, lifespan.io 18K, Longevity.Technology 39K, FoundMyFitness 1.2M, Huberman 8.1M, Attia 1.57M, Sinclair 903K).

**The caption shape**

1. **Hook line.** One emoji, then the finding as a claim with a number, or a question. Name the institution when it carries weight ("Harvard scientists made old, blind mice see again").
2. **Who and how.** Name the lead scientists and their institution, and one or two concrete details of the method (the genes, the dose, how long). Vague captions read like content farms; names, places and specifics read like news.
3. **What happened**, with the paper's own numbers.
4. **The catch**: mice or people, how many, observational or trial, preprint or peer reviewed.
5. **One ask**: "Comment WORD and we'll send you the paper", or "Follow <topic> free, link in bio". Never both.
6. **Citation line**: "📄 Lead, Senior et al. Journal, year. doi.org/…". Names and the DOI come from PubMed, never from memory.
7. **Five hashtags at most** (Instagram's hard cap since 18 Dec 2025): one broad (#longevity 3.1M), one or two mid (#healthspan 256K, #sciencenews 246K), one or two specific (#taurine 73K, #longevityscience 67K, #agingresearch 9K).

**Emojis: one on the hook, one on the citation line, none elsewhere.** `virality compare` ranked four densities of the same caption: hook-only first, none second, one per block third ("feels more promotional"), heavy last ("a sensational tone, weakening credibility").

**Every carousel is six swipes, 4:5:** hook cover (cinematic photo, the hook in Host Grotesk) → what they did → the explainer card (ARN's X card method, house style, one palette per post) → the catch → why it matters → follow/comment ask. The text slides take the card's palette so the carousel reads as one piece. Every number on every slide is checked against the paper's text before it ships.

**Worked example** (the site's top story on 2 Nov 2026):

```
👁️ A photo of your eye may show how fast you're aging.

🔬 An AI read retinal photos and estimated each person's lifespan path. Its estimates matched who actually died, and when.

👥 Trained on a European group, then tested on three more. An observational cohort, so it shows risk, not cause.

⚠️ It's a research model, not a test your optician can run yet.

💬 Comment EYE and we'll DM you the paper in plain words.

#longevity #aging #agingresearch
```

**Comment-to-DM: the growth loop.** Huberman's "Comment SUBLIME & I'll DM you a link" is the strongest move on the platform: every comment lifts the post in the feed, and every DM opens a private line to a reader. clinstagram runs it end to end:

```bash
clinstagram --json comments list <post> --limit 100          # find comments containing the keyword
clinstagram --json dm send @<commenter> "Here's the paper in plain words 👇 https://agingresearch.news/s/...?utm_source=instagram&utm_medium=dm — tap Follow on the page to get the next one free."
clinstagram --json comments reply <comment_ref> "Sent! 📩"
```

Answer each one within the hour, and keep to the DM caps in `config.toml` (`private_dm_per_hour = 30`). Instagram watches DM bursts more closely than anything else.

**Formats, in order of reach**

| Format | Use it for | Shape |
|---|---|---|
| Reel, 15–40 s | The day's most surprising finding | Hook stat on screen in the first second, the figure from the paper, the catch, the ask |
| Carousel, 4–7 slides | The lead story | 1 hook · 2 finding · 3 who was studied · 4 the catch · 5 what it means · 6 the ask |
| Weekly roundup carousel | Fridays: "This week in aging 🧬" | One slide per story, an emoji tag on each, as ScienceAlert does (💊 drugs, 🧠 brain, 🫀 heart, 🐭 mice) |
| Story with link sticker | Every post, the same day | The hook plus the sticker to the story page |

Keep to the house style on every slide: white sans text on #1f1f1f with the teal bar as the accent, as on agingresearch.news.

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
