#!/usr/bin/env python3
"""Build the Cloudflare colo-to-IANA-timezone lookup used at startup."""

import argparse
import csv
import io
from pathlib import Path
from urllib.request import Request, urlopen

AIRPORTS_URL = "https://raw.githubusercontent.com/lxndrblz/Airports/main/airports.csv"
DEFAULT_OUTPUT = Path("/usr/local/share/codemate/colo-timezones.tsv")

# Cloudflare still uses a few legacy or city codes that are absent from the
# airport data set. Keep these small exceptions beside the generated data.
CLOUDFLARE_OVERRIDES = {
    "FRU": "Asia/Bishkek",
    "JXG": "Asia/Shanghai",
    "KBP": "Europe/Kyiv",
    "KIV": "Europe/Chisinau",
    "QWJ": "America/Sao_Paulo",
    "TXL": "Europe/Berlin",
    "ZDM": "Asia/Hebron",
}


def download_airports(url: str) -> str:
    request = Request(url, headers={"User-Agent": "CodeMate Docker build"})
    with urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8-sig")


def build_map(csv_text: str) -> dict[str, str]:
    rows = csv.DictReader(io.StringIO(csv_text))
    required_columns = {"code", "time_zone"}
    if not rows.fieldnames or not required_columns.issubset(rows.fieldnames):
        raise ValueError("Airport data must contain code and time_zone columns")

    mappings = {
        row["code"].strip().upper(): row["time_zone"].strip()
        for row in rows
        if row.get("code", "").strip() and row.get("time_zone", "").strip()
    }
    mappings.update(CLOUDFLARE_OVERRIDES)
    return mappings


def write_map(mappings: dict[str, str], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as destination:
        destination.write(
            "# Airport data: https://github.com/lxndrblz/Airports "
            "(https://creativecommons.org/licenses/by-sa/4.0/)\n"
        )
        destination.write("# Cloudflare colo\tIANA timezone\n")
        for colo, timezone in sorted(mappings.items()):
            destination.write(f"{colo}\t{timezone}\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=AIRPORTS_URL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    mappings = build_map(download_airports(args.source))
    write_map(mappings, args.output)
    print(f"Built {len(mappings)} colo timezone mappings in {args.output}")


if __name__ == "__main__":
    main()
