#!/bin/bash
# make-release.sh - builds the archive for a release and the checksum that goes with it.
#
#     ./setup/make-release.sh 1.1
#
# Both files belong on the GitHub release. Installations check the archive against
# SHA256SUMS before running anything in it, and refuse to update when it is missing, so a
# release published without it cannot be installed by anyone already running the program.
set -Eeuo pipefail

VERSION="${1:-}"
[ -n "$VERSION" ] || { echo "Usage: $0 <version>   e.g. $0 1.1" >&2; exit 2; }
[[ "$VERSION" =~ ^[0-9]+(\.[0-9]+){1,3}$ ]] || { echo "A version looks like 1.1 or 1.1.2" >&2; exit 2; }

ROOT="$(dirname "$(dirname "$(readlink -f "$0")")")"
cd "$ROOT"

FILE_VERSION="$(tr -d '[:space:]' < VERSION)"
if [ "$FILE_VERSION" != "$VERSION" ]; then
    echo "VERSION says $FILE_VERSION and this release says $VERSION." >&2
    echo "They have to match, or the program will offer itself an update forever." >&2
    exit 1
fi

git rev-parse --git-dir >/dev/null 2>&1 || { echo "Not a git checkout" >&2; exit 1; }
if [ -n "$(git status --porcelain)" ]; then
    echo "The working tree has changes that are not committed." >&2
    echo "A release is built from what is committed, so that what people download is what is here." >&2
    exit 1
fi

OUT="$ROOT/dist"
mkdir -p "$OUT"
NAME="armonico-v$VERSION"
ARCHIVE="$OUT/$NAME.zip"

# From git, so nothing local and nothing ignored can travel with it. One folder inside,
# named after the release, which is what the updater and a person unpacking it both expect.
git archive --format=zip --prefix="$NAME/" -o "$ARCHIVE" HEAD

( cd "$OUT" && sha256sum "$NAME.zip" > SHA256SUMS )

echo
echo "Built:"
echo "  $ARCHIVE"
echo "  $OUT/SHA256SUMS"
echo
cat "$OUT/SHA256SUMS"
echo
echo "Next: tag v$VERSION, then put both files on the GitHub release."
