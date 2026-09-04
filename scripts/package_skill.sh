#!/usr/bin/env bash
#
# Package the Skills for upload to Cowork.
#
#   scripts/package_skill.sh                     all three, into dist/
#   scripts/package_skill.sh extract-to-schema   just the one
#
# Zip the *directory*, not the SKILL.md. This is the whole reason the script exists.
#
# A Skill is a directory: a SKILL.md and whatever it bundles beside it. Upload a bare SKILL.md
# and it will install, it will load, and it will look completely fine -- and the scripts it
# tells the model to run are not there.
#
# What happens next is the expensive part. A capable model told to run `scripts/validate.py`,
# finding no such file, does not stop. It writes one. The substitute validates something,
# prints something plausible, exits 0, and the transcript reads as a clean run. It is not the
# contract, and the first sign of trouble is a scorecard that is quietly too good.
#
# So validate.py prints a fingerprint -- `docmess-validate/1` -- that a reimplementation has no
# reason to produce, and check_schema.py asserts on that string rather than on the exit code.
# Belt and braces: this script refuses to package a Skill whose bundled scripts are missing.

set -euo pipefail

cd "$(dirname "$0")/.."
REPO="$PWD"
SKILLS="$REPO/skills"
DIST="$REPO/dist"
SCHEMA="$REPO/schema/extraction.schema.json"

# extract-to-schema's validator needs the schema at runtime, inside a sandbox where this
# repository does not exist. So the schema is copied into the Skill rather than referenced.
#
# Two copies can drift, which is a real cost and is paid for deliberately: the alternative is a
# Skill that cannot validate anything once it leaves the repo. It is refreshed here on every
# package, and check_schema.py asserts the two are byte-identical, so a divergence fails a
# check rather than shipping.
sync_schema() {
    local target="$SKILLS/extract-to-schema/schema"
    mkdir -p "$target"
    cp "$SCHEMA" "$target/extraction.schema.json"
}

package() {
    local name="$1"
    local dir="$SKILLS/$name"

    [ -d "$dir" ] || { echo "error: no Skill at skills/$name" >&2; exit 1; }
    [ -f "$dir/SKILL.md" ] || { echo "error: skills/$name has no SKILL.md" >&2; exit 1; }

    # Refuse to ship a Skill that references a script it does not carry. The failure this
    # guards against is silent on the far side, so it has to be loud on this one.
    local missing=0
    while IFS= read -r script; do
        if [ ! -f "$dir/$script" ]; then
            echo "error: skills/$name/SKILL.md tells the model to run '$script', which is not in the Skill" >&2
            missing=1
        fi
    done < <(grep -oE 'python scripts/[A-Za-z0-9_]+\.py' "$dir/SKILL.md" | sed 's/^python //' | sort -u)
    [ "$missing" -eq 0 ] || exit 1

    mkdir -p "$DIST"
    rm -f "$DIST/$name.zip"
    # -x to keep macOS resource forks and .DS_Store out of something a reviewer will unzip.
    ( cd "$SKILLS" && zip -qr "$DIST/$name.zip" "$name" -x '*.DS_Store' -x '__MACOSX/*' -x '*/__pycache__/*' )

    local files
    files=$(unzip -Z1 "$DIST/$name.zip" | grep -vc '/$' || true)
    printf '  %-28s %s  (%s files)\n' "$name" "$(du -h "$DIST/$name.zip" | cut -f1)" "$files"
}

sync_schema

echo "dist/"
if [ $# -gt 0 ]; then
    for name in "$@"; do package "$name"; done
else
    for dir in "$SKILLS"/*/; do package "$(basename "$dir")"; done
fi

cat <<'EOF'

Upload each zip to Cowork as a Skill. Unzip one first and check the scripts are in it --
a Skill that installs without its scripts fails silently, and the model writes a substitute.
EOF
