"""
FIFA World Cup 2026 - Circular Knockout Bracket (v3)

Renders the 32-team knockout bracket (Round of 32 -> R16 -> QF -> SF ->
Final -> champion) as a circular graphic, saved to wc2026_bracket.png.
The 32 teams sit on the outer ring, clockwise from 12 o'clock, with each
adjacent pair (0,1) (2,3) ... forming a Round-of-32 matchup; winners merge
inward round by round toward the champion at the centre.

VISUALS
  - Leaf markers are real circular flag images (from flags_png/<code>.png,
    e.g. flags_png/fr.png), masked to a circle with a thin white ring.
  - Federation crest badges are loaded from
    crests_svg/<country_name>_crest.svg (for example, Brazil_crest.svg).
    With no file present, a generated federation-acronym badge is used.
  - Connectors are "elbow" bracket lines: a radial segment from each node
    inward to a fixed radius, then an arc bridging the two paired nodes.
  - Flags and crests share one uniform size across every ring.

RESULTS
  - Completed knockout results are read from worldcup.json and matched to
    bracket pairings by team name. Run download_resources.py to refresh it.
  - Each decided match draws the winner's path as a bold WHITE line, a
    white dot at the winner's elbow corner, and advances the winner's flag
    inward onto the next ring.  A team's flag MOVES forward as it wins
    (only its furthest position is shown); passed-through nodes become
    white dots.  The champion's flag is placed on the centre.
  - Undecided ties stay grey.  Eliminated teams are shown in greyscale
    (flag + crest) everywhere they appear.

USAGE
    python WC26_Brackets.py [--simulate] [--seed=N] [--no-grey]

OPTIONS
  --simulate   Keep real results but resolve every still-undecided match at
               random, filling the bracket all the way to a champion.
  --seed=N     Seed the RNG so a --simulate run is reproducible.
  --no-grey    Disable greyscale for eliminated teams (render them in full
               colour).  Greyscale is on by default.
"""

import os
import io
import re
import sys
import base64
import random
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.offsetbox import OffsetImage, AnnotationBbox
from matplotlib.patches import Ellipse
from PIL import Image, ImageDraw
import resvg_py

from download_resources import (
    crest_filename,
    download_worldcup_json,
    extract_knockout_winners,
    round_of_32_teams,
)


def print_usage():
    """Print how to run the script, then let the caller exit."""
    prog = os.path.basename(sys.argv[0]) or "WC26_Brackets.py"
    print(f"""FIFA World Cup 2026 - Circular Knockout Bracket

Draws the 32-team knockout bracket to wc2026_bracket.png using completed
knockout results from worldcup.json. Winners advance inward toward the
champion at the centre; eliminated teams are greyed out.

Usage:
    python {prog} [--simulate] [--seed=N] [--no-grey] [-h|--help]

Options:
    --simulate   Keep the real results, then resolve every still-undecided
                 match at random so the bracket fills all the way to a champion.
    --seed=N     Seed the random generator so a --simulate run is reproducible.
    --no-grey    Render eliminated teams in full colour (by default knocked-out
                 teams are shown in greyscale).
    -h, --help   Show this message and exit.

Examples:
    python {prog}                       # live bracket, greyed-out losers
    python {prog} --simulate --seed=7   # reproducible full simulation
    python {prog} --no-grey             # keep every flag in colour
""")


if "--help" in sys.argv or "-h" in sys.argv:
    print_usage()
    sys.exit(0)


# ----------------------------------------------------------------------
# 1. TEAM DATA  (name, flag-icons code, federation acronym)
#    Order = clockwise around the circle, starting at 12 o'clock.
#    Adjacent pairs (0,1) (2,3) (4,5) ... are Round-of-32 matchups.
# ----------------------------------------------------------------------
teams = [
    ("Brazil",       "br",     "CBF"),
    ("Japan",        "jp",     "JFA"),
    ("Ivory Coast",  "ci",     "FIF"),
    ("Norway",       "no",     "NFF"),
    ("Mexico",       "mx",     "FMF"),
    ("Ecuador",      "ec",     "FEF"),
    ("England",      "gb-eng", "FA"),
    ("Democratic\nRepublic\nof Congo",     "cd",     "FECOFA"),
    ("Argentina",    "ar",     "AFA"),
    ("Cape Verde",   "cv",     "FCF"),
    ("Australia",    "au",     "FA"),
    ("Egypt",        "eg",     "EFA"),
    ("Switzerland",  "ch",     "SFV"),
    ("Algeria",      "dz",     "FAF"),
    ("Colombia",     "co",     "FCF"),
    ("Ghana",        "gh",     "GFA"),
    ("Senegal",      "sn",     "FSF"),
    ("Belgium",      "be",     "RBFA"),
    ("Bosnia and\nHerzegovina",       "ba",     "NFSBIH"),
    ("United States\nof America",          "us",     "USSF"),
    ("Austria",      "at",     "OFB"),
    ("Spain",        "es",     "RFEF"),
    ("Croatia",      "hr",     "HNS"),
    ("Portugal",     "pt",     "FPF"),
    ("Morocco",      "ma",     "FRMF"),
    ("Netherlands",  "nl",     "KNVB"),
    ("Canada",       "ca",     "CSA"),
    ("South Africa", "za",     "SAFA"),
    ("Sweden",       "se",     "SVFF"),
    ("France",       "fr",     "FFF"),
    ("Paraguay",     "py",     "APF"),
    ("Germany",      "de",     "DFB"),
]
assert len(teams) == 32

N_TEAMS = len(teams)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
FLAG_DIR = os.path.join(SCRIPT_DIR, "flags_png")
FLAG_SVG_DIR = os.path.join(SCRIPT_DIR, "flags_svg")   # vector flags, for SVG output
CREST_DIR = os.path.join(SCRIPT_DIR, "crests_svg")  # optional real crests, see docstring
LOGO_PATH = os.path.join(SCRIPT_DIR, "2026_FIFA_World_Cup_logo.svg")

# ----------------------------------------------------------------------
# 2. GEOMETRY HELPERS
# ----------------------------------------------------------------------
ANGLE_STEP_DEG = 360 / N_TEAMS
START_ANGLE_DEG = 90 - ANGLE_STEP_DEG / 2
RADII = [1.00, 0.82, 0.64, 0.46, 0.28, 0.0]   # leaves -> R16 -> QF -> SF -> F -> champion


def leaf_angle(i):
    return np.deg2rad(START_ANGLE_DEG - i * ANGLE_STEP_DEG)


def polar_to_xy(r, theta):
    return r * np.cos(theta), r * np.sin(theta)


def arc_points(r, theta_a, theta_b, n=40):
    """Points along a circular arc of radius r between two angles."""
    d = theta_b - theta_a
    d = (d + np.pi) % (2 * np.pi) - np.pi
    thetas = theta_a + np.linspace(0, d, n)
    xs = r * np.cos(thetas)
    ys = r * np.sin(thetas)
    return xs, ys


def circular_image(path, size_px=240):
    """Load an image and mask it into a circle with a thin white ring."""
    im = Image.open(path).convert("RGBA").resize((size_px, size_px), Image.LANCZOS)
    mask = Image.new("L", (size_px, size_px), 0)
    draw = ImageDraw.Draw(mask)
    pad = 3
    draw.ellipse((pad, pad, size_px - pad, size_px - pad), fill=255)
    out = Image.new("RGBA", (size_px, size_px), (0, 0, 0, 0))
    out.paste(im, (0, 0), mask)
    d2 = ImageDraw.Draw(out)
    d2.ellipse((pad, pad, size_px - pad, size_px - pad), outline=(255, 255, 255, 255), width=6)
    return out


def load_svg_image(path, width_px=800):
    """Rasterize an SVG to a transparent-background RGBA image."""
    # resvg (not cairosvg): the 2026 logo masks an embedded raster trophy with
    # a luminance mask, which cairosvg renders wrong (see-through trophy + grey
    # band).  resvg applies the mask correctly.
    png_bytes = bytes(resvg_py.svg_to_bytes(svg_path=path, width=width_px))
    return Image.open(io.BytesIO(png_bytes)).convert("RGBA")


def to_greyscale(im):
    """Desaturate an RGBA image while preserving its alpha (used for teams
    that have been knocked out).  The white ring stays white."""
    im = im.convert("RGBA")
    r, g, b, a = im.split()
    grey = Image.merge("RGB", (r, g, b)).convert("L").convert("RGBA")
    grey.putalpha(a)
    return grey


def pad_to_square(im, size=260):
    """Center an (arbitrary aspect ratio) image on a transparent square
    canvas so different crest shapes render at a consistent visual size."""
    im = im.convert("RGBA")
    scale = min(size / im.width, size / im.height) * 0.92
    new_w, new_h = max(1, int(im.width * scale)), max(1, int(im.height * scale))
    im = im.resize((new_w, new_h), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(im, ((size - new_w) // 2, (size - new_h) // 2), im)
    return canvas


def load_logo_image(country_name, acronym, size=260):
    """Federation crest loader. Tries, in order:
       crests_svg/<country_name>_crest.svg
       falls back to the generated shield badge."""
    svg_path = os.path.join(CREST_DIR, crest_filename(country_name))
    if os.path.exists(svg_path):
        png_bytes = bytes(resvg_py.svg_to_bytes(svg_path=svg_path, width=size * 2))
        im = Image.open(io.BytesIO(png_bytes))
        return pad_to_square(im, size)
    else:
        return shield_badge(acronym, size)


def shield_badge(text, size_px=260, fill=(30, 30, 34, 255)):
    """Fallback badge (shield outline + acronym) used when no real crest
    image is available for a federation."""
    im = Image.new("RGBA", (size_px, size_px), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    w, h = size_px, size_px
    pts = [
        (w * 0.10, h * 0.08), (w * 0.90, h * 0.08),
        (w * 0.90, h * 0.55), (w * 0.50, h * 0.95),
        (w * 0.10, h * 0.55),
    ]
    d.polygon(pts, fill=fill, outline=(230, 195, 92, 255), width=8)
    try:
        from PIL import ImageFont
        font = ImageFont.load_default()
    except Exception:
        font = None
    bbox = d.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    d.text((w / 2 - tw / 2, h * 0.42 - th / 2), text, fill="white", font=font)
    return im


# ----------------------------------------------------------------------
# 2b. RESULTS  (FIFA World Cup 2026, from worldcup.json)
# ----------------------------------------------------------------------
# Map a team name from worldcup.json to our internal flag-code.
_CODE_BY_NAME = {name.lower(): code for name, code, _ in teams}
_CODE_BY_NAME.update({
    "united states": "us", "usa": "us",
    "cote d'ivoire": "ci", "côte d'ivoire": "ci", "ivory coast": "ci",
    "congo dr": "cd", "dr congo": "cd",
    "democratic republic of the congo": "cd",
    "bosnia and herzegovina": "ba", "bosnia & herzegovina": "ba",
    "cabo verde": "cv", "cape verde": "cv",
    "england": "gb-eng",
})

WORLDCUP_PATH = os.path.join(SCRIPT_DIR, "worldcup.json")


def refresh_worldcup_data():
    """Refresh worldcup.json, falling back to the local copy if necessary."""
    try:
        path = download_worldcup_json(WORLDCUP_PATH)
        print(f"Refreshed World Cup data from GitHub: {path}")
    except RuntimeError as exc:
        if not os.path.exists(WORLDCUP_PATH):
            raise
        print(f"[warn] World Cup data refresh failed ({exc}); using local copy.")


refresh_worldcup_data()


def _resolve_code(team_name):
    return _CODE_BY_NAME.get(str(team_name).lower())


_COUNTRY_BY_CODE = {
    _resolve_code(team_name): team_name
    for team_name in round_of_32_teams(WORLDCUP_PATH)
    if _resolve_code(team_name)
}


def fetch_winners():
    """Return graph-ready winners for completed local JSON matches."""
    try:
        named_winners = extract_knockout_winners(WORLDCUP_PATH)
    except Exception as exc:
        print(f"[warn] could not load knockout results ({exc})")
        return {}

    winners = {}
    for matchup, winner_name in named_winners.items():
        codes = {_resolve_code(team_name) for team_name in matchup}
        winner_code = _resolve_code(winner_name)
        if None not in codes and len(codes) == 2 and winner_code:
            winners[frozenset(codes)] = winner_code
    return winners


WINNERS = fetch_winners()
print(f"Loaded {len(WINNERS)} completed knockout result(s).")

# Simulation mode: keep real results, but randomly resolve every match that
# is still undecided, so the bracket fills all the way to a champion.
#   python WC26_Brackets.py --simulate [--seed=N]
SIMULATE = "--simulate" in sys.argv
for _a in sys.argv:
    if _a.startswith("--seed="):
        random.seed(int(_a.split("=", 1)[1]))
if SIMULATE:
    print("Simulation mode ON: undecided matches resolved at random.")

# Option: render knocked-out teams (flag + crest) in greyscale.  On by
# default; disable with --no-grey.
GREY_ELIMINATED = "--no-grey" not in sys.argv


# ----------------------------------------------------------------------
# 3. BUILD TREE + ELBOW CONNECTOR SEGMENTS
# ----------------------------------------------------------------------
leaf_angles = [leaf_angle(i) for i in range(N_TEAMS)]
# each node carries the code of the team occupying it (None if undecided)
current_round = [(RADII[0], a, teams[i][1]) for i, a in enumerate(leaf_angles)]

gray_lines = []       # (xs, ys) default connector segments
white_lines = []      # (xs, ys) winning-team progression segments
node_dots = []        # (x, y) elbow-corner dots
white_dots = []       # (x, y) merge node a winner advanced from
winner_flags = []     # (x, y, code, round_i) flag of team that advanced
eliminated = set()    # codes of teams that have lost and are out
champion_code = None  # winner of the final, drawn on the centre

round_i = 0
while len(current_round) > 1:
    next_round = []
    r_here = current_round[0][0]
    r_next = RADII[round_i + 1]
    for i in range(0, len(current_round), 2):
        _, a_a, code_a = current_round[i]
        _, a_b, code_b = current_round[i + 1]

        mid_angle = a_a + (((a_b - a_a) + np.pi) % (2 * np.pi) - np.pi) / 2

        # a match is decided if both teams are known and a result exists;
        # in simulation mode an undecided match is resolved at random
        win_code = None
        if code_a and code_b:
            win_code = WINNERS.get(frozenset({code_a, code_b}))
            if win_code is None and SIMULATE:
                win_code = random.choice([code_a, code_b])
            if win_code:
                eliminated.add(code_b if win_code == code_a else code_a)

        # each child = a radial (child -> r_next) + its half of the arc
        # (child -> mid).  The winner's two segments are drawn white.
        for a_child, code_child in ((a_a, code_a), (a_b, code_b)):
            cx, cy = polar_to_xy(r_here, a_child)
            nx, ny = polar_to_xy(r_next, a_child)
            radial = ([cx, nx], [cy, ny])
            ax_, ay_ = arc_points(r_next, a_child, mid_angle)
            arc = (ax_, ay_)
            if win_code and code_child == win_code:
                white_lines.extend((radial, arc))
            else:
                gray_lines.extend((radial, arc))

        # dots sit at the elbow corners (both endpoints of the bridging arc)
        if r_next > 0:
            node_dots.append(polar_to_xy(r_next, a_a))
            node_dots.append(polar_to_xy(r_next, a_b))

        if win_code and r_next > 0:
            # WHITE dot at the winner's elbow corner (where its line turns in)
            a_win = a_a if win_code == code_a else a_b
            white_dots.append(polar_to_xy(r_next, a_win))
            # advance the flag inward onto the next circular path; finalists
            # (no inner ring left) simply sit on their merge node
            mx, my = polar_to_xy(r_next, mid_angle)
            if round_i + 2 <= len(RADII) - 2:
                fx, fy = polar_to_xy(RADII[round_i + 2], mid_angle)
                white_lines.append(([mx, fx], [my, fy]))
            else:
                fx, fy = mx, my
            winner_flags.append((fx, fy, win_code, round_i))
        elif win_code:
            # final resolved -> this team is the champion (drawn on the centre)
            champion_code = win_code

        next_round.append((r_next, mid_angle, win_code))
    current_round = next_round
    round_i += 1

# A team's flag MOVES inward as it wins rather than replicating every round:
# keep only each team's furthest (deepest) flag.  The vacated positions are
# already marked by white dots from the following round's elbow corner.  The
# champion is shown on the centre, so its flag is dropped here (and its final
# node left as a white dot).
_deepest = {}
for _e in winner_flags:
    _code, _rnd = _e[2], _e[3]
    if _code not in _deepest or _rnd > _deepest[_code][3]:
        _deepest[_code] = _e
winner_flags = []
for _e in _deepest.values():
    if _e[2] == champion_code:
        white_dots.append((_e[0], _e[1]))
    else:
        winner_flags.append(_e)

# ----------------------------------------------------------------------
# 4. DRAW
# ----------------------------------------------------------------------
BG = "#0a0a0a"
LINE_COLOR = "#5a5a5a"
TEXT_COLOR = "#f2f2f2"
GOLD = "#e6c35c"

fig, ax = plt.subplots(figsize=(16, 16), facecolor=BG)
ax.set_facecolor(BG)

WHITE = "#ffffff"
for xs, ys in gray_lines:
    ax.plot(xs, ys, color=LINE_COLOR, lw=1.3, zorder=1, solid_capstyle="round")
for x, y in node_dots:
    ax.plot(x, y, "o", color=LINE_COLOR, ms=4.5, zorder=2)
# winning-team progression, drawn on top so it reads as a clear white path
for xs, ys in white_lines:
    ax.plot(xs, ys, color=WHITE, lw=2.4, zorder=2.5, solid_capstyle="round")
for x, y in white_dots:
    ax.plot(x, y, "o", color=WHITE, ms=5.5, zorder=2.7)

FLAG_PX = 240           # base raster size for every flag
FLAG_ZOOM = 0.155       # one flag size, shared by outer ring and inner rings
CREST_ZOOM = 0.190      # federation crests, a touch larger than the flags

# Champion badge: the winner's flag sits over the trophy on the centre logo,
# matching the beaten finalist's flag exactly -- same size, and level with it
# on the horizontal centre line (the finalist node sits at y = 0).
CHAMP_ZOOM = FLAG_ZOOM  # same size as every other flag
CHAMP_FLAG_Y = 0.0      # same height as the losing finalist
CHAMP_TEXT_Y = -0.235   # label sits clear of the logo's lower edge
CHAMP_SUB_DY = -0.052   # subtitle offset below the nation's name
CHAMP_NAME_FS = 18      # nation: large and bold
CHAMP_SUB_FS = 12        # subtitle: small and regular weight
CHAMP_SUBTITLE = "World Cup Champions"

_NAME_BY_CODE = {code: name for name, code, _ in teams}

for i, (name, code, acronym) in enumerate(teams):
    theta = leaf_angles[i]
    x, y = polar_to_xy(RADII[0], theta)

    # Outer-ring leaf flags/crests are always shown in full colour; only the
    # inner (advanced) flags closest to the centre are greyed when eliminated.
    flag_path = os.path.join(FLAG_DIR, f"{code}.png")
    flag_im = circular_image(flag_path, FLAG_PX)
    ab = AnnotationBbox(OffsetImage(flag_im, zoom=FLAG_ZOOM), (x, y),
                         frameon=False, zorder=3, pad=0)
    ax.add_artist(ab)

    lx, ly = polar_to_xy(RADII[0] + 0.175, theta)
    crest_im = load_logo_image(_COUNTRY_BY_CODE.get(code, name), acronym)
    ab2 = AnnotationBbox(OffsetImage(crest_im, zoom=CREST_ZOOM), (lx, ly),
                          frameon=False, zorder=3, pad=0)
    ax.add_artist(ab2)

    tx, ty = polar_to_xy(RADII[0] + 0.30, theta)
    rot_deg = np.degrees(theta) % 360
    ha = "left"
    if 90 < rot_deg < 270:
        rot_deg += 180
        ha = "right"
    ax.text(tx, ty, name, rotation=rot_deg, rotation_mode="anchor",
            ha=ha, va="center", fontsize=9, color=TEXT_COLOR, zorder=4)

# flag of each team that has advanced, placed on the node it progressed to
# (same size as the outer-ring flags, for a uniform look)
for wx, wy, code, rnd in winner_flags:
    adv_im = circular_image(os.path.join(FLAG_DIR, f"{code}.png"), FLAG_PX)
    if GREY_ELIMINATED and code in eliminated:
        adv_im = to_greyscale(adv_im)
    ax.add_artist(AnnotationBbox(OffsetImage(adv_im, zoom=FLAG_ZOOM),
                                 (wx, wy), frameon=False, zorder=5, pad=0))

# The tournament logo always holds the centre.  Once the final is decided the
# champion's flag is laid over the trophy, with the winning nation named below.
logo_im = load_svg_image(LOGO_PATH, 800)
ax.add_artist(AnnotationBbox(OffsetImage(logo_im, zoom=0.11), (0, 0),
                             frameon=False, zorder=6, pad=0))

if champion_code:
    champ_name = _NAME_BY_CODE.get(champion_code, champion_code).replace("\n", " ")
    champ_im = circular_image(os.path.join(FLAG_DIR, f"{champion_code}.png"), FLAG_PX)
    # gold halo, then the flag over the cup
    ax.scatter([0], [CHAMP_FLAG_Y], s=(FLAG_PX * CHAMP_ZOOM * 1.16) ** 2, marker="o",
               color=GOLD, edgecolor="#8a6d1f", linewidth=2, zorder=6.5)
    ax.add_artist(AnnotationBbox(OffsetImage(champ_im, zoom=CHAMP_ZOOM),
                                 (0, CHAMP_FLAG_Y), frameon=False, zorder=7, pad=0))
    ax.text(0, CHAMP_TEXT_Y, champ_name.upper(), ha="center", va="top",
            fontsize=CHAMP_NAME_FS, color=GOLD, fontweight="bold", zorder=7)
    ax.text(0, CHAMP_TEXT_Y + CHAMP_SUB_DY, CHAMP_SUBTITLE, ha="center", va="top",
            fontsize=CHAMP_SUB_FS, color=GOLD, zorder=7)

ax.set_xlim(-1.6, 1.6)
ax.set_ylim(-1.6, 1.6)
ax.set_aspect("equal")
ax.axis("off")

plt.tight_layout()
out_path = os.path.join(SCRIPT_DIR, "wc2026_bracket.png")
plt.savefig(out_path, dpi=220, facecolor=fig.get_facecolor(), bbox_inches="tight")
print(f"Saved bracket to {out_path}")

# ----------------------------------------------------------------------
# 5. VECTOR SVG EXPORT
# ----------------------------------------------------------------------
# A fully self-contained, resolution-independent SVG built directly (not via
# matplotlib, which rasterizes every image).  Lines, dots, circles and text are
# native SVG vectors.  Flags and crests are embedded as <image> elements that
# reference the source SVG through a data-URI, so they stay vector and each
# lives in its own document (no id collisions between flag files).  The trophy
# logo is embedded the same way; a browser/Inkscape renders its internal mask
# correctly (the earlier grey-band artefact was a cairosvg-only bug).
CANVAS = 1600           # output is CANVAS x CANVAS user units (scales freely)
VIEW = 1.6              # data half-extent mapped to the canvas (matches xlim)
S = CANVAS / (2 * VIEW)  # data units -> SVG user units
FS = 15                 # team-name font size, in SVG user units

# Match matplotlib's on-figure image sizes exactly.  Every flag/crest/logo is
# an OffsetImage whose on-figure size is  native_px * zoom * (dpi/72)  display
# pixels; convert that length through the axes transform to get the equivalent
# size in data units, so the SVG dimensions are identical to the PNG.
fig.canvas.draw()
_DPI_COR = fig.dpi / 72.0
_inv = ax.transData.inverted()


def _disp_len_to_data(px):
    x0 = _inv.transform((0.0, 0.0))[0]
    x1 = _inv.transform((px, 0.0))[0]
    return abs(x1 - x0)


FLAG_D = _disp_len_to_data(FLAG_PX * FLAG_ZOOM * _DPI_COR)          # flag diameter
CREST_D = _disp_len_to_data(260 * CREST_ZOOM * _DPI_COR)           # crest box (load_logo_image size=260)
CHAMP_D = _disp_len_to_data(FLAG_PX * CHAMP_ZOOM * _DPI_COR)       # champion flag


def _to_px(x, y):
    """Data coords (y up) -> SVG user coords (y down)."""
    return CANVAS / 2 + x * S, CANVAS / 2 - y * S


def _data_uri(path):
    with open(path, "rb") as fh:
        raw = fh.read()
    return "data:image/svg+xml;base64," + base64.b64encode(raw).decode("ascii")


def _polyline(xs, ys, color, width):
    pts = " ".join(f"{_to_px(x, y)[0]:.2f},{_to_px(x, y)[1]:.2f}"
                   for x, y in zip(xs, ys))
    return (f'<polyline points="{pts}" fill="none" stroke="{color}" '
            f'stroke-width="{width}" stroke-linecap="round" '
            f'stroke-linejoin="round"/>')


def _flag_image(x, y, code, diameter, grey=False):
    """Circular, white-ringed flag as a vector <image> at data point (x, y)."""
    uri = _data_uri(os.path.join(FLAG_SVG_DIR, f"{code}.svg"))
    cx, cy = _to_px(x, y)
    r = diameter * S / 2
    filt = ' filter="url(#greyscale)"' if grey else ""
    ring_w = max(1.4, r * 0.055)
    return (
        f'<image x="{cx - r:.2f}" y="{cy - r:.2f}" width="{2 * r:.2f}" '
        f'height="{2 * r:.2f}" preserveAspectRatio="xMidYMid slice" '
        f'clip-path="url(#circClip)" xlink:href="{uri}"{filt}/>'
        f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}" fill="none" '
        f'stroke="#ffffff" stroke-width="{ring_w:.2f}"/>'
    )


def _svg_aspect(path):
    """Intrinsic width/height ratio of an SVG (viewBox first, else width/height;
    some crests, e.g. Portugal, have width/height but no viewBox)."""
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


def _crest_image(x, y, country_name, acronym, size):
    """Federation crest as a vector <image>, or a drawn shield fallback.

    Mirrors pad_to_square: the crest keeps its aspect ratio and fills 0.92 of
    the box along its longer side, centred (so odd-aspect crests such as
    Portugal's match the PNG instead of being stretched to a full square)."""
    svg_path_ = os.path.join(CREST_DIR, crest_filename(country_name))
    cx, cy = _to_px(x, y)
    half = size * S / 2
    if os.path.exists(svg_path_):
        uri = _data_uri(svg_path_)
        box = size * 0.92 * S            # pad_to_square's 0.92 scale factor
        aspect = _svg_aspect(svg_path_)
        if aspect >= 1:
            w, h = box, box / aspect
        else:
            w, h = box * aspect, box
        return (f'<image x="{cx - w / 2:.2f}" y="{cy - h / 2:.2f}" '
                f'width="{w:.2f}" height="{h:.2f}" '
                f'preserveAspectRatio="xMidYMid meet" xlink:href="{uri}"/>')
    # vector shield fallback (mirrors shield_badge)
    w = 2 * half
    pts = [(-0.40, 0.42), (0.40, 0.42), (0.40, -0.05),
           (0.0, -0.45), (-0.40, -0.05)]
    pstr = " ".join(f"{cx + px * w:.1f},{cy - py * w:.1f}" for px, py in pts)
    return (f'<polygon points="{pstr}" fill="#1e1e22" stroke="#e6c35c" '
            f'stroke-width="{w * 0.03:.1f}"/>'
            f'<text x="{cx:.1f}" y="{cy:.1f}" text-anchor="middle" '
            f'dominant-baseline="middle" font-size="{w * 0.22:.1f}" '
            f'fill="#ffffff" font-family="Arial, sans-serif">{acronym}</text>')


def _name_text(x, y, name, theta):
    cx, cy = _to_px(x, y)
    rot_deg = np.degrees(theta) % 360
    anchor = "start"
    if 90 < rot_deg < 270:
        rot_deg += 180
        anchor = "end"
    lines = name.split("\n")
    lh = FS * 1.05
    first = -(len(lines) - 1) / 2 * lh
    spans = "".join(
        f'<tspan x="{cx:.1f}" dy="{(first if i == 0 else lh):.1f}">{ln}</tspan>'
        for i, ln in enumerate(lines))
    return (f'<text transform="rotate({-rot_deg:.2f} {cx:.1f} {cy:.1f})" '
            f'x="{cx:.1f}" y="{cy:.1f}" text-anchor="{anchor}" '
            f'dominant-baseline="middle" font-size="{FS}" fill="{TEXT_COLOR}" '
            f'font-family="DejaVu Sans, Arial, sans-serif">{spans}</text>')


parts = [
    f'<svg xmlns="http://www.w3.org/2000/svg" '
    f'xmlns:xlink="http://www.w3.org/1999/xlink" '
    f'viewBox="0 0 {CANVAS} {CANVAS}" width="{CANVAS}" height="{CANVAS}">',
    '<defs>',
    '<clipPath id="circClip" clipPathUnits="objectBoundingBox">'
    '<circle cx="0.5" cy="0.5" r="0.5"/></clipPath>',
    '<filter id="greyscale"><feColorMatrix type="saturate" values="0"/></filter>',
    '</defs>',
    f'<rect x="0" y="0" width="{CANVAS}" height="{CANVAS}" fill="{BG}"/>',
]

# connector geometry (grey base, then white winning path on top)
for xs, ys in gray_lines:
    parts.append(_polyline(xs, ys, LINE_COLOR, 1.6))
for x, y in node_dots:
    px, py = _to_px(x, y)
    parts.append(f'<circle cx="{px:.2f}" cy="{py:.2f}" r="3.2" fill="{LINE_COLOR}"/>')
for xs, ys in white_lines:
    parts.append(_polyline(xs, ys, WHITE, 3.0))
for x, y in white_dots:
    px, py = _to_px(x, y)
    parts.append(f'<circle cx="{px:.2f}" cy="{py:.2f}" r="4.0" fill="{WHITE}"/>')

# outer ring: flag + crest + name (always full colour)
for i, (name, code, acronym) in enumerate(teams):
    theta = leaf_angles[i]
    fx, fy = polar_to_xy(RADII[0], theta)
    parts.append(_flag_image(fx, fy, code, FLAG_D))
    lx, ly = polar_to_xy(RADII[0] + 0.175, theta)
    parts.append(_crest_image(lx, ly, _COUNTRY_BY_CODE.get(code, name), acronym, CREST_D))
    tx, ty = polar_to_xy(RADII[0] + 0.30, theta)
    parts.append(_name_text(tx, ty, name, theta))

# inner (advanced) flags, greyed when eliminated
for wx, wy, code, rnd in winner_flags:
    parts.append(_flag_image(wx, wy, code, FLAG_D,
                             grey=GREY_ELIMINATED and code in eliminated))

# centre: the tournament logo always, with the champion's flag laid over the
# cup (and the winning nation named) once the final is decided
logo_uri = _data_uri(LOGO_PATH)
# match matplotlib: logo_im is 'logo_im.size' px, drawn at zoom 0.11
logo_w = _disp_len_to_data(logo_im.size[0] * 0.11 * _DPI_COR)
logo_h = _disp_len_to_data(logo_im.size[1] * 0.11 * _DPI_COR)
ox, oy = _to_px(-logo_w / 2, logo_h / 2)
parts.append(f'<image x="{ox:.2f}" y="{oy:.2f}" width="{logo_w * S:.2f}" '
             f'height="{logo_h * S:.2f}" preserveAspectRatio="xMidYMid meet" '
             f'xlink:href="{logo_uri}"/>')

if champion_code:
    cpx, cpy = _to_px(0, CHAMP_FLAG_Y)
    gold_r = CHAMP_D * S / 2 * 1.16
    parts.append(f'<circle cx="{cpx:.2f}" cy="{cpy:.2f}" r="{gold_r:.2f}" '
                 f'fill="{GOLD}" stroke="#8a6d1f" stroke-width="3"/>')
    parts.append(_flag_image(0, CHAMP_FLAG_Y, champion_code, CHAMP_D))
    champ_name = _NAME_BY_CODE.get(champion_code, champion_code).replace("\n", " ")
    # matplotlib point sizes -> SVG user units (team names: 9 pt drawn at FS)
    _pt = FS / 9.0
    ctx, cty = _to_px(0, CHAMP_TEXT_Y)
    parts.append(f'<text x="{ctx:.1f}" y="{cty:.1f}" text-anchor="middle" '
                 f'dominant-baseline="hanging" font-size="{CHAMP_NAME_FS * _pt:.1f}" '
                 f'fill="{GOLD}" font-weight="bold" '
                 f'font-family="DejaVu Sans, Arial, sans-serif">'
                 f'{champ_name.upper()}</text>')
    stx, sty = _to_px(0, CHAMP_TEXT_Y + CHAMP_SUB_DY)
    parts.append(f'<text x="{stx:.1f}" y="{sty:.1f}" text-anchor="middle" '
                 f'dominant-baseline="hanging" font-size="{CHAMP_SUB_FS * _pt:.1f}" '
                 f'fill="{GOLD}" font-family="DejaVu Sans, Arial, sans-serif">'
                 f'{CHAMP_SUBTITLE}</text>')

parts.append('</svg>')

svg_path = os.path.join(SCRIPT_DIR, "wc2026_bracket.svg")
with open(svg_path, "w", encoding="utf-8") as fh:
    fh.write("\n".join(parts))
print(f"Saved bracket to {svg_path}")
