import importlib.util
import os
import subprocess
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).parents[1]
BUILDER = ROOT / "docker/setup/python/build-colo-timezones.py"
CHECK_REGION = ROOT / "docker/setup/shell/check-region-cloudflare.sh"


def load_builder() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_colo_timezones", BUILDER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_builds_colo_timezone_map(tmp_path: Path) -> None:
    builder = load_builder()
    csv_text = (
        "code,icao,name,latitude,longitude,elevation,url,time_zone\n"
        "SJC,KSJC,San Jose,0,0,0,,America/Los_Angeles\n"
        "LHR,EGLL,London,0,0,0,,Europe/London\n"
        "XXX,XXXX,Unknown,0,0,0,,\n"
    )

    mappings = builder.build_map(csv_text)
    output = tmp_path / "colo-timezones.tsv"
    builder.write_map(mappings, output)

    generated = output.read_text()
    assert "SJC\tAmerica/Los_Angeles\n" in generated
    assert "LHR\tEurope/London\n" in generated
    assert "KBP\tEurope/Kyiv\n" in generated
    assert "QWJ\tAmerica/Sao_Paulo\n" in generated
    assert "\nXXX\t" not in generated


def run_region_check(
    tmp_path: Path,
    *,
    trace: str,
    country: str = "",
    allowed_ip: str = "",
    timezone: str = "America/Los_Angeles",
) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text(f"#!/bin/sh\nprintf '%s' '{trace}'\n")
    curl.chmod(0o755)
    gh = bin_dir / "gh"
    gh.write_text("#!/bin/sh\nexit 1\n")
    gh.chmod(0o755)

    timezone_map = tmp_path / "colo-timezones.tsv"
    timezone_map.write_text("SJC\tAmerica/Los_Angeles\n")
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "CODEMATE_ALLOW_COUNTRY": country,
        "CODEMATE_ALLOW_IP": allowed_ip,
        "CODEMATE_COLO_TIMEZONE_MAP": str(timezone_map),
        "TZ": timezone,
    }
    return subprocess.run(
        ["bash", str(CHECK_REGION)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_cloudflare_loc_and_colo_timezone_pass(tmp_path: Path) -> None:
    result = run_region_check(
        tmp_path,
        trace="ip=203.0.113.7\ncolo=SJC\nloc=US\n",
        country="US",
    )

    assert result.returncode == 0
    assert "matched by country 'US'" in result.stdout
    assert "colo=SJC" in result.stdout


def test_cloudflare_ip_allowlist_takes_precedence(tmp_path: Path) -> None:
    result = run_region_check(
        tmp_path,
        trace="ip=203.0.113.7\ncolo=SJC\nloc=US\n",
        country="CA",
        allowed_ip="203.0.113.0/24",
    )

    assert result.returncode == 0
    assert "matched by IP '203.0.113.7'" in result.stdout


def test_cloudflare_colo_timezone_must_match_tz(tmp_path: Path) -> None:
    result = run_region_check(
        tmp_path,
        trace="ip=203.0.113.7\ncolo=SJC\nloc=US\n",
        country="US",
        timezone="UTC",
    )

    assert result.returncode == 1
    assert (
        "Cloudflare colo 'SJC' maps to timezone='America/Los_Angeles'" in result.stdout
    )


def test_cloudflare_trace_requires_colo_loc_and_ip(tmp_path: Path) -> None:
    result = run_region_check(
        tmp_path,
        trace="ip=203.0.113.7\ncolo=SJC\n",
        country="US",
    )

    assert result.returncode == 1
    assert "Could not detect colo, loc, and ip" in result.stdout
