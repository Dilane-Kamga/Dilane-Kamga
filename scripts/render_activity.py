"""Render the profile activity card (assets/activity.svg) from the GitHub GraphQL API.

Standard library only. Private repositories are counted in the totals but never named:
the per-repository list keeps public repositories only. On an API error or an incomplete
response the script exits non-zero and leaves the output file untouched, so the README
never shows a partial or zeroed card.

Usage: GITHUB_TOKEN=... python3 -I scripts/render_activity.py --login Dilane-Kamga --out assets/activity.svg
"""

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

API_URL = "https://api.github.com/graphql"

# contributions.totalCount is the number of commits in the repository over the period
# (the nodes are per-day groups), so it needs no pagination.
QUERY = """
query($login: String!) {
  user(login: $login) {
    contributionsCollection {
      contributionCalendar { totalContributions }
      restrictedContributionsCount
      commitContributionsByRepository(maxRepositories: 10) {
        repository { name isPrivate }
        contributions { totalCount }
      }
    }
  }
}
"""

TOP_N = 4
MAX_LABEL = 26
LABELS = {"Udacity-Product-Manager-Nanodegree-Program": "WealthPilot (Udacity PM)"}

# Brand palette (content-factory tokens)
BG = "#0a0f1c"
BORDER = "#334155"
INDIGO = "#6366f1"
AMBER = "#f59e0b"
TEXT = "#f1f5f9"
MUTED = "#94a3b8"


class IncompleteResponse(Exception):
    """The API answered with an error, or without a field the card needs."""


def fetch(login, token):
    body = json.dumps({"query": QUERY, "variables": {"login": login}}).encode("utf-8")
    request = urllib.request.Request(
        API_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": f"{login}-profile-activity-card",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except (OSError, ValueError) as exc:  # URLError/HTTPError/timeout, or a non-JSON body
        raise IncompleteResponse(f"GitHub API request failed: {exc}") from exc


def _field(obj, key, kind):
    if not isinstance(obj, dict) or key not in obj:
        raise IncompleteResponse(f"missing field: {key}")
    value = obj[key]
    # bool is a subclass of int: a count must be a real int, a flag a real bool
    if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
        raise IncompleteResponse(f"unexpected value for {key}: {value!r}")
    return value


def build_model(data, login):
    if not isinstance(data, dict):
        raise IncompleteResponse("response is not a JSON object")
    if data.get("errors"):
        raise IncompleteResponse(f"GitHub API returned errors: {data['errors']}")

    user = _field(_field(data, "data", dict), "user", dict)
    collection = _field(user, "contributionsCollection", dict)
    total = _field(_field(collection, "contributionCalendar", dict), "totalContributions", int)
    private = _field(collection, "restrictedContributionsCount", int)
    if total <= 0 or not 0 <= private <= total:
        raise IncompleteResponse(f"implausible counts: total={total}, private={private}")

    repos = []
    for entry in _field(collection, "commitContributionsByRepository", list):
        repository = _field(entry, "repository", dict)
        name = _field(repository, "name", str)
        is_private = _field(repository, "isPrivate", bool)
        commits = _field(_field(entry, "contributions", dict), "totalCount", int)
        # Hard filter: a private repository's name never leaves this function.
        if is_private or name.lower() == login.lower():
            continue
        repos.append({"name": name, "label": LABELS.get(name, name), "commits": commits})

    repos.sort(key=lambda r: (-r["commits"], r["name"].lower()))
    return {"total": total, "private": private, "repos": repos[:TOP_N]}


def _attr(text):
    return escape(text, {'"': "&quot;"})


def _short(label):
    return label if len(label) <= MAX_LABEL else label[: MAX_LABEL - 1] + "…"


def _commits(n):
    return f"{n:,} commit" + ("" if n == 1 else "s")


def _bar(x, y, width, height, radius=4):
    """Horizontal bar anchored on the baseline at x, rounded at the data end only."""
    r = min(radius, width / 2, height / 2)
    end = x + width
    return (
        f'<path d="M{x} {y}H{end - r:.1f}A{r:.1f} {r:.1f} 0 0 1 {end:.1f} {y + r:.1f}'
        f'V{y + height - r:.1f}A{r:.1f} {r:.1f} 0 0 1 {end - r:.1f} {y + height}H{x}Z" fill="{INDIGO}"/>'
    )


def render_svg(model, as_of):
    total, private, repos = model["total"], model["private"], model["repos"]
    summary = f"{total:,} contributions in the last 12 months, {private:,} in private repositories."
    if repos:
        summary += " Most commits in public repositories: " + ", ".join(
            f"{r['label']} {_commits(r['commits'])}" for r in repos
        ) + "."

    bar_x, bar_max, row_top, row_step = 560, 170, 100, 36
    top = max((r["commits"] for r in repos), default=0) or 1
    rows = []
    for i, r in enumerate(repos):
        y = row_top + i * row_step
        width = max(2, round(bar_max * r["commits"] / top))
        rows.append(
            f'<text x="352" y="{y}" class="body" font-size="14" fill="{TEXT}">{escape(_short(r["label"]))}</text>\n'
            f"    {_bar(bar_x, y - 11, width, 12)}\n"
            f'    <text x="820" y="{y}" text-anchor="end" class="mono" font-size="13" fill="{MUTED}">{_commits(r["commits"])}</text>'
        )
    if not rows:
        rows.append(
            f'<text x="352" y="{row_top}" class="body" font-size="14" fill="{MUTED}">No commits to public repositories in this period</text>'
        )
    baseline = ""
    if repos:
        y1, y2 = row_top - 17, row_top + (len(repos) - 1) * row_step + 5
        baseline = f'<line x1="{bar_x}" y1="{y1}" x2="{bar_x}" y2="{y2}" stroke="{BORDER}" stroke-width="1"/>'

    body = "\n    ".join(rows)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="860" height="260" viewBox="0 0 860 260" role="img" aria-label="{_attr(summary)}">
  <title>{escape(summary)}</title>
  <!-- Generated by scripts/render_activity.py. Do not edit by hand. -->
  <style>
    .title {{ font-family: 'Space Grotesk', 'Segoe UI', system-ui, -apple-system, Roboto, Helvetica, Arial, sans-serif; }}
    .body  {{ font-family: 'Inter', 'Segoe UI', system-ui, -apple-system, Roboto, Helvetica, Arial, sans-serif; }}
    .mono  {{ font-family: 'JetBrains Mono', ui-monospace, 'SF Mono', Menlo, Consolas, monospace; }}
  </style>

  <rect x="0.75" y="0.75" width="858.5" height="258.5" rx="14" fill="{BG}" stroke="{BORDER}" stroke-width="1.5"/>

  <!-- Totals -->
  <text x="40" y="96" class="title" font-size="56" font-weight="700" fill="{TEXT}">{total:,}</text>
  <text x="40" y="124" class="body" font-size="15" fill="{MUTED}">contributions in the last 12 months</text>
  <rect x="40" y="146" width="48" height="3" rx="1.5" fill="{AMBER}"/>
  <text x="40" y="190" class="title" font-size="34" font-weight="700" fill="{TEXT}">{private:,}</text>
  <text x="40" y="214" class="body" font-size="15" fill="{MUTED}">in private repositories</text>

  <line x1="320" y1="40" x2="320" y2="220" stroke="{BORDER}" stroke-width="1"/>

  <!-- Public repositories only -->
  <text x="352" y="62" class="mono" font-size="12" letter-spacing="1.5" fill="{MUTED}">MOST COMMITS, PUBLIC REPOSITORIES</text>
  {baseline}
    {body}

  <text x="820" y="244" text-anchor="end" class="mono" font-size="11" fill="{MUTED}">as of {as_of} (UTC)</text>
</svg>
"""


def main(argv=None):
    parser = argparse.ArgumentParser(description="Render the profile activity card.")
    parser.add_argument("--login", default="Dilane-Kamga")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("error: GITHUB_TOKEN is not set; card not written", file=sys.stderr)
        return 1
    try:
        model = build_model(fetch(args.login, token), args.login)
    except IncompleteResponse as exc:
        print(f"error: {exc}; card not written", file=sys.stderr)
        return 1

    svg = render_svg(model, datetime.now(timezone.utc).date().isoformat())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.out.with_name(args.out.name + ".tmp")
    tmp.write_text(svg, encoding="utf-8", newline="\n")
    os.replace(tmp, args.out)  # atomic: readers see the old card or the new one, never half
    print(
        f"wrote {args.out}: {model['total']} contributions, {model['private']} private, "
        f"{len(model['repos'])} public repositories listed"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
