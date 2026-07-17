"""Download data and federation crests used by the World Cup bracket."""

from __future__ import annotations

import argparse
import json
import re
import time
import unicodedata
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SCRIPT_DIR = Path(__file__).resolve().parent
WORLDCUP_JSON_URL = (
    "https://raw.githubusercontent.com/openfootball/worldcup.json/"
    "master/2026/worldcup.json"
)
NATIONAL_TEAMS_URL = "https://football-logos.cc/national-teams/"
USER_AGENT = "WorldCup2026-resource-downloader/1.0"
KNOCKOUT_ROUNDS = (
    "Round of 32",
    "Round of 16",
    "Quarter-final",
    "Semi-final",
    "Final",
)

_CREST_NAME_ALIASES = {
    "ivory coast": "cote d ivoire",
    "cape verde": "cabo verde",
    "dr congo": "congo dr",
}


class _CrestIndexParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.crests: dict[str, tuple[str, str]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "div":
            return

        attributes = dict(attrs)
        if "data-logo-downloads" not in attributes:
            return

        category = attributes.get("data-category-id")
        logo_id = attributes.get("data-logo-id")
        svg_hash = attributes.get("data-svg-hash")
        if category and logo_id and svg_hash:
            self.crests[category] = (logo_id, svg_hash)


def _name_tokens(name: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    normalized = normalized.lower().replace("&", " and ")
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized).strip()
    alias = _CREST_NAME_ALIASES.get(normalized, normalized)
    return tuple(alias.split())


def crest_filename(team_name: str) -> str:
    """Return the country-name crest filename used by the bracket."""
    safe_name = re.sub(r"[^A-Za-z0-9]+", "_", team_name.replace("&", "and"))
    return f"{safe_name.strip('_')}_crest.svg"


def _crest_category(team_name: str, categories: set[str]) -> str | None:
    wanted_tokens = _name_tokens(team_name)
    for category in categories:
        category_tokens = _name_tokens(category)
        if category_tokens == wanted_tokens or sorted(category_tokens) == sorted(wanted_tokens):
            return category
    return None


def _download(url: str, timeout: float) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except (HTTPError, URLError, TimeoutError) as error:
        raise RuntimeError(f"Could not download {url}: {error}") from error


def _write_bytes(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_bytes(content)
    temporary_path.replace(path)
    return path


def download_worldcup_json(
    destination: str | Path = SCRIPT_DIR / "worldcup.json",
    *,
    timeout: float = 30.0,
) -> Path:
    """Download and validate the OpenFootball 2026 World Cup JSON file."""
    destination = Path(destination)
    content = _download(WORLDCUP_JSON_URL, timeout)

    try:
        json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("The downloaded World Cup file is not valid JSON") from error

    return _write_bytes(destination, content)


def load_worldcup_json(source: str | Path = SCRIPT_DIR / "worldcup.json") -> dict:
    """Load a downloaded OpenFootball World Cup JSON file."""
    source = Path(source)
    try:
        with source.open(encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Could not load {source}: {error}") from error

    if not isinstance(data.get("matches"), list):
        raise RuntimeError(f"{source} does not contain a matches list")
    return data


def round_of_32_teams(
    source: str | Path = SCRIPT_DIR / "worldcup.json",
) -> list[str]:
    """Return the 32 teams in match order from the Round of 32."""
    data = load_worldcup_json(source)
    teams = [
        team
        for match in data["matches"]
        if match.get("round") == "Round of 32"
        for team in (match.get("team1"), match.get("team2"))
        if isinstance(team, str)
    ]
    if len(teams) != 32:
        raise RuntimeError(f"Expected 32 Round-of-32 teams, found {len(teams)}")
    return teams


def print_round_of_32_teams(
    source: str | Path = SCRIPT_DIR / "worldcup.json",
) -> list[str]:
    """Print and return the Round-of-32 team list."""
    teams = round_of_32_teams(source)
    print("Round of 32 teams:")
    for index, team in enumerate(teams, start=1):
        print(f"{index:2}. {team}")
    return teams


def _winner_index(score: object) -> int | None:
    if not isinstance(score, dict):
        return None

    for score_type in ("p", "et", "ft"):
        values = score.get(score_type)
        if (
            isinstance(values, list)
            and len(values) == 2
            and all(isinstance(value, int) for value in values)
            and values[0] != values[1]
        ):
            return 0 if values[0] > values[1] else 1
    return None


def extract_knockout_results(
    source: str | Path = SCRIPT_DIR / "worldcup.json",
) -> list[dict]:
    """Return normalized knockout matches and their winner when decided."""
    data = load_worldcup_json(source)
    results = []
    for match in data["matches"]:
        if match.get("round") not in KNOCKOUT_ROUNDS:
            continue

        team1 = match.get("team1")
        team2 = match.get("team2")
        winner_index = _winner_index(match.get("score"))
        teams = (team1, team2)
        results.append(
            {
                "round": match.get("round"),
                "match_number": match.get("num"),
                "team1": team1,
                "team2": team2,
                "score": match.get("score"),
                "winner": teams[winner_index] if winner_index is not None else None,
            }
        )
    return results


def extract_knockout_winners(
    source: str | Path = SCRIPT_DIR / "worldcup.json",
) -> dict[frozenset[str], str]:
    """Return graph-ready ``{matchup: winner}`` entries for decided games."""
    winners = {}
    for result in extract_knockout_results(source):
        team1, team2, winner = (
            result["team1"],
            result["team2"],
            result["winner"],
        )
        if (
            isinstance(team1, str)
            and isinstance(team2, str)
            and isinstance(winner, str)
            and not (team1[:1] in ("W", "L") and team1[1:].isdigit())
            and not (team2[:1] in ("W", "L") and team2[1:].isdigit())
        ):
            winners[frozenset((team1, team2))] = winner
    return winners


def download_team_crests(
    destination: str | Path = SCRIPT_DIR / "crests_svg",
    *,
    source: str | Path = SCRIPT_DIR / "worldcup.json",
    overwrite: bool = False,
    timeout: float = 30.0,
) -> dict[str, Path]:
    """Download Round-of-32 SVG crests listed in ``worldcup.json``.

    Files are named ``<country_name>_crest.svg``.
    Existing files are preserved unless ``overwrite`` is true.
    """
    destination = Path(destination)
    team_names = round_of_32_teams(source)
    output_paths = {
        team_name: destination / crest_filename(team_name) for team_name in team_names
    }
    pending = [
        team_name
        for team_name in team_names
        if overwrite or not output_paths[team_name].exists()
    ]
    if not pending:
        return output_paths

    index_html = _download(NATIONAL_TEAMS_URL, timeout).decode("utf-8")
    parser = _CrestIndexParser()
    parser.feed(index_html)

    categories = {
        team_name: _crest_category(team_name, set(parser.crests)) for team_name in team_names
    }
    missing_teams = [name for name, category in categories.items() if category is None]
    if missing_teams:
        missing = ", ".join(missing_teams)
        raise RuntimeError(f"Crest metadata was not found for: {missing}")

    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support import expected_conditions as conditions
    from selenium.webdriver.support.ui import WebDriverWait

    destination.mkdir(parents=True, exist_ok=True)
    options = webdriver.ChromeOptions()
    options.add_argument("--disable-gpu")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--window-position=-32000,-32000")
    options.add_argument("--window-size=800,600")
    options.add_experimental_option(
        "prefs",
        {
            "download.default_directory": str(destination.resolve()),
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": True,
        },
    )

    browser = webdriver.Chrome(options=options)
    try:
        for name in pending:
            category = categories[name]
            assert category is not None
            logo_id, _ = parser.crests[category]

            downloaded_path = None
            attempt_timeout = max(5.0, timeout / 3)
            for _ in range(3):
                browser.get(f"https://football-logos.cc/{category}/{logo_id}/")
                button = WebDriverWait(browser, timeout).until(
                    conditions.element_to_be_clickable(
                        (By.CSS_SELECTOR, "[data-logo-svg-download-button]")
                    )
                )
                files_before = set(destination.iterdir())
                button.click()

                deadline = time.monotonic() + attempt_timeout
                while downloaded_path is None and time.monotonic() < deadline:
                    new_svg_files = [
                        path
                        for path in set(destination.iterdir()) - files_before
                        if path.suffix.lower() == ".svg"
                    ]
                    if new_svg_files:
                        downloaded_path = max(
                            new_svg_files, key=lambda path: path.stat().st_mtime
                        )
                        break
                    time.sleep(0.2)
                if downloaded_path is not None:
                    break
            if downloaded_path is None:
                raise RuntimeError(f"Timed out downloading the SVG crest for {name}")

            content = downloaded_path.read_bytes()
            if b"<svg" not in content[:1000].lower():
                raise RuntimeError(f"The downloaded crest for {name} is not an SVG")
            downloaded_path.replace(output_paths[name])
    finally:
        browser.quit()

    return output_paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--overwrite-crests",
        action="store_true",
        help="replace crest SVGs that already exist",
    )
    parser.add_argument(
        "--json-only",
        action="store_true",
        help="download worldcup.json without downloading crests",
    )
    args = parser.parse_args()

    json_path = download_worldcup_json()
    print(f"Downloaded {json_path}")
    print_round_of_32_teams(json_path)
    completed = sum(
        result["winner"] is not None for result in extract_knockout_results(json_path)
    )
    print(f"Extracted {completed} completed knockout result(s)")

    if not args.json_only:
        crest_paths = download_team_crests(
            source=json_path,
            overwrite=args.overwrite_crests,
        )
        print(f"Crests available in {next(iter(crest_paths.values())).parent}")


if __name__ == "__main__":
    main()
