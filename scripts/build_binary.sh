#!/usr/bin/env bash
# Build a single-file bqtop binary with PyInstaller and pack it as dist/bqtop-<version>-<os>-<arch>.tar.gz
# Usage: scripts/build_binary.sh            (needs uv)
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION=$(uv run python -c "import bqtop; print(bqtop.__version__)")
case "$(uname -s)" in Darwin) OS=macos;; Linux) OS=linux;; *) OS=$(uname -s | tr '[:upper:]' '[:lower:]');; esac
case "$(uname -m)" in arm64|aarch64) ARCH=arm64;; x86_64|amd64) ARCH=x86_64;; *) ARCH=$(uname -m);; esac
NAME="bqtop-${VERSION}-${OS}-${ARCH}"

rm -rf build dist
uv run --with pyinstaller pyinstaller --noconfirm --clean --onefile --name bqtop \
  --collect-all textual --collect-all bqtop --collect-data tzdata \
  --copy-metadata google-cloud-bigquery --copy-metadata google-api-core --copy-metadata google-auth \
  --copy-metadata google-cloud-core --copy-metadata google-resumable-media --copy-metadata textual --copy-metadata rich \
  --hidden-import bqtop.app --hidden-import bqtop.wizard --hidden-import bqtop.sources.demo \
  src/bqtop/__main__.py

dist/bqtop --version
dist/bqtop --demo --once -w 1 >/dev/null
tar -C dist -czf "dist/${NAME}.tar.gz" bqtop
( cd dist && shasum -a 256 "${NAME}.tar.gz" > "${NAME}.tar.gz.sha256" )
echo "built dist/${NAME}.tar.gz"
cat "dist/${NAME}.tar.gz.sha256"
