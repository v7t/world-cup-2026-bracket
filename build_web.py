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


def collect_matches():
    """Decided knockout matches, in chronological order."""
    with open(WORLDCUP_PATH, encoding="utf-8") as fh:
        data = json.load(fh)
    rows = []
    for m in data["matches"]:
        rnd = m.get("round")
        if rnd not in KNOCKOUT_ROUNDS:
            continue
        a, b = resolve_code(m.get("team1")), resolve_code(m.get("team2"))
        wi = winner_index(m.get("score"))
        if not a or not b or wi is None:
            continue                      # undecided, or a "W73"-style placeholder
        rows.append({
            "round": ROUND_LABEL.get(rnd, rnd),
            "date": m.get("date", ""),
            "a": a, "b": b,
            "winner": (a, b)[wi],
            "score": score_text(m.get("score")),
            "num": m.get("num") or 0,
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
    print(f"Loaded {len(matches)} decided knockout match(es).")

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
    width: 330px; flex-shrink: 0; border-left: 1px solid #242424;
    display: flex; flex-direction: column; min-height: 0;
  }
  .sidebar h2 {
    margin: 0; padding: 14px 16px 10px; font-size: 13px; letter-spacing: .12em;
    text-transform: uppercase; color: #9a9a9a; border-bottom: 1px solid #242424;
  }
  .results { overflow-y: auto; padding: 6px 8px 16px; flex: 1; }
  .rnd-head {
    font-size: 11px; letter-spacing: .14em; color: #e6c35c; padding: 12px 8px 5px;
    text-transform: uppercase;
  }
  .row {
    display: grid; grid-template-columns: 1fr auto; gap: 6px; align-items: center;
    padding: 7px 8px; border-radius: 6px; cursor: pointer; margin-bottom: 2px;
    border: 1px solid transparent;
  }
  .row:hover { background: #171717; }
  .row.future { opacity: .28; }
  .row.latest { background: #1c1c1c; border-color: #e6c35c; }
  .side { display: flex; align-items: center; gap: 7px; font-size: 13px; line-height: 1.25; }
  .side + .side { margin-top: 3px; }
  .side img { width: 19px; height: 19px; border-radius: 50%; object-fit: cover; flex-shrink: 0; }
  .side.win { font-weight: 600; color: #ffffff; }
  .side.lose { color: #7c7c7c; }
  .score { font-variant-numeric: tabular-nums; font-size: 12px; color: #b9b9b9; text-align: right; }
  .date { font-size: 10px; color: #6a6a6a; margin-top: 3px; }

  .footer { border-top: 1px solid #242424; padding: 12px 22px 16px; }
  .ctrl { display: flex; align-items: center; gap: 16px; }
  button {
    background: #1d1d1d; color: #f2f2f2; border: 1px solid #3a3a3a; border-radius: 6px;
    padding: 7px 16px; cursor: pointer; font-size: 13px; min-width: 74px;
  }
  button:hover { background: #292929; }
  input[type=range] { flex: 1; accent-color: #e6c35c; height: 22px; cursor: pointer; }
  .count { font-size: 13px; color: #b9b9b9; min-width: 132px; font-variant-numeric: tabular-nums; }
  .count b { color: #e6c35c; font-size: 15px; }
</style>
</head>
<body>
  <div class="main">
    <div class="stage" id="stage"></div>
    <aside class="sidebar">
      <h2>Results</h2>
      <div class="results" id="results"></div>
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
    r.addEventListener('click', () => setN(+r.dataset.i + 1)));
}

const stage = document.getElementById('stage');
const slider = document.getElementById('slider');
const rows = () => document.querySelectorAll('.row');

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
