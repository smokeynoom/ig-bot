[![Post reel](https://github.com/smokeynoom/ig-bot/actions/workflows/post.yml/badge.svg)](https://github.com/smokeynoom/ig-bot/actions/workflows/post.yml)

# ig-bot

Posts a reel to Instagram on a schedule using GitHub Actions.

- `bot.py` picks the next queue video or writes a new Gemini quote, posts it, and saves progress to `posted_state.json`.
- `.github/workflows/post.yml` runs it 4 times a day and can also be run by hand (Actions tab, Run workflow).

Secrets needed (Settings, Secrets and variables, Actions): `IG_USER_ID`, `IG_ACCESS_TOKEN`, `GEMINI_API_KEY`. Optional: `GEMINI_MODEL`, `GRAPH_VERSION`.
