#!/usr/bin/env bash
# Copies all staged files to a "temp staged" folder at the repo root,
# preserving directory structure. Clears the folder first.

REPO_ROOT="$(git rev-parse --show-toplevel)"
DEST="$REPO_ROOT/temp staged"

# Clear destination
rm -rf "$DEST"
mkdir -p "$DEST"

# Get staged files and copy each one
git diff --name-only --cached | while IFS= read -r file; do
  if [ -f "$REPO_ROOT/$file" ]; then
    dest_dir="$DEST/$(dirname "$file")"
    mkdir -p "$dest_dir"
    cp "$REPO_ROOT/$file" "$dest_dir/"
    echo "Copied: $file"
  else
    echo "Skipped (deleted): $file"
  fi
done

echo ""
echo "Done. Files are in: $DEST"
