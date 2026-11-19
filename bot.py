import os, sys, json, random, re, subprocess, time, tempfile
import urllib.request, urllib.parse, urllib.error

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(ROOT, "posted_state.json")
IG_USER = os.environ["IG_USER_ID"]
TOKEN = os.environ["IG_ACCESS_TOKEN"]
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL") or "gemini-3.8-flash"
GV = os.environ.get("GRAPH_VERSION") or "v23.0"
POOL_SHARE = float(os.environ.get("POOL_SHARE") or 0.5)
POOL_COUNT = 100
QUOTA_RESERVE = 5
DRY_RUN = os.environ.get("DRY_RUN") == "1"
CAPTION_TEMPLATE = "#relatable #overthinking #realtalk"
CAPTION_STYLE = ("Romantic, loyal, soft-spoken. Mix of: funny casual 'when...' relatable moments about missing "
                 "someone or waiting on a text; earnest advice about love and loyalty; soft aesthetic longing; "
                 "and a message written directly to one partner. She is the only girl in his world; never imply "
                 "other options or rank her ('the one'). English.")
POOL_LANES = "dffdeddffdeddfedffdfffffffsdfessddeddfdsdfeddsfdfsddfdddfeddffdfeedddddseffeedfeeseddedsdsdeefefdfed"
LANE_TAGS = {
    "f": "#flirty #relationshipgoals #lovequotes #couplegoals",
    "m": "#couplegoals #relationshipgoals #lovehumor #relatable",
    "r": "#couplegoals #lovequotes #relationshipgoals #flirty",
    "t": "#couplegoals #lovehumor #relatable #justsaying",
    "u": "#imissyou #longdistance #lovequotes #couplegoals",
    "w": "#lovequotes #couplegoals #relationshipgoals #soft",
    "y": "#lovequotes #relationshipgoals #couplegoals #softlove",
}
VIDEO_TAGS = {"d": "#lovequotes #relationshipgoals #couplegoals", "f": "#lovehumor #relatable #couplegoals",
              "e": "#lovequotes #loyalty #relationshipgoals", "s": "#softlove #lovequotes #aesthetic"}


def log(*a):
    print(*a, flush=True)


def http(url, data=None, headers=None, method=None, timeout=120):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} {url.split('?')[0]}: {e.read().decode()[:500]}")


def graph(path, params=None, post=False):
    params = dict(params or {})
    params["access_token"] = TOKEN
    url = f"https://graph.facebook.com/{GV}/{path}"
    if post:
        return http(url, data=urllib.parse.urlencode(params).encode(), method="POST")
    return http(url + "?" + urllib.parse.urlencode(params))


def load_state():
    if os.path.exists(STATE_FILE):
        return json.load(open(STATE_FILE))
    return {}


def build_posted(state):
    if state.get("postedItems") is not None:
        return list(state["postedItems"])
    n = int(state.get("poolIndex") or 0)
    return [f"v{i:03d}" for i in range(1, n + 1)]


def all_ids():
    # GitHub bot owns the newer lanes only. The n8n workflow owns f/m/r/v, so the two never overlap.
    return ([f"t{i:02d}" for i in range(1, 31)] + [f"u{i:02d}" for i in range(1, 31)]
            + [f"w{i:02d}" for i in range(1, 31)] + [f"y{i:02d}" for i in range(1, 31)])


def pool_post(item):
    kind, num = item[0], int(item[1:])
    if kind == "f":
        path, lane = f"flirty_videos/flirty_{num:02d}.mp4", "f"
        caption = LANE_TAGS["f"]
    elif kind == "m":
        path, lane = f"manip_videos/manip_{num:02d}.mp4", "m"
        caption = LANE_TAGS["m"]
    elif kind == "r":
        path, lane = f"reply_videos/reply_{num:02d}.mp4", "r"
        caption = LANE_TAGS["r"]
    elif kind == "y":
        path, lane = f"stack_videos/stack_{num:02d}.mp4", "y"
        caption = LANE_TAGS["y"]
    elif kind == "w":
        path, lane = f"bit_videos/bit_{num:02d}.mp4", "w"
        caption = LANE_TAGS["w"]
    elif kind == "u":
        path, lane = f"missing_videos/missing_{num:02d}.mp4", "u"
        caption = LANE_TAGS["u"]
    elif kind == "t":
        path, lane = f"caption_videos/caption_{num:02d}.mp4", "t"
        caption = LANE_TAGS["t"]
    else:
        lo = ((num - 1) // 25) * 25 + 1
        path = f"videos_{lo:03d}-{lo + 24:03d}/video_{num:03d}.mp4"
        lane = POOL_LANES[num - 1] if num <= len(POOL_LANES) else "d"
        caption = VIDEO_TAGS.get(lane, CAPTION_TEMPLATE)
    return os.path.join(ROOT, path), caption


NORM = lambda s: re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", s.lower())).strip()


def too_similar(text, olds):
    a = set(NORM(text).split())
    for old in olds:
        b = set(NORM(old).split())
        if a and b and len(a & b) / max(len(a), len(b)) > 0.7:
            return True
    return False


def gemini_quote(recent):
    prompt = "\n".join([
        "Write ONE original short text-on-screen quote for a romantic Instagram Reel, plus its caption.",
        "",
        "Pick ONE of these styles at random (vary between runs):",
        '  A) "funny casual" - starts with "when" or a relatable texting moment about missing someone, waiting on a reply, being soft for one person. Lowercase, playful.',
        '  B) "earnest advice" - 1 to 2 sincere sentences about loyalty, choosing someone daily, love that feels safe.',
        '  C) "soft aesthetic" - 2 to 4 very short lowercase lines, longing or comfort, a little repetition is ok.',
        '  D) "direct to partner" - spoken straight to one person ("i", "you"), warm and specific, never needy.',
        "",
        "Vibe examples (match the feel, do not copy):",
        '- "i hope the person you\'re missing tonight is missing you right back"',
        '- "when they say call you in 5 and it\'s been 45 minutes but i\'m still sitting by the phone with my full chest"',
        "",
        'Rules for "overlay":',
        "- Style A/C/D: 6 to 30 words. Style B: 12 to 40 words. Emotionally honest, loyal, romantic.",
        "- There is only ONE girl in the speaker's world. Never imply other options, never rank her (\"the one who...\").",
        '- Fully lowercase for A/C/D with little punctuation; Gen-Z phrasing like "the way", "lowkey" is fine but not every time.',
        "- Must be ORIGINAL. Never use song lyrics or quote a famous person, book, film, or existing post.",
        "- No hashtags, no emojis.",
        'Also return "style" as A, B, C or D.',
        'Rules for "caption": one short casual line (no questions, no "tag someone", no engagement bait), then 3-5 relevant hashtags that fit romantic/relationship quote reels. Under 200 characters.',
        "Style/topics: " + CAPTION_STYLE,
        'Openers like "no bc" or "when" are fine to reuse, but the idea and wording must be different from every post below. Never repeat or closely copy any of them:',
        "\n".join("- " + q for q in recent[:60]) or "(none yet)",
    ])
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 1.1, "responseMimeType": "application/json",
                                 "responseSchema": {"type": "OBJECT", "properties": {
                                     "style": {"type": "STRING"}, "overlay": {"type": "STRING"},
                                     "caption": {"type": "STRING"}},
                                     "required": ["style", "overlay", "caption"]}}}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    out = http(url, data=json.dumps(body).encode(),
               headers={"Content-Type": "application/json", "x-goog-api-key": GEMINI_KEY}, timeout=60)
    txt = "".join(p.get("text", "") for p in out["candidates"][0]["content"]["parts"])
    return json.loads(txt)


def wrap(s, mx=24):
    lines, line = [], ""
    for w in s.split(" "):
        if len((line + " " + w).strip()) > mx and line:
            lines.append(line)
            line = w
        else:
            line = (line + " " + w).strip()
    if line:
        lines.append(line)
    return "\n".join(lines)


def render_quote(overlay, out_path):
    words = len(overlay.split())
    duration = min(15, max(7, round(words / 2.6 + 3)))
    m = re.match(r"^((?:hot take|a lesson i learned[^:]*|reminder|unpopular opinion):)\s*(.*)$", overlay, re.I)
    text = m.group(1) + "\n" + wrap(m.group(2)) if m else wrap(overlay)
    tf = os.path.join(tempfile.gettempdir(), "quote.txt")
    open(tf, "w", encoding="utf-8").write(text)
    palettes = [("0x0b0b1a", "0x2a1f3d", "0x101828"), ("0x0d1117", "0x1f2a44", "0x0a0f1f"),
                ("0x140c1c", "0x3a1c32", "0x0c0a14"), ("0x0a1414", "0x16302e", "0x0b0f12"),
                ("0x151012", "0x33202a", "0x0e0b10")]
    c = random.choice(palettes)
    main_font = os.path.join(ROOT, "fonts", "Poppins-Bold.ttf")
    handle_font = os.path.join(ROOT, "fonts", "Poppins-Medium.ttf")
    vf = ",".join([
        f"drawtext=fontfile='{main_font}':textfile='{tf}':expansion=none:text_align=center:fontsize=62"
        f":fontcolor=0xF6E7A1:line_spacing=18:borderw=3:bordercolor=black@0.6:x=(w-text_w)/2:y=(h-text_h)/2"
        f":alpha='min(1,t/0.8)'",
        f"drawtext=fontfile='{handle_font}':text='@sm':fontsize=42:fontcolor=white@0.6:borderw=2"
        f":bordercolor=black@0.4:x=(w-text_w)/2:y=210",
    ])
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-f", "lavfi", "-i", f"gradients=s=1080x1920:r=30:d={duration}:c0={c[0]}:c1={c[1]}:c2={c[2]}:n=3:speed=0.008",
           "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
           "-t", str(duration), "-vf", vf,
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "high", "-preset", "medium", "-crf", "20",
           "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", out_path]
    subprocess.run(cmd, check=True)


def load_music_list():
    p = os.path.join(ROOT, "music.txt")
    if not os.path.exists(p):
        return []
    out = []
    for ln in open(p, encoding="utf-8"):
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            out.append(ln)
    return out


def pick_audio(state):
    """Your music.txt list first (search by name, or a raw audio id), then trending music."""
    if os.environ.get("USE_AUDIO", "1") == "0":
        return None
    used = state.get("usedAudio") or []
    try:
        mine = load_music_list()
        random.shuffle(mine)
        for entry in mine:
            if entry.isdigit():
                log("audio (your list, id):", entry)
                return entry
            try:
                res = graph("ig_audio", {"audio_type": "music", "user_id": IG_USER, "search_query": entry})
            except Exception as e:
                log("search failed for", entry, ":", e)
                continue
            tracks = [t for t in (res.get("audio") or []) if t.get("audio_id")]
            if tracks:
                t = tracks[0]
                log("audio (your list):", t.get("title"), "-", t.get("display_artist"))
                return t["audio_id"]
            log("not available on Instagram:", entry)
        res = graph("ig_audio", {"audio_type": "music", "user_id": IG_USER})
        tracks = res.get("audio") or []
        fresh = [t for t in tracks if t.get("audio_id") and t["audio_id"] not in used]
        pool = (fresh or [t for t in tracks if t.get("audio_id")])[:15]
        if not pool:
            log("no trending audio returned")
            return None
        t = random.choice(pool)
        log("audio (trending):", t.get("title"), "-", t.get("display_artist"))
        return t["audio_id"]
    except Exception as e:
        log("audio lookup failed, posting without audio:", e)
        return None


def post_reel(path, caption, audio_id=None):
    size = os.path.getsize(path)
    params = {"media_type": "REELS", "upload_type": "resumable",
              "caption": caption, "share_to_feed": "true"}
    c = None
    if audio_id:
        try:
            p2 = dict(params)
            p2["audio_configuration"] = json.dumps({"audio_id": audio_id, "audio_volume": 80, "video_volume": 15})
            c = graph(f"{IG_USER}/media", p2, post=True)
            log("container created with audio")
        except Exception as e:
            log("audio rejected, retrying without audio:", e)
            audio_id = None
    if c is None:
        c = graph(f"{IG_USER}/media", params, post=True)
    cid = c["id"]
    log("container", cid, "size", size)
    data = open(path, "rb").read()
    http(f"https://rupload.facebook.com/ig-api-upload/{GV}/{cid}", data=data, method="POST",
         headers={"Authorization": "OAuth " + TOKEN, "offset": "0", "file_size": str(size)}, timeout=600)
    for _ in range(40):
        time.sleep(15)
        s = graph(cid, {"fields": "status_code,status"})
        log("status", s.get("status_code"))
        if s.get("status_code") == "FINISHED":
            break
        if s.get("status_code") in ("ERROR", "EXPIRED"):
            raise RuntimeError("Processing failed: " + json.dumps(s))
    else:
        raise RuntimeError("Processing timed out")
    pub = graph(f"{IG_USER}/media_publish", {"creation_id": cid}, post=True)
    return pub["id"]


def main():
    state = load_state()
    q = graph(f"{IG_USER}/content_publishing_limit", {"fields": "quota_usage,config"})
    d = (q.get("data") or [{}])[0]
    used = d.get("quota_usage", 0)
    total = (d.get("config") or {}).get("quota_total", 100)
    log("quota", used, "/", total)
    if used >= total - QUOTA_RESERVE:
        log("quota too high, skipping")
        return

    posted = build_posted(state)
    queue = [i for i in all_ids() if i not in posted]
    use_pool = bool(queue) and random.random() < POOL_SHARE or (bool(queue) and not GEMINI_KEY)
    recent = state.get("recentQuotes") or []
    item_id = None

    def pick_pool():
        iid = queue[0]
        p, cap = pool_post(iid)
        if not os.path.exists(p):
            raise RuntimeError("Missing video file: " + p)
        return iid, p, cap

    out = None
    overlay = None
    if not use_pool:
        try:
            for attempt in range(4):
                out = gemini_quote(recent)
                overlay = re.sub(r"\s+", " ", str(out.get("overlay", ""))).strip()
                if len(overlay.split()) < 6:
                    continue
                if too_similar(overlay, recent):
                    log("duplicate, retrying:", overlay)
                    continue
                break
            else:
                raise RuntimeError("Could not get a fresh quote")
        except Exception as e:
            log("Gemini failed:", e)
            if not queue:
                raise
            use_pool = True

    if use_pool:
        item_id, path, caption = pick_pool()
        overlay = item_id
        source = "pool"
    else:
        caption = str(out.get("caption", "")).strip()[:2100] or CAPTION_TEMPLATE
        path = os.path.join(tempfile.gettempdir(), "quote.mp4")
        render_quote(overlay, path)
        source = "gemini"

    log("posting", source, overlay)
    if DRY_RUN:
        log("dry run, not posting")
        return
    audio_id = pick_audio(state)
    media_id = post_reel(path, caption, audio_id)
    log("published", media_id)

    if source == "pool":
        posted.append(item_id)
    n = int(state.get("lastPosted") or 0) + 1
    hist = (state.get("history") or [])[-200:]
    hist.append(int(time.time() * 1000))
    state.update({"lastPosted": n, "postedItems": posted, "history": hist,
                  "recentQuotes": ([overlay] + recent)[:1000] if source == "gemini" else recent,
                  "usedAudio": (([audio_id] if audio_id else []) + (state.get("usedAudio") or []))[:60],
                  "lastSource": source, "lastMediaId": media_id, "lastCaption": caption})
    json.dump(state, open(STATE_FILE, "w"), indent=1)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log("ERROR:", e)
        sys.exit(1)
