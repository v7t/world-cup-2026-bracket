"""Build an interactive web page for the FIFA World Cup 2026 knockout bracket.

Writes a single self-contained HTML page containing:
  * the circular bracket, drawn as vector SVG in the browser
  * a slider (bottom) from 0 games played up to the number of decided games
  * a results list (right) that reveals matches as the slider advances

Every flag/crest/logo is embedded once as an SVG data-URI, and the browser
re-derives the bracket for any slider position, so scrubbing is instant.

    python build_web.py                 # -> docs/index.html (served by GitHub Pages)
    python build_web.py out/page.html   # -> a path of your choosing

This is a standalone companion to WC26_Brackets.py (which renders the static
PNG/SVG); it does not import or modify it.  The team table below is kept in
sync with the one there by hand.
"""

import os
import re
import json
import base64
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from download_resources import crest_filename, download_worldcup_json, round_of_32_teams

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FLAG_SVG_DIR = os.path.join(SCRIPT_DIR, "flags_svg")
CREST_DIR = os.path.join(SCRIPT_DIR, "crests_svg")
LOGO_PATH = os.path.join(SCRIPT_DIR, "2026_FIFA_World_Cup_logo.svg")
WORLDCUP_PATH = os.path.join(SCRIPT_DIR, "worldcup.json")
HIGHLIGHTS_PATH = os.path.join(SCRIPT_DIR, "worldcup_highlights.json")
# Default output is docs/index.html: that is what GitHub Pages serves
# (Settings -> Pages -> Deploy from a branch -> master -> /docs).
OUT_PATH = os.path.join(SCRIPT_DIR, "docs", "index.html")

# (name, flag-icons code, federation acronym) -- clockwise from 12 o'clock;
# adjacent pairs are Round-of-32 matchups.  Mirrors WC26_Brackets.py.
TEAMS = [
    ("Brazil", "br", "CBF"), ("Japan", "jp", "JFA"),
    ("Ivory Coast", "ci", "FIF"), ("Norway", "no", "NFF"),
    ("Mexico", "mx", "FMF"), ("Ecuador", "ec", "FEF"),
    ("England", "gb-eng", "FA"), ("Democratic\nRepublic\nof Congo", "cd", "FECOFA"),
    ("Argentina", "ar", "AFA"), ("Cape Verde", "cv", "FCF"),
    ("Australia", "au", "FA"), ("Egypt", "eg", "EFA"),
    ("Switzerland", "ch", "SFV"), ("Algeria", "dz", "FAF"),
    ("Colombia", "co", "FCF"), ("Ghana", "gh", "GFA"),
    ("Senegal", "sn", "FSF"), ("Belgium", "be", "RBFA"),
    ("Bosnia and\nHerzegovina", "ba", "NFSBIH"), ("United States\nof America", "us", "USSF"),
    ("Austria", "at", "OFB"), ("Spain", "es", "RFEF"),
    ("Croatia", "hr", "HNS"), ("Portugal", "pt", "FPF"),
    ("Morocco", "ma", "FRMF"), ("Netherlands", "nl", "KNVB"),
    ("Canada", "ca", "CSA"), ("South Africa", "za", "SAFA"),
    ("Sweden", "se", "SVFF"), ("France", "fr", "FFF"),
    ("Paraguay", "py", "APF"), ("Germany", "de", "DFB"),
]
assert len(TEAMS) == 32

RADII = [1.00, 0.82, 0.64, 0.46, 0.28, 0.0]
# The third-place play-off is listed in the results panel for completeness even
# though it has no node on the bracket: its two teams both lost their
# semi-finals, so they never share a node and the graph is unaffected.
KNOCKOUT_ROUNDS = ("Round of 32", "Round of 16", "Quarter-final", "Semi-final",
                   "Match for third place", "Final")
ROUND_LABEL = {"Round of 32": "Round of 32", "Round of 16": "Round of 16",
               "Quarter-final": "Quarter Finals", "Semi-final": "Semi Finals",
               "Match for third place": "Third Place", "Final": "Final"}

CODE_BY_NAME = {name.lower(): code for name, code, _ in TEAMS}
CODE_BY_NAME.update({
    "united states": "us", "usa": "us",
    "cote d'ivoire": "ci", "côte d'ivoire": "ci", "ivory coast": "ci",
    "congo dr": "cd", "dr congo": "cd",
    "democratic republic of the congo": "cd",
    "bosnia and herzegovina": "ba", "bosnia & herzegovina": "ba",
    "cabo verde": "cv", "cape verde": "cv",
    "england": "gb-eng",
})

# Image sizes are taken from matplotlib so the page matches the PNG/SVG output.
# Each flag/crest is an OffsetImage of  native_px * zoom * (dpi/72)  display
# pixels; convert that through the axes transform to get data units.
FLAG_PX, FLAG_ZOOM, CREST_ZOOM = 240, 0.155, 0.190

# Champion badge: the winner's flag sits over the trophy on the centre logo,
# matching the beaten finalist's flag exactly -- same size, and level with it
# on the horizontal centre line (the finalist node sits at y = 0).
CHAMP_ZOOM = FLAG_ZOOM  # same size as every other flag
CHAMP_FLAG_Y = 0.0      # same height as the losing finalist
CHAMP_TEXT_Y = -0.215   # label sits clear of the logo's lower edge
CHAMP_SUB_DY = -0.052   # subtitle offset below the nation's name
CHAMP_NAME_FS = 16      # nation: large and bold   (matplotlib points)
CHAMP_SUB_FS = 9        # subtitle: small, regular weight
CHAMP_SUBTITLE = "World Cup Champions"


def layout_sizes():
    """Flag/crest/champion/logo sizes in data units, as matplotlib renders them."""
    fig, ax = plt.subplots(figsize=(16, 16))
    ax.set_xlim(-1.6, 1.6)
    ax.set_ylim(-1.6, 1.6)
    ax.set_aspect("equal")
    ax.axis("off")
    plt.tight_layout()
    fig.canvas.draw()
    dpi_cor = fig.dpi / 72.0
    inv = ax.transData.inverted()

    def to_data(px):
        return abs(inv.transform((px, 0))[0] - inv.transform((0, 0))[0])

    logo_aspect = svg_aspect(LOGO_PATH)          # w/h
    logo_native_h = 800 / logo_aspect
    sizes = {
        "flagD": to_data(FLAG_PX * FLAG_ZOOM * dpi_cor),
        "crestD": to_data(260 * CREST_ZOOM * dpi_cor),
        "champD": to_data(FLAG_PX * CHAMP_ZOOM * dpi_cor),
        "logoW": to_data(800 * 0.11 * dpi_cor),
        "logoH": to_data(logo_native_h * 0.11 * dpi_cor),
    }
    plt.close(fig)
    return sizes


def svg_aspect(path):
    """Intrinsic width/height of an SVG (viewBox first, else width/height --
    some crests, e.g. Portugal, declare width/height and no viewBox)."""
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        head = fh.read(1200)
    m = re.search(r'viewBox\s*=\s*"[\d.eE+\-]+\s+[\d.eE+\-]+\s+'
                  r'([\d.eE+\-]+)\s+([\d.eE+\-]+)"', head)
    if not m:
        mw = re.search(r'\bwidth\s*=\s*"([\d.]+)', head)
        mh = re.search(r'\bheight\s*=\s*"([\d.]+)', head)
        if mw and mh:
            return float(mw.group(1)) / float(mh.group(1))
        return 1.0
    w, h = float(m.group(1)), float(m.group(2))
    return w / h if h else 1.0


def data_uri(path):
    with open(path, "rb") as fh:
        return "data:image/svg+xml;base64," + base64.b64encode(fh.read()).decode("ascii")


def resolve_code(team_name):
    return CODE_BY_NAME.get(str(team_name).lower())


def winner_index(score):
    """Penalties, then extra time, then full time -- as download_resources does."""
    if not isinstance(score, dict):
        return None
    for key in ("p", "et", "ft"):
        v = score.get(key)
        if isinstance(v, list) and len(v) == 2 and all(isinstance(i, int) for i in v) and v[0] != v[1]:
            return 0 if v[0] > v[1] else 1
    return None


def score_text(score):
    if not isinstance(score, dict):
        return ""
    ft, et, p = score.get("ft"), score.get("et"), score.get("p")
    txt = f"{ft[0]}-{ft[1]}" if isinstance(ft, list) and len(ft) == 2 else ""
    if isinstance(et, list) and len(et) == 2:
        txt = f"{et[0]}-{et[1]} aet"
    if isinstance(p, list) and len(p) == 2:
        txt += f" (pens {p[0]}-{p[1]})"
    return txt


def load_highlights():
    """match number -> {'highlights': {...}, 'extended': {...}} video links.

    Optional: the page degrades gracefully to 'no video' if the file is absent.
    Keyed on match_number, which lines up exactly with worldcup.json's 'num'.
    """
    if not os.path.exists(HIGHLIGHTS_PATH):
        print("[warn] worldcup_highlights.json not found; no video links.")
        return {}, ""
    with open(HIGHLIGHTS_PATH, encoding="utf-8") as fh:
        data = json.load(fh)
    by_num = {}
    for m in data.get("matches", []):
        num = m.get("match_number")
        if num is None:
            continue
        entry = {}
        for src, dst in (("highlights", "hl"), ("extended_highlights", "ext")):
            v = m.get(src)
            if isinstance(v, dict) and v.get("url"):
                vid = re.search(r"[?&]v=([\w-]+)", v["url"])
                entry[dst] = {"title": v.get("title", ""), "url": v["url"],
                              "id": vid.group(1) if vid else ""}
        if entry:
            by_num[num] = entry
    return by_num, data.get("source_channel", "")


def _goals(lst, code):
    """Normalise a worldcup.json goals list for one team into render-ready rows."""
    out = []
    for g in lst or []:
        out.append({"code": code, "name": g.get("name", ""),
                    "min": str(g.get("minute", "")),
                    "pen": bool(g.get("penalty")), "og": bool(g.get("owngoal"))})
    return out


def _minute_key(g):
    """Sort key for a goal minute like '45', '90+2', '120+5'."""
    m = re.match(r"(\d+)(?:\+(\d+))?", g["min"])
    return (int(m.group(1)), int(m.group(2) or 0)) if m else (0, 0)


def collect_matches():
    """Decided knockout matches, in chronological order, with video links."""
    with open(WORLDCUP_PATH, encoding="utf-8") as fh:
        data = json.load(fh)
    videos, _ = load_highlights()
    rows = []
    for m in data["matches"]:
        rnd = m.get("round")
        if rnd not in KNOCKOUT_ROUNDS:
            continue
        a, b = resolve_code(m.get("team1")), resolve_code(m.get("team2"))
        wi = winner_index(m.get("score"))
        if not a or not b or wi is None:
            continue                      # undecided, or a "W73"-style placeholder
        num = m.get("num") or 0
        goals = _goals(m.get("goals1"), a) + _goals(m.get("goals2"), b)
        goals.sort(key=_minute_key)
        rows.append({
            "round": ROUND_LABEL.get(rnd, rnd),
            "date": m.get("date", ""),
            "a": a, "b": b,
            "winner": (a, b)[wi],
            "score": score_text(m.get("score")),
            "num": num,
            "ground": m.get("ground", ""),
            "goals": goals,
            "video": videos.get(num, {}),
        })
    rows.sort(key=lambda r: (r["date"], r["num"]))
    return rows


def build_payload():
    try:
        download_worldcup_json(WORLDCUP_PATH)
        print("Refreshed World Cup data from GitHub.")
    except RuntimeError as exc:
        print(f"[warn] refresh failed ({exc}); using local copy.")

    # code -> the country name worldcup.json uses (for crest filenames)
    country_by_code = {}
    for team_name in round_of_32_teams(WORLDCUP_PATH):
        code = resolve_code(team_name)
        if code:
            country_by_code[code] = team_name

    flags, crests, aspects = {}, {}, {}
    for name, code, _ in TEAMS:
        flags[code] = data_uri(os.path.join(FLAG_SVG_DIR, f"{code}.svg"))
        crest_path = os.path.join(CREST_DIR, crest_filename(country_by_code.get(code, name)))
        if os.path.exists(crest_path):
            crests[code] = data_uri(crest_path)
            aspects[code] = svg_aspect(crest_path)

    matches = collect_matches()
    _, source_channel = load_highlights()
    n_vid = sum(1 for m in matches if m["video"])
    print(f"Loaded {len(matches)} decided knockout match(es); {n_vid} with video.")

    payload = {
        "canvas": 1600, "view": 1.6, "fs": 15,
        "champFlagY": CHAMP_FLAG_Y, "champTextY": CHAMP_TEXT_Y,
        "champSubDy": CHAMP_SUB_DY, "champSubtitle": CHAMP_SUBTITLE,
        # matplotlib point sizes -> SVG user units (team names: 9 pt drawn at fs 15)
        "champNameFs": CHAMP_NAME_FS * 15 / 9, "champSubFs": CHAMP_SUB_FS * 15 / 9,
        "radii": RADII,
        "startAngleDeg": 90 - (360 / len(TEAMS)) / 2,
        "angleStepDeg": 360 / len(TEAMS),
        "colors": {"bg": "#0a0a0a", "line": "#5a5a5a", "text": "#f2f2f2",
                   "gold": "#e6c35c", "white": "#ffffff"},
        "teams": [{"name": n, "code": c, "acronym": a} for n, c, a in TEAMS],
        "matches": matches,
        "sourceChannel": source_channel,
        "flags": flags, "crests": crests, "crestAspect": aspects,
        "logo": data_uri(LOGO_PATH),
    }
    payload.update(layout_sizes())
    return payload


HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FIFA World Cup 2026 — Knockout Bracket</title>
<style>
  /* Whole-page font scale: 175% of the 16px default -> 1rem = 28px.  Every
     text size below is in rem, so it tracks this one knob.  (The bracket SVG
     uses its own font sizes in user units and is unaffected.) */
  :root { font-size: 175%; }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: #0a0a0a; color: #f2f2f2; height: 100vh;
    display: flex; flex-direction: column; overflow: hidden;
    font-family: "Segoe UI", system-ui, -apple-system, sans-serif;
  }
  .main { flex: 1; display: flex; min-height: 0; }
  .stage {
    flex: 1; min-width: 0; display: flex; align-items: center;
    justify-content: center; padding: 8px;
  }
  .stage svg { width: 100%; height: 100%; max-height: 100%; }

  .sidebar {
    width: 380px; flex-shrink: 0; border-left: 1px solid #242424;
    display: flex; flex-direction: column; min-height: 0;
  }
  .sidebar h2, .details h2 {
    margin: 0; padding: .5rem .6rem .38rem; font-size: .82rem; letter-spacing: .12em;
    text-transform: uppercase; color: #9a9a9a; border-bottom: 1px solid #242424;
  }
  /* scrollable, but with the scrollbar chrome hidden on both columns */
  .results, .detail-body {
    scrollbar-width: none;              /* Firefox */
    -ms-overflow-style: none;           /* old Edge */
  }
  .results::-webkit-scrollbar, .detail-body::-webkit-scrollbar { width: 0; height: 0; }
  .results { overflow-y: auto; padding: .25rem .3rem .6rem; flex: 1; }
  .rnd-head {
    font-size: .7rem; letter-spacing: .14em; color: #e6c35c; padding: .5rem .3rem .2rem;
    text-transform: uppercase;
  }
  .row {
    display: grid; grid-template-columns: 1fr auto; gap: .25rem; align-items: center;
    padding: .28rem .3rem; border-radius: 6px; cursor: pointer; margin-bottom: .08rem;
    border: 1px solid transparent;
  }
  .row:hover { background: #171717; }
  .row.future { opacity: .28; }
  .row.latest { background: #1c1c1c; border-color: #e6c35c; }
  .side { display: flex; align-items: center; gap: .3rem; font-size: .82rem; line-height: 1.25; }
  .side + .side { margin-top: .12rem; }
  .side img { width: 1.2rem; height: 1.2rem; border-radius: 50%; object-fit: cover; flex-shrink: 0; }
  .side.win { font-weight: 600; color: #ffffff; }
  .side.lose { color: #7c7c7c; }
  .score { font-variant-numeric: tabular-nums; font-size: .76rem; color: #b9b9b9; text-align: right; }
  .date { font-size: .64rem; color: #6a6a6a; margin-top: .12rem; }
  .row.selected { background: #20242c; border-color: #3f7bd6; }

  /* right-most column: match info, goals and the embedded highlights video */
  .details {
    width: 460px; flex-shrink: 0; border-left: 1px solid #242424;
    display: flex; flex-direction: column; min-height: 0;
  }
  .detail-body { overflow-y: auto; padding: 1rem; flex: 1; }
  .d-round {
    font-size: .7rem; letter-spacing: .14em; text-transform: uppercase;
    color: #e6c35c; margin-bottom: .6rem;
  }
  .d-team {
    display: flex; align-items: center; gap: .5rem; font-size: 1rem; padding: .25rem 0;
  }
  .d-team img { width: 1.9rem; height: 1.9rem; border-radius: 50%; object-fit: cover; }
  .d-team .nm { flex: 1; }
  .d-team.win { font-weight: 700; color: #fff; }
  .d-team.win .nm::after { content: " ✓"; color: #e6c35c; }
  .d-team.lose { color: #8a8a8a; }
  .d-team .gl { font-variant-numeric: tabular-nums; font-size: 1.15rem; }
  .d-vs { font-size: .7rem; color: #6a6a6a; padding: .1rem 0 .1rem 2.4rem; }
  .d-meta { font-size: .76rem; color: #8a8a8a; margin: .7rem 0 .2rem; line-height: 1.5; }
  .d-meta b { color: #c8c8c8; font-weight: 600; }

  .sec-h {
    font-size: .64rem; letter-spacing: .12em; text-transform: uppercase;
    color: #7a7a7a; margin: .9rem 0 .3rem;
  }
  .goals { display: flex; flex-direction: column; gap: .3rem; }
  .goal { display: flex; align-items: center; gap: .45rem; font-size: .8rem; color: #d6d6d6; }
  .goal img { width: 1.05rem; height: 1.05rem; border-radius: 50%; object-fit: cover; flex-shrink: 0; }
  .goal .min { color: #e6c35c; font-variant-numeric: tabular-nums;
    min-width: 2.6em; text-align: right; }
  .goal .tag { color: #8a8a8a; font-size: .82em; }
  .no-goals { font-size: .74rem; color: #7a7a7a; font-style: italic; }

  .vids { margin-top: .55rem; display: flex; flex-direction: column; gap: .4rem; }
  .vid {
    display: flex; align-items: center; gap: .5rem; text-decoration: none;
    background: #1a1a1a; border: 1px solid #333; border-radius: 8px;
    padding: .5rem .6rem; color: #f2f2f2; font-size: .8rem;
  }
  .vid:hover { background: #232323; border-color: #c4302b; }
  .vid .yt {
    flex-shrink: 0; width: 1.6rem; height: 1.1rem; background: #c4302b; border-radius: 4px;
    display: flex; align-items: center; justify-content: center;
  }
  .vid .yt::after { content: ""; border-left: .5rem solid #fff;
    border-top: .32rem solid transparent; border-bottom: .32rem solid transparent; margin-left: .12rem; }
  .vid .vt { display: flex; flex-direction: column; line-height: 1.3; min-width: 0; }
  .vid .vt b { font-size: .8rem; }
  .vid .vt span { font-size: .68rem; color: #9a9a9a;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .no-vid { font-size: .76rem; color: #6a6a6a; margin-top: .8rem; font-style: italic; }
  .src { font-size: .64rem; color: #5a5a5a; margin-top: 1rem; }
  .src a { color: #7a7a7a; }
  .empty { font-size: .82rem; color: #6a6a6a; padding: .5rem 0; line-height: 1.5; }

  .footer { border-top: 1px solid #242424; padding: .5rem 1rem .6rem; }
  .ctrl { display: flex; align-items: center; gap: .7rem; }
  button {
    background: #1d1d1d; color: #f2f2f2; border: 1px solid #3a3a3a; border-radius: 6px;
    padding: .35rem .8rem; cursor: pointer; font-size: .82rem; min-width: 4.6rem;
  }
  button:hover { background: #292929; }
  input[type=range] { flex: 1; accent-color: #e6c35c; height: 1.4rem; cursor: pointer; }
  .count { font-size: .82rem; color: #b9b9b9; min-width: 9rem; font-variant-numeric: tabular-nums; }
  .count b { color: #e6c35c; font-size: .95rem; }
</style>
</head>
<body>
  <div class="main">
    <div class="stage" id="stage"></div>
    <aside class="sidebar">
      <h2>Results</h2>
      <div class="results" id="results"></div>
    </aside>
    <aside class="details">
      <h2>Match</h2>
      <div class="detail-body" id="details"></div>
    </aside>
  </div>
  <div class="footer">
    <div class="ctrl">
      <button id="play">Play</button>
      <input type="range" id="slider" min="0" step="1">
      <div class="count"><b id="n">0</b> <span id="total"></span></div>
    </div>
  </div>

<script id="payload" type="application/json">/*__DATA__*/</script>
<script>
const D = JSON.parse(document.getElementById('payload').textContent);
const S = D.canvas / (2 * D.view);
const C = D.colors;
const TAU = Math.PI * 2;

const pmod = (x, m) => ((x % m) + m) % m;          // Python-style modulo
const leafAngle = i => (D.startAngleDeg - i * D.angleStepDeg) * Math.PI / 180;
const polar = (r, t) => [r * Math.cos(t), r * Math.sin(t)];
const toPx = (x, y) => [D.canvas / 2 + x * S, D.canvas / 2 - y * S];
const key = (a, b) => [a, b].sort().join('|');

function arcPoints(r, a, b, n = 40) {
  const d = pmod(b - a + Math.PI, TAU) - Math.PI;
  const pts = [];
  for (let i = 0; i < n; i++) {
    const t = a + d * (i / (n - 1));
    pts.push([r * Math.cos(t), r * Math.sin(t)]);
  }
  return pts;
}

// Replays the bracket using only the first `n` results -- mirrors the tree
// build in WC26_Brackets.py (winners advance inward; losers drop out).
function buildTree(n) {
  const winners = new Map();
  for (let i = 0; i < n; i++) {
    const m = D.matches[i];
    winners.set(key(m.a, m.b), m.winner);
  }
  let current = D.teams.map((t, i) => ({ r: D.radii[0], a: leafAngle(i), code: t.code }));
  const gray = [], white = [], dots = [], wdots = [];
  let flags = [], eliminated = new Set(), champion = null, round = 0;

  while (current.length > 1) {
    const next = [], rHere = current[0].r, rNext = D.radii[round + 1];
    for (let i = 0; i < current.length; i += 2) {
      const A = current[i], B = current[i + 1];
      const mid = A.a + (pmod(B.a - A.a + Math.PI, TAU) - Math.PI) / 2;
      let win = null;
      if (A.code && B.code) {
        win = winners.get(key(A.code, B.code)) || null;
        if (win) eliminated.add(win === A.code ? B.code : A.code);
      }
      for (const ch of [A, B]) {
        const seg = [polar(rHere, ch.a), polar(rNext, ch.a)];
        const arc = arcPoints(rNext, ch.a, mid);
        const dest = (win && ch.code === win) ? white : gray;
        dest.push(seg); dest.push(arc);
      }
      if (rNext > 0) { dots.push(polar(rNext, A.a)); dots.push(polar(rNext, B.a)); }
      if (win && rNext > 0) {
        wdots.push(polar(rNext, win === A.code ? A.a : B.a));
        const [mx, my] = polar(rNext, mid);
        let fx = mx, fy = my;
        if (round + 2 <= D.radii.length - 2) {
          [fx, fy] = polar(D.radii[round + 2], mid);
          white.push([[mx, my], [fx, fy]]);
        }
        flags.push({ x: fx, y: fy, code: win, round });
      } else if (win) {
        champion = win;
      }
      next.push({ r: rNext, a: mid, code: win });
    }
    current = next; round++;
  }
  // a flag moves inward rather than repeating: keep only its deepest position
  const deepest = new Map();
  for (const f of flags) {
    const p = deepest.get(f.code);
    if (!p || f.round > p.round) deepest.set(f.code, f);
  }
  const out = [];
  for (const f of deepest.values()) {
    if (f.code === champion) wdots.push([f.x, f.y]); else out.push(f);
  }
  return { gray, white, dots, wdots, flags: out, eliminated, champion };
}

const poly = (pts, color, w) =>
  `<polyline points="${pts.map(p => toPx(p[0], p[1]).map(v => v.toFixed(2)).join(',')).join(' ')}"
    fill="none" stroke="${color}" stroke-width="${w}" stroke-linecap="round" stroke-linejoin="round"/>`;

function flagUse(x, y, code, d, grey) {
  const [cx, cy] = toPx(x, y), r = d * S / 2;
  const f = grey ? ' filter="url(#grey)"' : '';
  return `<use href="#f-${code}" x="${(cx - r).toFixed(2)}" y="${(cy - r).toFixed(2)}"
    width="${(2 * r).toFixed(2)}" height="${(2 * r).toFixed(2)}" clip-path="url(#circ)"${f}/>
    <circle cx="${cx.toFixed(2)}" cy="${cy.toFixed(2)}" r="${r.toFixed(2)}" fill="none"
      stroke="#fff" stroke-width="${Math.max(1.4, r * 0.055).toFixed(2)}"/>`;
}

function nameText(x, y, name, theta) {
  const [cx, cy] = toPx(x, y);
  let rot = pmod(theta * 180 / Math.PI, 360), anchor = 'start';
  if (rot > 90 && rot < 270) { rot += 180; anchor = 'end'; }
  const lines = name.split('\n'), lh = D.fs * 1.05;
  const spans = lines.map((ln, i) =>
    `<tspan x="${cx.toFixed(1)}" dy="${(i === 0 ? -(lines.length - 1) / 2 * lh : lh).toFixed(1)}">${ln}</tspan>`).join('');
  return `<text transform="rotate(${(-rot).toFixed(2)} ${cx.toFixed(1)} ${cy.toFixed(1)})"
    x="${cx.toFixed(1)}" y="${cy.toFixed(1)}" text-anchor="${anchor}" dominant-baseline="middle"
    font-size="${D.fs}" fill="${C.text}" font-family="Segoe UI, system-ui, sans-serif">${spans}</text>`;
}

// Static half of the drawing: defs + outer ring (flags/crests/names never change).
function staticSvg() {
  const symbols = Object.entries(D.flags).map(([code, uri]) =>
    `<symbol id="f-${code}" viewBox="0 0 100 100" preserveAspectRatio="xMidYMid slice">
       <image x="0" y="0" width="100" height="100" preserveAspectRatio="xMidYMid slice" href="${uri}"/>
     </symbol>`).join('');

  let s = '';
  D.teams.forEach((t, i) => {
    const th = leafAngle(i);
    const [fx, fy] = polar(D.radii[0], th);
    s += flagUse(fx, fy, t.code, D.flagD, false);

    const uri = D.crests[t.code];
    if (uri) {                       // crest: mirrors pad_to_square (0.92, centred)
      const [cx, cy] = toPx(...polar(D.radii[0] + 0.175, th));
      const box = D.crestD * 0.92 * S, asp = D.crestAspect[t.code] || 1;
      const w = asp >= 1 ? box : box * asp, h = asp >= 1 ? box / asp : box;
      s += `<image x="${(cx - w / 2).toFixed(2)}" y="${(cy - h / 2).toFixed(2)}"
        width="${w.toFixed(2)}" height="${h.toFixed(2)}" preserveAspectRatio="xMidYMid meet" href="${uri}"/>`;
    }
    const [tx, ty] = polar(D.radii[0] + 0.30, th);
    s += nameText(tx, ty, t.name, th);
  });

  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${D.canvas} ${D.canvas}">
    <defs>
      ${symbols}
      <clipPath id="circ" clipPathUnits="objectBoundingBox"><circle cx=".5" cy=".5" r=".5"/></clipPath>
      <filter id="grey"><feColorMatrix type="saturate" values="0"/></filter>
    </defs>
    <rect width="${D.canvas}" height="${D.canvas}" fill="${C.bg}"/>
    <g id="dyn"></g>
    <g>${s}</g>
  </svg>`;
}

// Dynamic half: connectors, advanced flags and the centre, for a given state.
function dynSvg(t) {
  let s = '';
  for (const p of t.gray) s += poly(p, C.line, 1.6);
  for (const [x, y] of t.dots) {
    const [px, py] = toPx(x, y);
    s += `<circle cx="${px.toFixed(2)}" cy="${py.toFixed(2)}" r="3.2" fill="${C.line}"/>`;
  }
  for (const p of t.white) s += poly(p, C.white, 3.0);
  for (const [x, y] of t.wdots) {
    const [px, py] = toPx(x, y);
    s += `<circle cx="${px.toFixed(2)}" cy="${py.toFixed(2)}" r="4" fill="${C.white}"/>`;
  }
  for (const f of t.flags) s += flagUse(f.x, f.y, f.code, D.flagD, t.eliminated.has(f.code));

  // the logo always holds the centre; the champion's flag is laid over the cup
  const [ox, oy] = toPx(-D.logoW / 2, D.logoH / 2);
  s += `<image x="${ox.toFixed(2)}" y="${oy.toFixed(2)}" width="${(D.logoW * S).toFixed(2)}"
    height="${(D.logoH * S).toFixed(2)}" preserveAspectRatio="xMidYMid meet" href="${D.logo}"/>`;

  if (t.champion) {
    const [cx, cy] = toPx(0, D.champFlagY), gr = D.champD * S / 2 * 1.16;
    s += `<circle cx="${cx.toFixed(2)}" cy="${cy.toFixed(2)}" r="${gr.toFixed(2)}"
      fill="${C.gold}" stroke="#8a6d1f" stroke-width="3"/>`;
    s += flagUse(0, D.champFlagY, t.champion, D.champD, false);
    const [tx, ty] = toPx(0, D.champTextY);
    s += `<text x="${tx.toFixed(1)}" y="${ty.toFixed(1)}" text-anchor="middle"
      dominant-baseline="hanging" font-size="${D.champNameFs.toFixed(1)}"
      fill="${C.gold}" font-weight="bold"
      font-family="Segoe UI, system-ui, sans-serif">${NAME[t.champion].toUpperCase()}</text>`;
    const [sx, sy] = toPx(0, D.champTextY + D.champSubDy);
    s += `<text x="${sx.toFixed(1)}" y="${sy.toFixed(1)}" text-anchor="middle"
      dominant-baseline="hanging" font-size="${D.champSubFs.toFixed(1)}"
      fill="${C.gold}" font-family="Segoe UI, system-ui, sans-serif">${D.champSubtitle}</text>`;
  }
  return s;
}

const NAME = {};
D.teams.forEach(t => NAME[t.code] = t.name.replace(/\n/g, ' '));

function buildResults() {
  let html = '', lastRound = null;
  D.matches.forEach((m, i) => {
    if (m.round !== lastRound) { html += `<div class="rnd-head">${m.round}</div>`; lastRound = m.round; }
    const side = c => `<div class="side ${m.winner === c ? 'win' : 'lose'}">
        <img src="${D.flags[c]}" alt=""><span>${NAME[c]}</span></div>`;
    html += `<div class="row future" data-i="${i}">
        <div>${side(m.a)}${side(m.b)}<div class="date">${m.date}</div></div>
        <div class="score">${m.score}</div>
      </div>`;
  });
  document.getElementById('results').innerHTML = html;
  document.querySelectorAll('.row').forEach(r =>
    r.addEventListener('click', () => select(+r.dataset.i)));
}

// Right-most column: info + video links for the selected match.
function vidLink(v, label) {
  return `<a class="vid" href="${v.url}" target="_blank" rel="noopener">
      <span class="yt"></span>
      <span class="vt"><b>${label}</b><span>${v.title || v.url}</span></span></a>`;
}

function renderDetails(i) {
  const el = document.getElementById('details');
  if (i < 0 || i >= D.matches.length) {
    el.innerHTML = `<div class="empty">Move the slider, or pick a match on the
      left, to see the score, venue and highlights here.</div>`;
    return;
  }
  const m = D.matches[i];
  const sc = /^(\d+)-(\d+)/.exec(m.score);      // leading FT/ET scoreline
  const ga = sc ? sc[1] : '', gb = sc ? sc[2] : '';
  const team = (c, g) => `<div class="d-team ${m.winner === c ? 'win' : 'lose'}">
      <img src="${D.flags[c]}" alt=""><span class="nm">${NAME[c]}</span>
      <span class="gl">${g}</span></div>`;

  // who scored, and when
  const goalRow = g => `<div class="goal">
      <img src="${D.flags[g.code]}" alt="">
      <span class="min">${g.min}'</span>
      <span class="nm2">${g.name}${g.og ? ' <span class="tag">(o.g.)</span>'
        : g.pen ? ' <span class="tag">(pen.)</span>' : ''}</span></div>`;
  const goalsBlock = m.goals.length
    ? `<div class="goals">${m.goals.map(goalRow).join('')}</div>`
    : `<div class="no-goals">No goals in normal or extra time.</div>`;

  // link out to the highlights on YouTube (FOX Sports disables embedding)
  const v = m.video || {};
  const links = [];
  if (v.hl) links.push(vidLink(v.hl, 'Highlights'));
  if (v.ext) links.push(vidLink(v.ext, 'Extended highlights'));
  const vidBlock = links.length
    ? `<div class="vids">${links.join('')}</div>`
    : `<div class="no-vid">No highlights video available for this match.</div>`;

  const meta = [`<b>Date:</b> ${m.date}`];
  if (m.ground) meta.push(`<b>Stadium:</b> ${m.ground}`);
  el.innerHTML = `
    <div class="d-round">${m.round}</div>
    ${team(m.a, ga)}
    <div class="d-vs">vs</div>
    ${team(m.b, gb)}
    <div class="d-meta">${meta.join('<br>')}</div>
    <div class="sec-h">Goals</div>
    ${goalsBlock}
    <div class="sec-h">Highlights</div>
    ${vidBlock}
    ${D.sourceChannel ? `<div class="src">Video via
      <a href="${D.sourceChannel}" target="_blank" rel="noopener"
      >${D.sourceChannel.replace('https://www.youtube.com/', '')}</a></div>` : ''}`;
}

const stage = document.getElementById('stage');
const slider = document.getElementById('slider');
const rows = () => document.querySelectorAll('.row');
let selected = -1;

// Select a match for the details column (independent of the slider position),
// so an earlier game's highlights can be pulled up without rewinding the graph.
function select(i) {
  selected = i;
  rows().forEach((r, k) => r.classList.toggle('selected', k === i));
  renderDetails(i);
}

function setN(n) {
  n = Math.max(0, Math.min(D.matches.length, n));
  slider.value = n;
  document.getElementById('n').textContent = n;
  document.getElementById('dyn').innerHTML = dynSvg(buildTree(n));
  rows().forEach((r, i) => {
    r.classList.toggle('future', i >= n);
    r.classList.toggle('latest', i === n - 1);
  });
  const cur = document.querySelector('.row.latest');
  if (cur) cur.scrollIntoView({ block: 'nearest' });
  select(n - 1);          // details follow the most recently played match
}

stage.innerHTML = staticSvg();
buildResults();
slider.max = D.matches.length;
document.getElementById('total').textContent = '/ ' + D.matches.length + ' games played';
slider.addEventListener('input', () => setN(+slider.value));

let timer = null;
document.getElementById('play').addEventListener('click', e => {
  if (timer) { clearInterval(timer); timer = null; e.target.textContent = 'Play'; return; }
  if (+slider.value >= D.matches.length) setN(0);
  e.target.textContent = 'Pause';
  timer = setInterval(() => {
    if (+slider.value >= D.matches.length) {
      clearInterval(timer); timer = null;
      document.getElementById('play').textContent = 'Play';
    } else setN(+slider.value + 1);
  }, 600);
});

setN(D.matches.length);
</script>
</body>
</html>
"""


def main(out_path=None):
    out_path = out_path or OUT_PATH
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    payload = build_payload()
    html = HTML.replace("/*__DATA__*/", json.dumps(payload))
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"Saved interactive bracket to {out_path} ({len(html) / 1e6:.1f} MB)")


if __name__ == "__main__":
    import sys
    main(sys.argv[1] if len(sys.argv) > 1 else None)
