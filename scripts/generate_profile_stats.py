#!/usr/bin/env python3
"""Generate small, self-hosted SVG cards from GitHub's public profile data."""

from __future__ import annotations

import calendar
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.sax.saxutils import escape


USERNAME = "Isasola"
API_ROOT = "https://api.github.com"
GRAPH_ROOT = f"https://github.com/users/{USERNAME}/contributions"
OUTPUT_DIR = Path(__file__).resolve().parents[1] / "stats"
TIMEOUT_SECONDS = 30

COLORS = {
    "background": "#10191d",
    "panel": "#142126",
    "border": "#2a3b40",
    "text": "#f4f0e7",
    "muted": "#9aaba8",
    "accent": "#e2bd72",
    "track": "#25353a",
    "levels": ["#243238", "#315e55", "#38866d", "#56ad7f", "#9bd39b"],
}

LANGUAGE_COLORS = {
    "TypeScript": "#3178c6",
    "JavaScript": "#f1e05a",
    "Python": "#3572a5",
    "CSS": "#563d7c",
    "HTML": "#e34c26",
    "Rust": "#dea584",
    "Go": "#00add8",
    "Java": "#b07219",
    "Shell": "#89e051",
}


def fetch(url: str, accept: str) -> bytes:
    request = Request(
        url,
        headers={
            "Accept": accept,
            "User-Agent": "Isasola-profile-stats/1.0",
            "Cache-Control": "no-cache",
        },
    )
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.read()
    except HTTPError as error:
        raise RuntimeError(
            f"GitHub returned HTTP {error.code} while reading public profile data."
        ) from None
    except (URLError, TimeoutError) as error:
        reason = getattr(error, "reason", "request timed out")
        raise RuntimeError(f"Could not read public GitHub data: {reason}") from None


def fetch_json(url: str) -> object:
    return json.loads(fetch(url, "application/vnd.github+json").decode("utf-8"))


def fetch_profile_and_repositories() -> tuple[dict, list[dict]]:
    profile = fetch_json(f"{API_ROOT}/users/{USERNAME}")
    if not isinstance(profile, dict) or profile.get("login", "").casefold() != USERNAME.casefold():
        raise RuntimeError("GitHub did not return the expected public user profile.")

    repositories: list[dict] = []
    page = 1
    while True:
        query = urlencode(
            {
                "per_page": 100,
                "page": page,
                "type": "owner",
                "sort": "updated",
                "direction": "desc",
            }
        )
        result = fetch_json(f"{API_ROOT}/users/{USERNAME}/repos?{query}")
        if not isinstance(result, list):
            raise RuntimeError("GitHub did not return a repository list.")
        repositories.extend(repo for repo in result if isinstance(repo, dict))
        if len(result) < 100:
            break
        page += 1

    return profile, repositories


def fetch_language_totals(repositories: list[dict]) -> dict[str, int]:
    totals: dict[str, int] = {}
    candidates = [
        repo
        for repo in repositories
        if not repo.get("private")
        and not repo.get("fork")
        and not repo.get("archived")
        and repo.get("languages_url")
    ]

    for repo in candidates:
        endpoint = repo["languages_url"]
        expected_prefix = f"{API_ROOT}/repos/{USERNAME}/"
        if not isinstance(endpoint, str) or not endpoint.startswith(expected_prefix):
            raise RuntimeError("GitHub returned an unexpected language endpoint.")
        languages = fetch_json(endpoint)
        if not isinstance(languages, dict):
            raise RuntimeError("GitHub did not return repository language totals.")
        for language, byte_count in languages.items():
            if isinstance(language, str) and isinstance(byte_count, int):
                totals[language] = totals.get(language, 0) + byte_count

    return totals


def fetch_public_commit_total(
    repositories: list[dict], start: date, end: date
) -> int:
    total = 0
    candidates = [
        repo
        for repo in repositories
        if not repo.get("private")
        and not repo.get("fork")
        and not repo.get("archived")
        and repo.get("pushed_at")
        and str(repo["pushed_at"])[:10] >= start.isoformat()
    ]
    since = f"{start.isoformat()}T00:00:00Z"
    until = f"{end.isoformat()}T23:59:59Z"

    for repo in candidates:
        full_name = repo.get("full_name")
        expected_prefix = f"{USERNAME}/"
        if not isinstance(full_name, str) or not full_name.startswith(expected_prefix):
            raise RuntimeError("GitHub returned an unexpected repository name.")
        page = 1
        while True:
            query = urlencode(
                {
                    "author": USERNAME,
                    "since": since,
                    "until": until,
                    "per_page": 100,
                    "page": page,
                }
            )
            commits = fetch_json(
                f"{API_ROOT}/repos/{full_name}/commits?{query}"
            )
            if not isinstance(commits, list):
                raise RuntimeError("GitHub did not return a commit list.")
            total += len(commits)
            if len(commits) < 100:
                break
            page += 1
    return total


class ContributionCalendarParser(HTMLParser):
    """Pair GitHub's daily calendar cells with their accessible tooltip text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cells: list[dict[str, str]] = []
        self.tooltips: dict[str, str] = {}
        self.active_tooltip: str | None = None
        self.active_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "td" and attributes.get("data-date"):
            self.cells.append(
                {
                    "date": attributes["data-date"] or "",
                    "id": attributes.get("id") or "",
                    "level": attributes.get("data-level") or "",
                }
            )
        elif tag == "tool-tip" and attributes.get("for"):
            self.active_tooltip = attributes["for"]
            self.active_text = []

    def handle_data(self, data: str) -> None:
        if self.active_tooltip is not None:
            self.active_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "tool-tip" and self.active_tooltip is not None:
            self.tooltips[self.active_tooltip] = " ".join(self.active_text).strip()
            self.active_tooltip = None
            self.active_text = []


def count_from_tooltip(text: str) -> int:
    if re.search(r"\bno contributions?\b", text, flags=re.IGNORECASE):
        return 0
    match = re.search(r"\b(\d[\d,]*)\s+contributions?\b", text, flags=re.IGNORECASE)
    if not match:
        raise RuntimeError(
            "GitHub's contribution calendar did not include a readable daily count."
        )
    return int(match.group(1).replace(",", ""))


def fetch_contributions(start: date, end: date) -> dict[date, int]:
    query = urlencode({"from": start.isoformat(), "to": end.isoformat()})
    markup = fetch(f"{GRAPH_ROOT}?{query}", "text/html").decode("utf-8")
    parser = ContributionCalendarParser()
    parser.feed(markup)
    parser.close()

    counts: dict[date, int] = {}
    for cell in parser.cells:
        try:
            day = date.fromisoformat(cell["date"])
        except ValueError:
            continue
        if day < start or day > end:
            continue
        tooltip = parser.tooltips.get(cell["id"], "")
        if not tooltip:
            raise RuntimeError(
                "GitHub's contribution calendar changed; a daily tooltip is missing."
            )
        counts[day] = count_from_tooltip(tooltip)

    expected_days = (end - start).days + 1
    if len(counts) != expected_days:
        raise RuntimeError(
            "GitHub's contribution calendar changed; expected "
            f"{expected_days} daily cells but found {len(counts)}."
        )
    return counts


def streak_lengths(counts: dict[date, int], today: date) -> tuple[int, int]:
    ordered_days = sorted(counts)
    best = 0
    run = 0
    previous: date | None = None
    for day in ordered_days:
        if counts[day] > 0:
            run = run + 1 if previous == day - timedelta(days=1) else 1
            best = max(best, run)
        else:
            run = 0
        previous = day

    current = 0
    cursor = today if counts.get(today, 0) > 0 else today - timedelta(days=1)
    while counts.get(cursor, 0) > 0:
        current += 1
        cursor -= timedelta(days=1)
    return current, best


def svg_start(width: int, height: int, title: str, description: str) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
        f"<title id=\"title\">{escape(title)}</title>",
        f"<desc id=\"desc\">{escape(description)}</desc>",
        f'<rect width="{width}" height="{height}" rx="18" fill="{COLORS["background"]}"/>',
        f'<rect x="1" y="1" width="{width - 2}" height="{height - 2}" rx="17" fill="none" stroke="{COLORS["border"]}"/>',
    ]


def svg_text(
    x: float,
    y: float,
    value: str,
    size: int,
    color: str,
    weight: int = 400,
    anchor: str = "start",
) -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" fill="{color}" font-family="Inter, ui-sans-serif, system-ui, sans-serif" '
        f'font-size="{size}" font-weight="{weight}" text-anchor="{anchor}">{escape(value)}</text>'
    )


def render_account(
    profile: dict,
    repositories: list[dict],
    total_contributions: int,
    public_commits: int,
) -> str:
    public_repositories = [repo for repo in repositories if not repo.get("private")]
    repository_stars = sum(int(repo.get("stargazers_count") or 0) for repo in public_repositories)
    values = [
        ("Contributions · YTD", f"{total_contributions:,}"),
        ("Public commits · YTD", f"{public_commits:,}"),
        ("Public repositories", f"{int(profile.get('public_repos') or 0):,}"),
        ("Repository stars", f"{repository_stars:,}"),
    ]
    width, height = 960, 190
    output = svg_start(
        width,
        height,
        "GitHub profile snapshot",
        "A summary of year-to-date profile-visible contributions and public default-branch commits, public repositories, and repository stars.",
    )
    output.append(svg_text(28, 32, f"PUBLIC PROFILE  /  {USERNAME.upper()}", 11, COLORS["accent"], 700))
    output.append(svg_text(28, 56, "A snapshot of what I'm building in public", 20, COLORS["text"], 650))
    cell_width = 228
    for index, (label, value) in enumerate(values):
        x = 28 + index * cell_width
        output.append(svg_text(x, 111, value, 29, COLORS["text"], 700))
        output.append(svg_text(x, 136, label, 12, COLORS["muted"], 500))
    output.append(svg_text(28, 157, "Commit totals count authored commits on public default branches.", 9, COLORS["muted"], 400))
    updated = datetime.now(timezone.utc).strftime("Updated %d %b %Y · UTC")
    output.append(svg_text(28, 175, updated, 9, COLORS["muted"], 400))
    output.append("</svg>")
    return "\n".join(output)


def render_languages(language_totals: dict[str, int]) -> str:
    ranked = sorted(language_totals.items(), key=lambda item: item[1], reverse=True)[:6]
    total_bytes = sum(language_totals.values())
    width, height = 960, 230
    output = svg_start(
        width,
        height,
        "Most-used programming languages",
        "Language shares are calculated from GitHub's byte totals for public, non-fork, non-archived repositories.",
    )
    output.append(svg_text(28, 32, "LANGUAGE MIX  /  PUBLIC SOURCE REPOSITORIES", 11, COLORS["accent"], 700))
    output.append(svg_text(28, 56, "Languages in the code I share", 20, COLORS["text"], 650))

    if not ranked or total_bytes <= 0:
        output.append(svg_text(28, 120, "No language data is available yet.", 14, COLORS["muted"], 400))
    else:
        for index, (language, byte_count) in enumerate(ranked):
            y = 89 + index * 22
            share = byte_count / total_bytes
            color = LANGUAGE_COLORS.get(language, "#55b6a0")
            output.append(svg_text(28, y + 12, language, 12, COLORS["text"], 550))
            output.append(
                f'<rect x="190" y="{y}" width="610" height="8" rx="4" fill="{COLORS["track"]}"/>'
            )
            output.append(
                f'<rect x="190" y="{y}" width="{610 * share:.2f}" height="8" rx="4" fill="{color}"/>'
            )
            output.append(svg_text(836, y + 12, f"{share * 100:.1f}%", 11, COLORS["muted"], 500))

    output.append(svg_text(28, 211, "Weighted by code size · forks and archived repositories excluded", 10, COLORS["muted"]))
    output.append("</svg>")
    return "\n".join(output)


def contribution_color(count: int) -> str:
    if count <= 0:
        return COLORS["levels"][0]
    if count <= 2:
        return COLORS["levels"][1]
    if count <= 5:
        return COLORS["levels"][2]
    if count <= 9:
        return COLORS["levels"][3]
    return COLORS["levels"][4]


def render_activity(counts: dict[date, int], start: date, end: date) -> str:
    total = sum(counts.values())
    current, best = streak_lengths(counts, end)
    width, height = 960, 272
    output = svg_start(
        width,
        height,
        "GitHub contribution activity for this year",
        f"{total:,} profile-visible contributions from {start.isoformat()} to {end.isoformat()}, with a current streak of {current} days and a best streak of {best} days.",
    )
    output.append(svg_text(28, 31, f"CONTRIBUTION ACTIVITY  /  {end.year} YEAR TO DATE", 11, COLORS["accent"], 700))
    output.append(svg_text(28, 54, "A year of showing up", 20, COLORS["text"], 650))

    metrics = [
        ("Contributions", f"{total:,}"),
        ("Current streak", f"{current} days"),
        ("Best streak · YTD", f"{best} days"),
    ]
    for index, (label, value) in enumerate(metrics):
        x = 28 + index * 248
        output.append(svg_text(x, 83, value, 17, COLORS["text"], 650))
        output.append(svg_text(x, 100, label, 10, COLORS["muted"], 500))

    first_sunday = start - timedelta(days=(start.weekday() + 1) % 7)
    chart_x, chart_y = 60, 130
    col_step, row_step, cell_size = 16, 15, 11
    output.extend(
        [
            svg_text(28, chart_y + 10, "Mon", 9, COLORS["muted"]),
            svg_text(28, chart_y + row_step * 3 + 10, "Wed", 9, COLORS["muted"]),
            svg_text(28, chart_y + row_step * 5 + 10, "Fri", 9, COLORS["muted"]),
        ]
    )

    month_labels: set[tuple[int, int]] = set()
    for day in sorted(counts):
        column = (day - first_sunday).days // 7
        weekday_row = (day.weekday() + 1) % 7
        x = chart_x + column * col_step
        y = chart_y + weekday_row * row_step
        output.append(
            f'<rect x="{x}" y="{y}" width="{cell_size}" height="{cell_size}" rx="3" fill="{contribution_color(counts[day])}"/>'
        )
        if day.day == 1 or day == start:
            month_key = (day.year, day.month)
            if month_key not in month_labels:
                output.append(
                    svg_text(x, chart_y - 8, calendar.month_abbr[day.month], 9, COLORS["muted"], 500)
                )
                month_labels.add(month_key)

    legend_x = 776
    output.append(svg_text(legend_x - 32, 252, "Less", 9, COLORS["muted"], 400))
    for index, level in enumerate(COLORS["levels"]):
        output.append(
            f'<rect x="{legend_x + index * 14}" y="242" width="10" height="10" rx="2" fill="{level}"/>'
        )
    output.append(svg_text(legend_x + 82, 252, "More", 9, COLORS["muted"], 400))
    output.append(svg_text(28, 252, "Profile-visible daily counts · year to date · GitHub contribution calendar", 9, COLORS["muted"]))
    output.append("</svg>")
    return "\n".join(output)


def main() -> int:
    today = datetime.now(timezone.utc).date()
    start = date(today.year, 1, 1)

    profile, repositories = fetch_profile_and_repositories()
    language_totals = fetch_language_totals(repositories)
    public_commits = fetch_public_commit_total(repositories, start, today)
    contribution_counts = fetch_contributions(start, today)

    cards = {
        "account.svg": render_account(
            profile,
            repositories,
            sum(contribution_counts.values()),
            public_commits,
        ),
        "languages.svg": render_languages(language_totals),
        "activity.svg": render_activity(contribution_counts, start, today),
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for filename, contents in cards.items():
        (OUTPUT_DIR / filename).write_text(contents + "\n", encoding="utf-8")
        print(f"Generated stats/{filename}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, HTTPError, URLError, json.JSONDecodeError) as error:
        print(f"Profile stats update failed: {error}", file=sys.stderr)
        raise SystemExit(1) from None
