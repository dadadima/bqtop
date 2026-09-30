"""Generate the Homebrew formula for a release from the built tarballs' .sha256 files.

python scripts/formula.py <version> <assets-dir> [--base-url URL] > bqtop.rb
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

TEMPLATE = """class Bqtop < Formula
  desc "htop for BigQuery: live jobs, principals, projects, hot tables and cost in your terminal"
  homepage "https://github.com/dadadima/bqtop"
  version "{version}"
  license "MIT"

  on_macos do
    on_arm do
      url "{base}/v{version}/bqtop-{version}-macos-arm64.tar.gz"
      sha256 "{macos_arm64}"
    end
    on_intel do
      url "{base}/v{version}/bqtop-{version}-macos-x86_64.tar.gz"
      sha256 "{macos_x86_64}"
    end
  end

  on_linux do
    on_arm do
      url "{base}/v{version}/bqtop-{version}-linux-arm64.tar.gz"
      sha256 "{linux_arm64}"
    end
    on_intel do
      url "{base}/v{version}/bqtop-{version}-linux-x86_64.tar.gz"
      sha256 "{linux_x86_64}"
    end
  end

  def install
    bin.install "bqtop"
  end

  test do
    assert_match "bqtop {version}", shell_output("#{{bin}}/bqtop --version")
  end
end
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("version")
    ap.add_argument("assets", type=Path)
    ap.add_argument("--base-url", default="https://github.com/dadadima/bqtop/releases/download")
    a = ap.parse_args()
    shas = {}
    for f in a.assets.rglob("*.tar.gz.sha256"):
        m = re.search(r"bqtop-[\d.]+-(macos|linux)-(arm64|x86_64)", f.name)
        if m:
            shas[f"{m.group(1)}_{m.group(2)}"] = f.read_text().split()[0]
    missing = {k for k in ("macos_arm64", "macos_x86_64", "linux_arm64", "linux_x86_64")} - set(shas)
    for k in missing:  # keep the formula valid even if a platform did not build
        shas[k] = "0" * 64
    print(TEMPLATE.format(version=a.version, base=a.base_url, **shas), end="")


if __name__ == "__main__":
    main()
