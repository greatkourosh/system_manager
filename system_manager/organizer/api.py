"""Media Organizer module — serves scan data written by the host-side toolbelt.

The routes here are the organizer's own. They used to be mounted by loading a
sibling checkout's app.py in-process and rewriting the absolute paths in its
HTML, because a second Flask app on the same interpreter has to be reached
through a proxy. Now that the routes are a blueprint, the paths in these
templates are simply right.
"""
import csv
import hashlib
import io
import json
import os
import re
import shlex
from collections import Counter
from datetime import date

from flask import Blueprint, Response, jsonify, render_template, request

from . import subtitle_queue

# Where the pre-generated JSON lives. Four levels up from this file lands on the
# directory that holds this repo, so the sibling checkout resolves in both
# layouts: on the host that is projects/folder_organizer, and in the container it
# is the /folder_organizer mount at the same depth. The host-side scripts are
# what write this data; the routes only read it, except for the tag and
# folder-fix proposals written back for the host to apply.
DATA = os.environ.get(
    "ORGANIZER_DATA_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))), "folder_organizer", "data"),
)

# Genres are scraped out of folder names, so the same genre arrives in several
# spellings: "Sci-fi" and "Sci-Fi" for one entry, and compound runs that were
# never split ("Sci-fiAdventure", "Drama Romance"). Splitting on a case change
# first recovers the boundaries, then on spaces, then folding case, so the
# dropdown lists one item per genre and filtering matches every spelling.
# A space only splits where both sides are known genres -- "Sci-fi" and
# "Black Comedy" are single genres and must survive intact.
_GENRE_SEP = re.compile(r"(?<=[a-z])(?=[A-Z])|[,/]|\s{2,}")

# Multi-word genres whose space is part of the name, not a separator.
_GENRE_PHRASES = {
    "sci-fi": "sci-fi", "sci-fi fantasy": "sci-fi fantasy",
    "black comedy": "black comedy", "romantic comedy": "romantic comedy",
    "science fiction": "science fiction", "film noir": "film noir",
}


def genre_tokens(genres):
    """A card's genre list, deduplicated and case-folded."""
    out = []
    for raw in (genres or "").split(","):
        for part in _GENRE_SEP.split(raw):
            words = [w for w in re.split(r"\s+", part.strip().lower()) if w]
            if not words:
                continue
            # Re-join words that are one multi-word genre, splitting the rest.
            i = 0
            while i < len(words):
                g = words[i]
                if i + 1 < len(words):
                    pair = _GENRE_PHRASES.get(g + " " + words[i + 1])
                    if pair:
                        g, i = pair, i + 1
                g = re.sub(r"^sci-?fi$", "sci-fi", g)
                if g not in out:
                    out.append(g)
                i += 1
    return out


def genre_label(token):
    return token.replace("sci-fi", "Sci-Fi").title()


# OpenSubtitles free tier, from subtitle_queue so there is one source of truth.
SUB_DAILY_QUOTA = subtitle_queue.DAILY_QUOTA

bp = Blueprint("organizer_api", __name__)


@bp.app_context_processor
def inject_theme():
    return {"theme": request.cookies.get("theme", "light")}


def load(name, default=None):
    p = os.path.join(DATA, name)
    if not os.path.exists(p):
        return default if default is not None else None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def gb(b):
    return f"{b / 1024**3:.1f} GB" if b else "0"


def fmt_size(b):
    if not b:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {unit}" if unit != "B" else f"{b} B"
        b /= 1024
    return f"{b:.1f} PB"


bp.add_app_template_filter(gb, "gb")
bp.add_app_template_filter(fmt_size, "fmt_size")


@bp.app_context_processor
def inject_today():
    return {"today": date.today().isoformat()}


@bp.route("/health")
def health():
    return jsonify({"status": "ok"})


@bp.route("/")
def index():
    summary = load("scan_summary.json") or {"totals": {"files": 0, "bytes": 0}, "per_drive": {}, "per_category": {}, "duplicate_summary": {}}
    cats = sorted(summary.get("per_category", {}).items(), key=lambda kv: -kv[1]["bytes"])
    return render_template("organizer/index.html", s=summary, cats=cats)


@bp.route("/scan")
def scan_page():
    summary = load("scan_summary.json")
    dups = load("duplicates.json") or {"groups": []}
    query = request.args.get("q", "").strip().lower()
    groups = dups.get("groups", [])
    if query:
        groups = [g for g in groups if any(query in p.lower() for p in g["copies"])]
    return render_template("organizer/scan.html", s=summary, dups={"groups": groups[:200], "total": dups.get("total_groups", 0), "redundant": dups.get("total_redundant_bytes", 0)}, q=query)


@bp.route("/music")
def music_page():
    catalog = load("music_catalog.json") or []
    query = request.args.get("q", "").strip().lower()
    if query:
        catalog = [f for f in catalog if query in f["path"].lower()]
    by_artist = {}
    for f in catalog:
        parts = f["path"].replace("\\", "/").split("/")
        try:
            i = next(i for i, p in enumerate(parts) if p.lower() == "music")
            artist = parts[i + 1] if i + 1 < len(parts) - 1 else "(root)"
        except StopIteration:
            artist = "(unknown)"
        by_artist.setdefault(artist, []).append(f)
    artists = sorted(by_artist.items(), key=lambda kv: -len(kv[1]))
    return render_template("organizer/music.html", artists=artists[:100], total=len(catalog), q=query)


VIDEO_PAGE_SIZE = 36

# year/rating/pop are scraped from folder names and stored as strings, and are
# '' or None on the cards that had no such token. Every comparison has to
# coerce, and blanks have to be placed explicitly or they default to 0 and
# outrank real values on descending sorts.
def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


REC_RATING = 8.0
REC_POP = 85

# TMDB's own score where we have one, else the folder-name scrape. TMDB is a
# provider number with a vote count behind it; the scrape is a rounded guess
# that only exists for titles whose folder name carried the token at all, so
# all 124 serials would otherwise have no rating at all.
def load_ratings():
    """card id -> rating entry, written host-side by tmdb_ratings.py."""
    cards = (load("ratings_state.json") or {}).get("cards")
    # Generated sidecar, so a stale or foreign file can put a list where the
    # id-keyed map belongs; fall back to the folder scrape rather than 500.
    return cards if isinstance(cards, dict) else {}


def load_seasons_state():
    """card id -> season entry, written host-side by tmdb_seasons.py."""
    cards = (load("seasons_state.json") or {}).get("cards")
    return cards if isinstance(cards, dict) else {}


def card_rating(card, ratings):
    """The score to show and rank on: TMDB's when present, the scrape otherwise.

    Returns (score, votes, exact) where votes is None on the fallback, which is
    what makes a low-confidence score visible rather than implied. A TMDB entry
    with nobody voting is not a rating — the library has game-asset folders that
    match some show — so it falls back rather than showing "⭐ 0.0".

    `exact` is False when TMDB matched a different string: usually the same
    work under another spelling (TÁR, Shōgun) but sometimes a different work
    entirely (the Persian-titled "سارا" matches Terminator: The Sarah Connor
    Chronicles). Nothing cheap separates those two, so the card says so.
    """
    entry = ratings.get(card.get("id"))
    if entry and entry.get("status") == "ok" and _num(entry.get("rating")) is not None \
            and (entry.get("votes") or 0) > 0:
        return _num(entry["rating"]), entry.get("votes"), bool(entry.get("exact"))
    return _num(card.get("rating")), None, False


def is_recommended(card):
    """Both inputs come from a folder-name regex, not a provider, so this is a
    rough shortlist — the UI labels it as such rather than calling it a score."""
    r, p = _num(card.get("rating")), _num(card.get("pop"))
    return r is not None and p is not None and r >= REC_RATING and p >= REC_POP


def _decade(card):
    y = _num(card.get("year"))
    return int(y) // 10 * 10 if y is not None else None


def season_badge(card):
    """Short on-disk season summary for a serial card, or None.

    Reports what the drive holds, which the folder name gets wrong for 43 of
    124 serials. "S1-3" when the seasons run contiguously, "S1, S3" when they
    don't (Family Guy jumps 6 to 15), and the shortfall when a name-claimed
    season is missing.
    """
    state = card.get("season_state")
    if not state or card.get("root") != "serials":
        return None
    present = state.get("seasons_present") or []
    if not present:
        return None

    runs, start, prev = [], present[0], present[0]
    for s in present[1:]:
        if s == prev + 1:
            prev = s
            continue
        runs.append((start, prev))
        start = prev = s
    runs.append((start, prev))
    label = ", ".join(f"S{a}" if a == b else f"S{a}-{b}" for a, b in runs)

    missing = state.get("seasons_missing") or []
    if missing:
        label += f" · missing {', '.join('S%d' % s for s in missing)}"
    return label


def season_badge_class(card):
    state = (card.get("season_state") or {}).get("state")
    return {"incomplete": "bg-warning text-dark", "complete": "bg-success"}.get(state, "bg-secondary")


def season_counts(card):
    """(on disk, claimed by the folder name, total the series ever had).

    The three disagree, which is the point. The name is a guess ("3 Body
    Problem S01 of 03+" claims 3 for a show TMDB counts as 1), the disk is what
    we own, and the TMDB total is the target -- but for a show still running it
    is a ceiling, not a target, so it is shown as such. Any element is None
    when unknown.
    """
    if card.get("kind") != "serial":
        return None

    on_disk = None
    present = (card.get("season_state") or {}).get("seasons_present")
    if present:
        # A count, not the last number: gaps would otherwise read as more
        # seasons than the drive holds (Family Guy has S6 and S15, not 15).
        on_disk = len(present)

    claimed = None
    try:
        claimed = int(card.get("seasons"))
    except (TypeError, ValueError):
        claimed = None

    total = None
    returning = False
    entry = (load_seasons_state().get(card.get("id")) or {})
    if entry.get("status") == "ok":
        total = entry.get("total_seasons")
        returning = bool(entry.get("returning"))

    if on_disk is None and claimed is None and total is None:
        return None
    return {"on_disk": on_disk, "claimed": claimed, "total": total, "returning": returning}


SORTS = {
    "": (None, False),
    "year": ("year", True),
    "rating": ("rating", True),
    "title": ("title", False),
    "size": ("total_bytes", True),
    "episodes": ("video_count", True),
}

# Blanks sort last in both directions. They are partitioned out rather than
# carried in the key tuple, because reverse=True would invert the blank flag
# along with the value and float unknowns back to the top.
def _sort_value(c, field, ratings=None):
    if field == "rating":
        return card_rating(c, ratings or {})[0]
    if field == "year":
        return _num(c.get("year"))
    v = c.get(field)
    if field == "title":
        return str(v or "").lower()
    return v


def _card_sort_key(c):
    return (str(c.get("title") or "").lower(), c.get("id") or "")


def _sort_cards(cards, sort, ratings=None):
    field, desc = SORTS.get(sort, (None, False))
    if field is None:
        return cards
    ratings = ratings or {}
    known = [c for c in cards if _sort_value(c, field, ratings) is not None]
    blanks = [c for c in cards if _sort_value(c, field, ratings) is None]
    known.sort(key=lambda c: (_sort_value(c, field, ratings), _card_sort_key(c)), reverse=desc)
    return known + blanks


def poster_path(card_id):
    return os.path.join(DATA, "posters_b64",
                        hashlib.md5(card_id.encode("utf-8")).hexdigest() + ".b64")


@bp.route("/videos")
def videos_page():
    lib = load("video_library.json")
    if not lib:
        catalog = load("video_catalog.json") or []
        query = request.args.get("q", "").strip().lower()
        if query:
            catalog = [f for f in catalog if query in f["path"].lower()]
        return render_template("organizer/videos.html", videos=catalog[:300], total=len(catalog), q=query)

    query = request.args.get("q", "").strip().lower()
    root = request.args.get("root", "all")
    sub = request.args.get("sub", "all")   # all | missing-fa | missing-en | missing-both
    genre = request.args.get("genre", "")
    decade = request.args.get("decade", "")
    rating = request.args.get("rating", "")
    kind = request.args.get("kind", "all")
    min_gb = request.args.get("min_gb", "")
    sort = request.args.get("sort", "")
    page = max(0, int(request.args.get("page", 0)))

    # poster on cards is a flag video_catalog.py carries over from the previous
    # library, so one rebuild that runs after a poster fill wipes it for good.
    # The .b64 file is the durable artefact, so ask the disk instead. Copied,
    # not mutated in place, so the loaded library stays as it was read.
    cards = [dict(c, poster=os.path.exists(poster_path(c.get("poster_id") or c["id"])))
             for c in lib["cards"]]
    ratings = load_ratings()
    all_genres = sorted({g for c in cards for g in genre_tokens(c.get("genres"))})
    decades = sorted({d for d in (_decade(c) for c in cards) if d is not None}, reverse=True)
    if root != "all":
        cards = [c for c in cards if c["root"] == root]
    if query:
        cards = [c for c in cards if query in c["folder"].lower()]
    if genre:
        cards = [c for c in cards if genre in genre_tokens(c.get("genres"))]
    if sub == "missing-fa":
        cards = [c for c in cards if not c["has_fa_sub"]]
    elif sub == "missing-en":
        cards = [c for c in cards if not c["has_en_sub"]]
    elif sub == "missing-both":
        cards = [c for c in cards if not c["has_fa_sub"] and not c["has_en_sub"]]
    if decade:
        if decade == "unknown":
            cards = [c for c in cards if _decade(c) is None]
        else:
            want = int(decade)
            cards = [c for c in cards if _decade(c) == want]
    if rating:
        floor = _num(rating)
        cards = [c for c in cards if (r := card_rating(c, ratings)[0]) is not None and r >= floor]
    if kind != "all":
        cards = [c for c in cards if c.get("kind") == kind]
    if min_gb:
        cards = [c for c in cards if (c.get("total_bytes") or 0) >= float(min_gb) * 1024 ** 3]

    # Sort before slicing, or paging walks the unsorted list and shows dupes.
    cards = _sort_cards(cards, sort, ratings)

    pages = max(1, (len(cards) + VIDEO_PAGE_SIZE - 1) // VIDEO_PAGE_SIZE)
    page = min(page, pages - 1)
    return render_template("organizer/videos.html", cards=cards[page * VIDEO_PAGE_SIZE:(page + 1) * VIDEO_PAGE_SIZE],
                           total=len(lib["cards"]), shown=len(cards), q=request.args.get("q", ""),
                           root=root, sub=sub, genre=genre, all_genres=all_genres,
                           decade=decade, decades=decades, rating=rating, kind=kind,
                           min_gb=min_gb, sort=sort,
                           rec_rating=REC_RATING, rec_pop=REC_POP,
                           genre_label=genre_label,
                           is_recommended=is_recommended,
                           card_rating=lambda c: card_rating(c, ratings),
                           season_badge=season_badge,
                           season_badge_class=season_badge_class,
                           season_counts=season_counts,
                           sub_quota=SUB_DAILY_QUOTA,
                           page=page, pages=pages, today=lib.get("date"))


@bp.route("/api/subtitles/queue", methods=["GET", "POST"])
def api_subtitle_queue():
    """Read or add to the host-side subtitle fetch queue.

    The container has no media mount and no OpenSubtitles credentials, so it
    cannot fetch anything itself; it only records what the user asked for and
    subtitle_runner.py does the work on the host.
    """
    if request.method == "GET":
        q = subtitle_queue.load_queue()
        return jsonify({"pending": len(q["requests"]), "quota": subtitle_queue.DAILY_QUOTA})
    body = request.get_json(silent=True) or {}
    card_ids = body.get("card_ids") or []
    langs = body.get("langs") or []
    if not card_ids or not langs:
        return jsonify({"ok": False, "error": "card_ids and langs are required"}), 400
    try:
        pending = subtitle_queue.enqueue(card_ids, langs)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "pending": pending, "quota": subtitle_queue.DAILY_QUOTA})


@bp.route("/api/subtitles/clear", methods=["POST"])
def api_subtitle_clear():
    q = subtitle_queue.load_queue()
    subtitle_queue.save_queue({"date": q["date"], "requests": []})
    return jsonify({"ok": True, "pending": 0})


@bp.route("/api/video/poster")
def api_video_poster():
    """Lazy poster image: reads from data/posters_b64/<md5(id)>.b64 written by tmdb_client.py."""
    card_id = request.args.get("id", "")
    if not card_id:
        return "", 404
    p = poster_path(card_id)
    if not os.path.exists(p):
        return "", 404
    try:
        with open(p, encoding="utf-8") as f:
            b64 = f.read().strip()
        resp = jsonify({"ok": True, "b64": b64})
        resp.headers["Cache-Control"] = "public, max-age=86400"
        return resp
    except OSError:
        return "", 404


FFP_FILE = os.path.join(DATA, "folder_fix_proposals.json")


def load_ffp():
    if not os.path.exists(FFP_FILE):
        return {"proposals": [], "count": 0}
    with open(FFP_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_ffp(ffp):
    tmp = FFP_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(ffp, f, ensure_ascii=False, indent=1)
    os.replace(tmp, FFP_FILE)


@bp.route("/folders")
def folders_page():
    ffp = load_ffp()
    area = request.args.get("area", "music")
    status = request.args.get("status", "pending")
    query = request.args.get("q", "").strip().lower()
    props = ffp["proposals"]
    areas = sorted({p["area"] for p in props})
    counts = {a: sum(1 for p in props if p["area"] == a and p.get("status", "pending") == "pending") for a in areas}
    if area != "all":
        props = [p for p in props if p["area"] == area]
    if status != "all":
        props = [p for p in props if p.get("status", "pending") == status]
    if query:
        props = [p for p in props if query in p["current"].lower() or query in p["proposed"].lower()]
    accepted = sum(1 for p in ffp["proposals"] if p.get("status") == "accepted")
    return render_template("organizer/folders.html", props=props[:400], ffp=ffp, area=area,
                           status=status, q=query, areas=areas, counts=counts,
                           accepted=accepted)


@bp.route("/api/folderfix/accept", methods=["POST"])
def api_ff_accept():
    body = request.get_json(force=True)
    pid = body.get("id")
    ffp = load_ffp()
    for p in ffp["proposals"]:
        if p["id"] == pid:
            p["status"] = "accepted" if body.get("accepted") else "pending"
            save_ffp(ffp)
            return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "id not found"}), 404


@bp.route("/api/folderfix/edit", methods=["POST"])
def api_ff_edit():
    body = request.get_json(force=True)
    pid = body.get("id")
    new_name = (body.get("proposed") or "").strip()
    if not new_name or any(ch in new_name for ch in '\\/:*?"<>|'):
        return jsonify({"ok": False, "error": "invalid folder name"}), 400
    ffp = load_ffp()
    for p in ffp["proposals"]:
        if p["id"] == pid:
            p["proposed"] = new_name
            save_ffp(ffp)
            return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "id not found"}), 404


@bp.route("/api/folderfix/export", methods=["POST"])
def api_ff_export():
    body = request.get_json(silent=True) or {}
    area = body.get("area")
    ffp = load_ffp()
    acc = [p for p in ffp["proposals"] if p.get("status") == "accepted"
           and (not area or p["area"] == area)]
    os.makedirs(CMD_DIR, exist_ok=True)
    payload = {"generated": date.today().isoformat(), "count": len(acc),
               "entries": [{"id": p["id"], "area": p["area"], "dir": p["dir"],
                            "current": p["current"], "proposed": p["proposed"]} for p in acc]}
    out = os.path.join(CMD_DIR, "folder_fix_list.json")
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    os.replace(tmp, out)
    return jsonify({"ok": True, "count": len(acc), "file": "commands_to_run/folder_fix_list.json",
                    "dry_command": run_cmd("apply_folder_fix.py"),
                    "apply_command": run_cmd("apply_folder_fix.py", "--apply")})


@bp.route("/programs")
def programs_page():
    catalog = load("programs_catalog.json") or []
    query = request.args.get("q", "").strip().lower()
    ext = request.args.get("ext", "").strip().lower()
    extensions = sorted({f["ext"].lower() for f in catalog if f["ext"]})
    if query:
        catalog = [f for f in catalog if query in f["path"].lower()]
    if ext:
        catalog = [f for f in catalog if f["ext"].lower() == ext]
    total = len(catalog)
    page_size = 300
    pages = max(1, (total + page_size - 1) // page_size)
    page = min(max(0, request.args.get("page", 0, type=int)), pages - 1)
    start = page * page_size
    return render_template(
        "organizer/programs.html", programs=catalog[start:start + page_size], total=total,
        q=query, ext=ext, extensions=extensions, page=page, pages=pages,
        first=start + 1 if total else 0, last=min(start + page_size, total),
    )


@bp.route("/programs/export.csv")
def programs_export_csv():
    catalog = load("programs_catalog.json") or []
    query = request.args.get("q", "").strip().lower()
    ext = request.args.get("ext", "").strip().lower()
    if query:
        catalog = [f for f in catalog if query in f["path"].lower()]
    if ext:
        catalog = [f for f in catalog if f["ext"].lower() == ext]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["path", "ext", "size_bytes"])
    for f in catalog:
        writer.writerow([f["path"], f["ext"], f["size_bytes"]])
    stamp = date.today().isoformat()
    return Response(
        buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="programs_{stamp}.csv"'},
    )


@bp.route("/cleanup")
def cleanup_page():
    prop = load("cleanup_proposal.json") or {"groups": [], "total_groups": 0}
    risk = request.args.get("risk", "safe")
    query = request.args.get("q", "").strip().lower()
    groups = [g for g in prop.get("groups", []) if risk == "all" or g["risk"] == risk]
    if query:
        groups = [g for g in groups if query in g["keep"].lower() or any(query in d.lower() for d in g["delete"])]
    return render_template("organizer/cleanup.html", p=prop, groups=groups[:200], risk=risk, q=query, shown=len(groups))


@bp.route("/music/tags")
def music_tags_page():
    audit = load("music_tag_audit.json")
    if not audit:
        return render_template("organizer/music_tags.html", a=None, problems=[], folders=[], q="",
                               state="all", folder="", missing="all", page=0, pages=1,
                               plan_stats=None, mb_available=False, shown=0)
    plan = load_tag_plan()
    query = request.args.get("q", "").strip().lower()
    state = request.args.get("state", "all")
    folder = request.args.get("folder", "")
    missing = request.args.get("missing", "all")
    page = max(0, int(request.args.get("page", 0)))
    problems = _music_problems_filtered(audit, plan, state, folder, missing, query)
    pages = max(1, (len(problems) + TAG_PAGE_SIZE - 1) // TAG_PAGE_SIZE)
    page = min(page, pages - 1)
    entries = plan["entries"]
    page_problems = []
    for p in problems[page * TAG_PAGE_SIZE:(page + 1) * TAG_PAGE_SIZE]:
        q = dict(p)
        e = entries.get(p["path"])
        q["plan_entry"] = e
        page_problems.append(q)
    folders = sorted(audit.get("per_folder", {}).items(), key=lambda kv: -kv[1]["no_tags"])[:60]
    src = Counter()
    accepted_n = 0
    for e in entries.values():
        if e.get("accepted"):
            accepted_n += 1
        for spec in e.get("fields", {}).values():
            src[spec.get("source", "?")] += 1
    plan_stats = {"entries": len(entries), "accepted": accepted_n, "fields_by_source": dict(src)}
    return render_template("organizer/music_tags.html", a=audit, problems=page_problems, folders=folders,
                           q=request.args.get("q", ""), state=state, folder=folder,
                           missing=missing, page=page, pages=pages, shown=len(problems),
                           plan_stats=plan_stats,
                           mb_available=os.path.exists(MB_CAND_FILE))


@bp.route("/api/cleanup")
def api_cleanup():
    return jsonify(load("cleanup_proposal.json") or {})


@bp.route("/api/music/tags")
def api_music_tags():
    return jsonify(load("music_tag_audit.json") or {})


SELECTION_FILE = os.path.join(DATA, "selection_state.json")
SKIP_RULES_FILE = os.path.join(DATA, "skip_rules.json")
# Where the tag/folder-fix lists are written. Writable, unlike DATA: these are
# the proposals the user reviews here and then applies host-side.
CMD_DIR = os.environ.get("ORGANIZER_CMD_DIR",
                         os.path.join(os.path.dirname(DATA), "commands_to_run"))
TAG_PLAN_FILE = os.path.join(DATA, "tag_plan.json")
TAG_FIX_LIST = os.path.join(CMD_DIR, "tag_fix_list.json")
MB_CAND_FILE = os.path.join(DATA, "mb_candidates.json")
TAG_PAGE_SIZE = 50

# The dry/apply commands are pasted into a shell on the *host*, not run in this
# container. This app is bind-mounted from the sibling checkout, so BASE here
# (/folder_organizer) is not a path that exists on the host and must not be used
# to build them. Override only if the checkout lives somewhere else.
HOST_ORG_ROOT = os.environ.get("HOST_ORG_ROOT",
                               "/media/kourosh/DEVNVME/projects/folder_organizer")


def run_cmd(script, *args):
    """A command for the user to paste into a host shell, quoted for safety."""
    parts = ["python3", shlex.quote(os.path.join(HOST_ORG_ROOT, script))]
    return " ".join(parts + list(args))


def load_tag_plan():
    if not os.path.exists(TAG_PLAN_FILE):
        return {"version": 1, "updated": None, "entries": {}}
    try:
        with open(TAG_PLAN_FILE, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        import time
        time.sleep(0.05)
        try:
            with open(TAG_PLAN_FILE, encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            return {"version": 1, "updated": None, "entries": {}}


def save_tag_plan(plan):
    os.makedirs(DATA, exist_ok=True)
    plan["updated"] = date.today().isoformat()
    tmp = TAG_PLAN_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=1)
    os.replace(tmp, TAG_PLAN_FILE)  # atomic on same volume


def load_skip_rules():
    if not os.path.exists(SKIP_RULES_FILE):
        return []
    with open(SKIP_RULES_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_skip_rules(rules):
    os.makedirs(DATA, exist_ok=True)
    tmp = SKIP_RULES_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rules, f, ensure_ascii=False, indent=1)
    os.replace(tmp, SKIP_RULES_FILE)  # atomic on same volume


def is_skipped(path, ext, rules):
    """True if path is covered by a skip rule. Rules: [{folder, ext}] ext None = all types."""
    pl = path.lower()
    for r in rules:
        folder = r["folder"].lower()
        if pl.startswith(folder + os.sep) or pl == folder or pl.startswith(folder + "\\") or pl.startswith(folder + "/"):
            if r.get("ext") is None or r["ext"].lower() == ext.lower():
                return True
    return False


def _groups_with_gid():
    prop = load("cleanup_proposal.json") or {"groups": []}
    groups = prop.get("groups", [])
    seen = {}
    for g in groups:
        # stable id: content hash + category (no positional index — indexes shift when groups are removed)
        gid = f"{g['sha256']}-{g['category']}"
        n = seen.get(gid, 0)
        seen[gid] = n + 1
        if n:
            gid = f"{gid}-{n}"
        g["gid"] = gid
        g["ext"] = os.path.splitext(g["keep"])[1].lower()
    return groups


def load_selection():
    if not os.path.exists(SELECTION_FILE):
        return {"marks": {}, "decided": {}}
    try:
        with open(SELECTION_FILE, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        # torn write from an older non-atomic save; retry once after the writer's os.replace lands
        import time
        time.sleep(0.05)
        try:
            with open(SELECTION_FILE, encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            return {"marks": {}, "decided": {}}


def save_selection(sel):
    os.makedirs(DATA, exist_ok=True)
    tmp = SELECTION_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(sel, f, ensure_ascii=False, indent=1)
    os.replace(tmp, SELECTION_FILE)  # atomic on same volume; readers never see a partial file


def selection_totals(groups, sel):
    files = 0
    total_bytes = 0
    for g in groups:
        marked = [p for p in sel["marks"].get(g["gid"], []) if p in [g["keep"]] + g["delete"]]
        files += len(marked)
        total_bytes += len(marked) * g["size_bytes"]
    return files, total_bytes


PAGE_SIZE = 40


@bp.route("/select")
def select_page():
    groups = _groups_with_gid()
    sel = load_selection()
    rules = load_skip_rules()
    risk = request.args.get("risk", "all")
    state = request.args.get("state", "all")
    category = request.args.get("cat", "all")
    ext = request.args.get("ext", "all").lower()
    skipf = request.args.get("skip", "all")  # all | noskip | skipped
    query = request.args.get("q", "").strip().lower()
    page = max(0, int(request.args.get("page", 0)))

    # facet counts computed before narrowing, so the dropdowns always show what's available
    cat_counts = Counter(g["category"] for g in groups)
    ext_counts = Counter(g["ext"] for g in groups if g["ext"])

    def effective_deletes(g):
        return [d for d in g["delete"] if not is_skipped(d, g["ext"], rules)]

    view = groups
    if risk != "all":
        view = [g for g in view if g["risk"] == risk]
    if category != "all":
        view = [g for g in view if g["category"] == category]
    if ext != "all":
        view = [g for g in view if g["ext"] == ext]
    if skipf == "noskip":
        view = [g for g in view if not any(is_skipped(p, g["ext"], rules) for p in [g["keep"]] + g["delete"])]
    elif skipf == "skipped":
        view = [g for g in view if any(is_skipped(p, g["ext"], rules) for p in [g["keep"]] + g["delete"])]
    if query:
        view = [g for g in view if query in g["keep"].lower() or any(query in d.lower() for d in g["delete"])]
    if state == "decided":
        view = [g for g in view if sel["decided"].get(g["gid"])]
    elif state == "undecided":
        view = [g for g in view if not sel["decided"].get(g["gid"])]

    # totals for the current filtered view (after skip rules)
    view_files = sum(len(sel["marks"].get(g["gid"], effective_deletes(g))) for g in view)
    view_bytes = sum(len(sel["marks"].get(g["gid"], effective_deletes(g))) * g["size_bytes"] for g in view)

    pages = max(1, (len(view) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, pages - 1)
    page_groups = view[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]

    rendered = []
    for g in page_groups:
        default_marks = sel["marks"].get(g["gid"])
        if default_marks is not None:
            marked_set = set(default_marks)
        else:
            marked_set = set(effective_deletes(g))
        eff_del = effective_deletes(g)
        keep_skipped = is_skipped(g["keep"], g["ext"], rules)
        copies = [{"path": g["keep"], "suggested_delete": False, "marked": g["keep"] in marked_set, "skipped": keep_skipped}]
        copies += [{"path": d, "suggested_delete": True, "marked": d in marked_set,
                    "skipped": is_skipped(d, g["ext"], rules)} for d in g["delete"]]
        rendered.append({**g, "copies": copies,
                         "selected_delete": [c for c in copies if c["marked"] and not c["skipped"]],
                         "effective_delete": eff_del,
                         "fully_skipped": len(g["delete"]) > 0 and len(eff_del) == 0,
                         "has_skipped": any(c["skipped"] for c in copies),
                         "decided": bool(sel["decided"].get(g["gid"]))})

    sel_files, sel_bytes = selection_totals(groups, {"marks": {gg["gid"]: sel["marks"].get(gg["gid"], [d for d in gg["delete"] if not is_skipped(d, gg["ext"], rules)]) for gg in groups}, "decided": sel["decided"]})
    return render_template("organizer/select.html", groups=rendered, page=page, pages=pages,
                           total_groups=len(view), sel_total_groups=len(groups),
                           sel_files=sel_files, sel_bytes=sel_bytes,
                           view_files=view_files, view_bytes=view_bytes,
                           risk=risk, state=state, cat=category, ext=ext, skipf=skipf,
                           cat_counts=cat_counts.most_common(),
                           ext_counts=ext_counts.most_common(40),
                           skip_rules=rules,
                           q=request.args.get("q", ""))


@bp.route("/api/selection/set-group", methods=["POST"])
def api_sel_set_group():
    body = request.get_json(force=True)
    gid = body["gid"]
    marks = [m["path"] for m in body["marks"] if m.get("marked")]
    valid = {c for g in _groups_with_gid() if g["gid"] == gid for c in [g["keep"]] + g["delete"]}
    sel = load_selection()
    sel["marks"][gid] = [m for m in marks if m in valid]
    save_selection(sel)
    return jsonify({"ok": True, "marked": len(sel["marks"][gid])})


@bp.route("/api/selection/decide", methods=["POST"])
def api_sel_decide():
    body = request.get_json(force=True)
    sel = load_selection()
    if body.get("decided"):
        sel["decided"][body["gid"]] = True
    else:
        sel["decided"].pop(body["gid"], None)
    save_selection(sel)
    return jsonify({"ok": True})


@bp.route("/api/selection/bulk", methods=["POST"])
def api_sel_bulk():
    body = request.get_json(force=True)
    mode = body.get("mode")
    groups = _groups_with_gid()
    sel = load_selection()

    if body.get("filters") is not None:
        f = body["filters"]
        rules = load_skip_rules()
        view = groups
        if f.get("risk", "all") not in (None, "all"):
            view = [g for g in view if g["risk"] == f["risk"]]
        if f.get("cat", "all") not in (None, "all"):
            view = [g for g in view if g["category"] == f["cat"]]
        if f.get("ext", "all") not in (None, "all"):
            view = [g for g in view if g["ext"] == f["ext"]]
        skipf = f.get("skip", "all")
        if skipf == "noskip":
            view = [g for g in view if not any(is_skipped(p, g["ext"], rules) for p in [g["keep"]] + g["delete"])]
        elif skipf == "skipped":
            view = [g for g in view if any(is_skipped(p, g["ext"], rules) for p in [g["keep"]] + g["delete"])]
        q = (f.get("q") or "").strip().lower()
        if q:
            view = [g for g in view if q in g["keep"].lower() or any(q in d.lower() for d in g["delete"])]
        if f.get("state") == "decided":
            view = [g for g in view if sel["decided"].get(g["gid"])]
        elif f.get("state") == "undecided":
            view = [g for g in view if not sel["decided"].get(g["gid"])]
        targets = view
    else:
        gids = set(body.get("gids", []))
        targets = [g for g in groups if g["gid"] in gids]

    updated = 0
    for g in targets:
        gid = g["gid"]
        if mode == "accept-suggested":
            sel["marks"][gid] = list(g["delete"])
            sel["decided"][gid] = True
        elif mode == "mark-reviewed":
            sel["decided"][gid] = True
        elif mode in ("clear-marks", "clear-page"):
            sel["marks"][gid] = []
        elif mode in ("all-suggested",):
            sel["marks"][gid] = list(g["delete"])
        else:
            return jsonify({"ok": False, "error": f"unknown mode {mode}"}), 400
        updated += 1
    save_selection(sel)
    return jsonify({"ok": True, "updated": updated})


@bp.route("/api/skip-rules", methods=["GET", "POST", "DELETE"])
def api_skip_rules():
    if request.method == "GET":
        return jsonify({"rules": load_skip_rules()})
    if request.method == "POST":
        body = request.get_json(force=True)
        folder = (body.get("folder") or "").strip().rstrip("\\/")
        ext = body.get("ext")  # None/null = all file types
        # container cannot see host drives, so validate format only:
        # Windows absolute (X:\...) or Unix absolute (/...)
        is_win_abs = len(folder) >= 3 and folder[1:2] == ":" and folder[2:3] in ("\\", "/")
        is_unix_abs = folder.startswith("/") and len(folder) > 1
        if not (is_win_abs or is_unix_abs):
            return jsonify({"ok": False, "error": "invalid folder path"}), 400
        if ext is not None:
            ext = ext.lower()
        rules = load_skip_rules()
        for r in rules:
            if r["folder"].lower() == folder.lower() and r.get("ext") == ext:
                return jsonify({"ok": True, "duplicate": True, "rules": rules})
        rules.append({"folder": folder, "ext": ext, "added": date.today().isoformat()})
        save_skip_rules(rules)
        return jsonify({"ok": True, "rules": rules})
    # DELETE by index
    body = request.get_json(force=True)
    idx = body.get("index")
    rules = load_skip_rules()
    if idx is None or idx < 0 or idx >= len(rules):
        return jsonify({"ok": False, "error": "bad index"}), 400
    removed = rules.pop(idx)
    save_skip_rules(rules)
    return jsonify({"ok": True, "removed": removed, "rules": rules})


def _apply_skip_rules_to_chosen(chosen, g, rules):
    return [p for p in chosen if not is_skipped(p, g["ext"], rules)]


@bp.route("/api/selection/export", methods=["POST"])
def api_sel_export():
    groups = _groups_with_gid()
    sel = load_selection()
    by_cat = {}
    total_bytes = 0
    rules = load_skip_rules()
    for g in groups:
        marks = sel["marks"].get(g["gid"])
        chosen = marks if marks is not None else list(g["delete"])
        chosen = [p for p in chosen if p in [g["keep"]] + g["delete"]]
        chosen = _apply_skip_rules_to_chosen(chosen, g, rules)
        if not chosen:
            continue
        by_cat.setdefault(g["category"], set()).update(chosen)
        total_bytes += len(chosen) * g["size_bytes"]

    os.makedirs(CMD_DIR, exist_ok=True)
    out_path = os.path.join(CMD_DIR, "delete_list_selected.txt")
    order = ["video", "music", "image", "program", "archive", "document", "disk_image", "other"]
    lines = []
    count = 0
    for cat in order + sorted(set(by_cat) - set(order)):
        if cat not in by_cat:
            continue
        paths = sorted(by_cat[cat], key=str.lower)
        lines.append(f"# ===== {cat.upper()} — {len(paths)} files =====")
        lines.extend(paths)
        lines.append("")
        count += len(paths)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return jsonify({"ok": True, "files": count, "gb": round(total_bytes / 1024 ** 3, 2),
                    "file": "commands_to_run/delete_list_selected.txt"})


@bp.route("/api/selection/export-decided", methods=["POST"])
def api_sel_export_decided():
    body = request.get_json(force=True) or {}
    groups = _groups_with_gid()
    sel = load_selection()

    f = body.get("filters")
    view = groups
    rules = load_skip_rules()
    if f:
        if f.get("risk", "all") not in (None, "all"):
            view = [g for g in view if g["risk"] == f["risk"]]
        if f.get("cat", "all") not in (None, "all"):
            view = [g for g in view if g["category"] == f["cat"]]
        if f.get("ext", "all") not in (None, "all"):
            view = [g for g in view if g["ext"] == f["ext"]]
        skipf = f.get("skip", "all")
        if skipf == "noskip":
            view = [g for g in view if not any(is_skipped(p, g["ext"], rules) for p in [g["keep"]] + g["delete"])]
        elif skipf == "skipped":
            view = [g for g in view if any(is_skipped(p, g["ext"], rules) for p in [g["keep"]] + g["delete"])]
        q = (f.get("q") or "").strip().lower()
        if q:
            view = [g for g in view if q in g["keep"].lower() or any(q in d.lower() for d in g["delete"])]

    by_cat = {}
    total_bytes = 0
    used_groups = 0
    skipped_undecided = 0
    skipped_by_rule = 0
    rules = load_skip_rules()
    for g in view:
        if not sel["decided"].get(g["gid"]):
            skipped_undecided += 1
            continue
        marks = sel["marks"].get(g["gid"])
        chosen = marks if marks is not None else list(g["delete"])
        chosen = [p for p in chosen if p in [g["keep"]] + g["delete"]]
        filtered = _apply_skip_rules_to_chosen(chosen, g, rules)
        skipped_by_rule += len(chosen) - len(filtered)
        chosen = filtered
        if not chosen:
            continue
        by_cat.setdefault(g["category"], set()).update(chosen)
        total_bytes += len(chosen) * g["size_bytes"]
        used_groups += 1

    os.makedirs(CMD_DIR, exist_ok=True)
    out_path = os.path.join(CMD_DIR, "delete_list_decided.txt")
    order = ["video", "music", "image", "program", "archive", "document", "disk_image", "other"]
    lines = [f"# Decided-only deletion list — {date.today().isoformat()}",
             f"# {used_groups} groups, filters: {f or 'none'}"]
    count = 0
    for cat in order + sorted(set(by_cat) - set(order)):
        if cat not in by_cat:
            continue
        paths = sorted(by_cat[cat], key=str.lower)
        lines.append(f"# ===== {cat.upper()} — {len(paths)} files =====")
        lines.extend(paths)
        lines.append("")
        count += len(paths)
    with open(out_path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(lines))
    return jsonify({"ok": True, "files": count, "groups": used_groups,
                    "skipped_undecided": skipped_undecided,
                    "skipped_by_rule": skipped_by_rule,
                    "gb": round(total_bytes / 1024 ** 3, 2),
                    "file": "commands_to_run/delete_list_decided.txt",
                    "command": f'powershell -ExecutionPolicy Bypass -File "H:\\projects\\folder_organizer\\commands_to_run\\delete_duplicates.ps1" -List "H:\\projects\\folder_organizer\\commands_to_run\\delete_list_decided.txt"'})


@bp.route("/skipped")
def skipped_page():
    rules = load_skip_rules()
    # what each rule currently protects: count files + bytes it would block
    groups = _groups_with_gid()
    sel = load_selection()
    rule_stats = []
    for i, r in enumerate(rules):
        files = 0
        bytes_ = 0
        for g in groups:
            marks = sel["marks"].get(g["gid"])
            chosen = marks if marks is not None else list(g["delete"])
            for p in chosen:
                if p in [g["keep"]] + g["delete"] and is_skipped(p, g["ext"], [r]):
                    files += 1
                    bytes_ += g["size_bytes"]
        rule_stats.append({**r, "blocked_files": files, "blocked_bytes": bytes_, "index": i})
    return render_template("organizer/skipped.html", rules=rule_stats)


@bp.route("/set-theme")
def set_theme():
    theme = request.args.get("value", "light")
    if theme not in ("light", "dark"):
        theme = "light"
    resp = jsonify({"ok": True, "theme": theme})
    resp.set_cookie("theme", theme, max_age=365 * 86400, samesite="Lax")
    return resp


def _music_problems_filtered(audit, plan, state, folder, missing, q):
    problems = audit.get("problems", [])
    entries = plan.get("entries", {})
    if folder:
        problems = [p for p in problems if f"{folder}\\" in p["path"] or p["path"].endswith("\\" + folder)]
    if missing and missing != "all":
        problems = [p for p in problems if missing in p.get("missing", [])]
    if state == "planned":
        problems = [p for p in problems if p["path"] in entries]
    elif state == "unplanned":
        problems = [p for p in problems if p["path"] not in entries]
    elif state == "accepted":
        problems = [p for p in problems if entries.get(p["path"], {}).get("accepted")]
    if q:
        problems = [p for p in problems if q in p["path"].lower()]
    return problems


def _audit_by_path(audit):
    return {p["path"]: p for p in audit.get("problems", [])}


@bp.route("/api/tags/propose", methods=["POST"])
def api_tags_propose():
    from . import tag_detect
    body = request.get_json(force=True)
    filters = body.get("filters") or {}
    audit = load("music_tag_audit.json") or {"problems": []}
    plan = load_tag_plan()
    problems = _music_problems_filtered(audit, plan, filters.get("state", "unplanned"),
                                        filters.get("folder"), filters.get("missing", "all"),
                                        (filters.get("q") or "").strip().lower())
    if len(problems) > 5000:
        return jsonify({"ok": False, "error": f"{len(problems)} files match — narrow your filter (max 5000 per batch)"}), 400
    catalog_mtime = {c["path"]: c.get("mtime") for c in (load("music_catalog.json") or [])}
    entries = [{"path": p["path"], "mtime": catalog_mtime.get(p["path"])} for p in problems]
    proposed = tag_detect.propose_for(entries, _audit_by_path(audit))
    for path, entry in proposed.items():
        old = plan["entries"].get(path, {})
        entry["accepted"] = old.get("accepted", False)
        entry["applied"] = old.get("applied", False)
        plan["entries"][path] = entry
    save_tag_plan(plan)
    return jsonify({"ok": True, "proposed": len(proposed), "plan_size": len(plan["entries"])})


@bp.route("/api/tags/set-entry", methods=["POST"])
def api_tags_set_entry():
    body = request.get_json(force=True)
    path = body.get("path")
    if not path:
        return jsonify({"ok": False, "error": "path required"}), 400
    plan = load_tag_plan()
    entry = plan["entries"].setdefault(path, {"fields": {}, "accepted": False, "applied": False})
    for field, spec in (body.get("fields") or {}).items():
        if field not in ("artist", "title", "album", "year", "genre"):
            continue
        value = (spec.get("value") or "").strip()
        if value == "":
            entry["fields"].pop(field, None)
            continue
        entry["fields"][field] = {"value": value, "source": "manual",
                                  "overwrite": bool(spec.get("overwrite")),
                                  "confidence": "high"}
    if "accepted" in body:
        entry["accepted"] = bool(body["accepted"])
    plan["entries"][path] = entry
    save_tag_plan(plan)
    return jsonify({"ok": True, "entry": entry})


@bp.route("/api/tags/bulk", methods=["POST"])
def api_tags_bulk():
    body = request.get_json(force=True)
    mode = body.get("mode")
    plan = load_tag_plan()
    entries = plan["entries"]
    if body.get("page_paths"):
        targets = set(body["page_paths"])
    elif body.get("filters") is not None:
        f = body["filters"]
        audit = load("music_tag_audit.json") or {"problems": []}
        paths = {p["path"] for p in _music_problems_filtered(
            audit, plan, f.get("state", "all"), f.get("folder"), f.get("missing", "all"),
            (f.get("q") or "").strip().lower())}
        targets = paths
    else:
        return jsonify({"ok": False, "error": "page_paths or filters required"}), 400
    n = 0
    for path in targets:
        if path not in entries:
            continue
        if mode == "accept":
            entries[path]["accepted"] = True
        elif mode == "unaccept":
            entries[path]["accepted"] = False
        elif mode == "accept-safe":
            # auto-accept only when every field is med/high confidence AND no overwrite is requested
            fields = entries[path].get("fields", {})
            safe = fields and all(
                spec.get("confidence") in ("high", "med") and not spec.get("overwrite")
                for spec in fields.values())
            if safe:
                entries[path]["accepted"] = True
        elif mode == "delete":
            del entries[path]
        else:
            return jsonify({"ok": False, "error": f"unknown mode {mode}"}), 400
        n += 1
    save_tag_plan(plan)
    return jsonify({"ok": True, "updated": n})


@bp.route("/api/tags/clear-plan", methods=["POST"])
def api_tags_clear_plan():
    save_tag_plan({"version": 1, "updated": None, "entries": {}})
    return jsonify({"ok": True})


@bp.route("/api/tags/export", methods=["POST"])
def api_tags_export():
    body = request.get_json(silent=True) or {}
    only_accepted = body.get("only_accepted", True)
    plan = load_tag_plan()
    audit = _audit_by_path(load("music_tag_audit.json") or {"problems": []})
    out_entries = []
    skipped_existing = 0
    skipped_unaccepted = 0
    for path, entry in plan["entries"].items():
        if only_accepted and not entry.get("accepted"):
            skipped_unaccepted += 1
            continue
        fields = entry.get("fields", {})
        if not fields:
            continue
        tags = {}
        overwrites = []
        cur = audit.get(path, {})
        for f, spec in fields.items():
            v = spec.get("value")
            if not v:
                continue
            if cur.get(f) and not spec.get("overwrite"):
                skipped_existing += 1
                continue
            tags[f] = v
            if cur.get(f):
                overwrites.append(f)
        if tags:
            out_entries.append({"path": path, "tags": tags, "overwrites": overwrites,
                                "ext": os.path.splitext(path)[1].lower()})
    if not out_entries:
        if skipped_unaccepted and not skipped_existing:
            why = "nothing is accepted yet — click Accept or Auto-accept safe first"
        elif skipped_existing and not skipped_unaccepted:
            why = "every proposed field already has a tag — tick overwrite to replace it"
        else:
            why = "nothing accepted and nothing writable — re-detect or clear the plan"
        return jsonify({"ok": False, "error": f"Nothing to export: {why}.",
                        "skipped_unaccepted": skipped_unaccepted,
                        "skipped_existing": skipped_existing}), 400
    os.makedirs(CMD_DIR, exist_ok=True)
    payload = {"generated": date.today().isoformat(), "count": len(out_entries), "entries": out_entries}
    tmp = TAG_FIX_LIST + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    os.replace(tmp, TAG_FIX_LIST)
    return jsonify({"ok": True, "count": len(out_entries), "skipped_existing": skipped_existing,
                    "skipped_unaccepted": skipped_unaccepted,
                    "file": "commands_to_run/tag_fix_list.json",
                    "dry_command": run_cmd("apply_music_tags.py"),
                    "apply_command": run_cmd("apply_music_tags.py", "--apply")})


@bp.route("/api/tags/merge-mb", methods=["POST"])
def api_tags_merge_mb():
    if not os.path.exists(MB_CAND_FILE):
        return jsonify({"ok": False, "error": "no mb_candidates.json — run musicbrainz_lookup.py on the host first"}), 400
    with open(MB_CAND_FILE, encoding="utf-8") as f:
        cands = json.load(f)
    plan = load_tag_plan()
    merged = 0
    for path, entry in plan["entries"].items():
        cand = cands.get(path)
        if not cand or cand.get("score", 0) < 70:
            continue
        for f in ("artist", "album", "year"):
            spec = entry["fields"].get(f)
            if cand.get(f) and (not spec or not spec.get("value")):
                entry["fields"][f] = {"value": cand[f], "source": "musicbrainz",
                                      "overwrite": False, "confidence": "med"}
                merged += 1
    save_tag_plan(plan)
    return jsonify({"ok": True, "fields_merged": merged})


@bp.route("/api/tags/stats")
def api_tags_stats():
    plan = load_tag_plan()
    entries = plan["entries"]
    src = Counter()
    accepted = 0
    for e in entries.values():
        if e.get("accepted"):
            accepted += 1
        for spec in e.get("fields", {}).values():
            src[spec.get("source", "?")] += 1
    return jsonify({"ok": True, "entries": len(entries), "accepted": accepted,
                    "fields_by_source": dict(src)})


@bp.route("/api/summary")
def api_summary():
    return jsonify(load("scan_summary.json") or {})


@bp.route("/api/duplicates")
def api_duplicates():
    return jsonify(load("duplicates.json") or {})


@bp.route("/api/catalog/<category>")
def api_catalog(category):
    valid = {"music": "music_catalog.json", "video": "video_catalog.json",
             "program": "programs_catalog.json", "disk_image": "disk_images_catalog.json",
             "full": "catalog_full.json"}
    if category not in valid:
        return jsonify({"error": "unknown category"}), 404
    return jsonify(load(valid[category]) or [])
