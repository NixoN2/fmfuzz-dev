#!/usr/bin/env python3
"""
Download latest solver binary from GitHub releases.

Reads the solver's config from solver.json to determine the GitHub repo
and binary name. Falls back to explicit --repo and --binary-name args.

Usage:
    python3 download_solver_release.py --solver cvc5 [output_dir]
    python3 download_solver_release.py --repo cvc5/cvc5 --binary-name cvc5 [output_dir]
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: 'requests' package is required. Install with: pip install requests")
    sys.exit(1)


def _scripts_dir():
    return Path(__file__).resolve().parent.parent


def _read_solver_config(solver_name):
    config_path = _scripts_dir() / "solvers" / solver_name / "solver.json"
    if not config_path.exists():
        print(f"ERROR: Solver config not found: {config_path}")
        sys.exit(1)
    with open(config_path) as f:
        return json.load(f)


def install_unzip():
    if shutil.which("unzip"):
        return
    print("Installing unzip...")
    try:
        subprocess.run(["sudo", "apt-get", "update"], check=True, capture_output=True)
        subprocess.run(
            ["sudo", "apt-get", "install", "-y", "unzip"],
            check=True,
            capture_output=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("ERROR: Cannot install unzip automatically. Please install it manually.")
        sys.exit(1)


def get_latest_release(repo, github_token=None):
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    headers = {"User-Agent": "FM-Fuzz/1.0"}
    if github_token:
        headers["Authorization"] = f"token {github_token}"
    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        print(f"ERROR: Failed to get latest release from {repo}: {e}")
        if (
            hasattr(e, "response")
            and e.response is not None
            and e.response.status_code == 403
        ):
            print(
                "Rate limit hit. Set GITHUB_TOKEN environment variable to increase limit."
            )
        sys.exit(1)


def find_linux_binary_asset(assets):
    """Find a Linux x86_64 binary asset from release assets."""
    for asset in assets:
        url = asset["browser_download_url"].lower()
        is_linux = "linux" in url
        is_x86 = "x86_64" in url or "x64" in url
        is_archive = url.endswith(".zip") or url.endswith(".tar.gz")
        if is_linux and is_x86 and is_archive:
            return asset["browser_download_url"]
    return None


def download_and_extract(asset_url, binary_name, output_dir, github_token=None):
    print(f"Downloading: {asset_url}")

    with tempfile.TemporaryDirectory() as temp_dir:
        is_zip = asset_url.lower().endswith(".zip")
        ext = ".zip" if is_zip else ".tar.gz"
        archive_path = os.path.join(temp_dir, f"solver{ext}")

        headers = {"User-Agent": "FM-Fuzz/1.0"}
        if github_token:
            headers["Authorization"] = f"token {github_token}"
        try:
            response = requests.get(
                asset_url, headers=headers, timeout=60, stream=True
            )
            response.raise_for_status()
            with open(archive_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
        except requests.RequestException as e:
            print(f"ERROR: Failed to download: {e}")
            sys.exit(1)

        print("Extracting...")
        extract_dir = os.path.join(temp_dir, "extracted")
        os.makedirs(extract_dir, exist_ok=True)

        if is_zip:
            subprocess.run(
                ["unzip", "-q", archive_path, "-d", extract_dir], check=True
            )
        else:
            subprocess.run(
                ["tar", "-xzf", archive_path, "-C", extract_dir], check=True
            )

        # Find the binary in extracted files
        for root, dirs, files in os.walk(extract_dir):
            if binary_name in files:
                src = os.path.join(root, binary_name)
                if os.path.isfile(src):
                    output_path = os.path.join(output_dir, binary_name)
                    shutil.copy2(src, output_path)
                    os.chmod(output_path, 0o755)
                    print(f"Installed to: {output_path}")
                    return output_path

        print(f"ERROR: '{binary_name}' binary not found in archive")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Download latest solver binary from GitHub releases"
    )
    parser.add_argument(
        "--solver",
        help="Solver name (reads repo and binary name from solver.json)",
    )
    parser.add_argument(
        "--repo",
        help="GitHub repo (e.g. cvc5/cvc5). Overrides solver config.",
    )
    parser.add_argument(
        "--binary-name",
        help="Binary name to find in archive (e.g. cvc5). Overrides solver config.",
    )
    parser.add_argument(
        "output_dir",
        nargs="?",
        default=os.path.join(os.path.expanduser("~"), ".local", "bin"),
        help="Output directory (default: ~/.local/bin)",
    )
    args = parser.parse_args()

    # Resolve repo and binary name
    if args.solver:
        config = _read_solver_config(args.solver)
        ci = config.get("ci", {})
        repo = args.repo or ci.get("github_release_repo", "")
        binary_name = args.binary_name or ci.get("github_release_binary", config["name"])
    elif args.repo and args.binary_name:
        repo = args.repo
        binary_name = args.binary_name
    else:
        parser.error("Either --solver or both --repo and --binary-name are required")

    if not repo:
        print(f"ERROR: No GitHub release repo configured for solver '{args.solver}'")
        print("Add 'ci.github_release_repo' to solver.json")
        sys.exit(1)

    install_unzip()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    github_token = os.environ.get("GITHUB_TOKEN")

    print(f"Finding latest {binary_name} release from {repo}...")
    release = get_latest_release(repo, github_token)
    tag = release["tag_name"]
    print(f"Latest release: {tag}")

    binary_url = find_linux_binary_asset(release["assets"])
    if not binary_url:
        print(f"ERROR: Linux x86_64 binary not found in {tag}")
        sys.exit(1)

    binary_path = download_and_extract(binary_url, binary_name, str(output_dir), github_token)

    # Verify
    result = subprocess.run(
        [binary_path, "--version"], capture_output=True, text=True, check=False
    )
    if result.returncode == 0:
        print(result.stdout.strip())


if __name__ == "__main__":
    main()
