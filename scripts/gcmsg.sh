#!/usr/bin/env bash
set -euo pipefail

WITH_BODY=false

for arg in "$@"; do
    case "$arg" in
        --with-body|-b) WITH_BODY=true ;;
        *) echo "Unknown argument: $arg" >&2; exit 1 ;;
    esac
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

get_codex_path() {
    if command -v codex &>/dev/null; then
        command -v codex
        return
    fi

    # WSL: check Windows-side VS Code extension
    if [[ -n "${USERPROFILE:-}" ]]; then
        local ext_dir
        ext_dir="$(wslpath "$USERPROFILE")/.vscode/extensions"
        if [[ -d "$ext_dir" ]]; then
            local latest
            latest="$(find "$ext_dir" -maxdepth 1 -type d -name 'openai.chatgpt-*' \
                | sort -t- -k3 -V | tail -1)"
            local candidate="$latest/bin/windows-x86_64/codex.exe"
            if [[ -f "$candidate" ]]; then
                echo "$candidate"
                return
            fi
        fi
    fi

    echo "Unable to find codex. Install the Codex CLI or add it to PATH." >&2
    exit 1
}

CODEX="$(get_codex_path)"

NAME_ONLY="$(git -C "$REPO_ROOT" diff --staged --name-only)"
if [[ -z "$NAME_ONLY" ]]; then
    echo "No staged changes found." >&2
    exit 1
fi

STAT="$(git -C "$REPO_ROOT" diff --staged --stat)"
PATCH="$(git -C "$REPO_ROOT" diff --staged)"

if $WITH_BODY; then
    OUTPUT_RULE="Output only:
1. A Conventional Commit subject line
2. A blank line
3. A short bullet list body"
else
    OUTPUT_RULE="Output only the commit subject line."
fi

PROMPT="Write a concise Conventional Commit message for these staged git changes.

Rules:
- Use format: type(scope): summary
- Prefer: feat, fix, refactor, test, docs, chore
- Keep the subject under 72 characters when possible
- Base the message only on the staged changes below
- Do not use markdown fences
- $OUTPUT_RULE

STAGED STAT:
$STAT

STAGED DIFF:
$PATCH"

PROMPT_FILE="$(mktemp /tmp/gcmsg-prompt-XXXXXX.txt)"
TMP_FILE="$(mktemp /tmp/gcmsg-XXXXXX.txt)"

cleanup() {
    rm -f "$PROMPT_FILE" "$TMP_FILE"
}
trap cleanup EXIT

printf '%s' "$PROMPT" > "$PROMPT_FILE"

"$CODEX" exec \
    --cd "$REPO_ROOT" \
    --skip-git-repo-check \
    --sandbox read-only \
    --color never \
    --output-last-message "$TMP_FILE" \
    - < "$PROMPT_FILE"

if [[ ! -s "$TMP_FILE" ]]; then
    echo "Codex did not return a commit message." >&2
    exit 1
fi

cat "$TMP_FILE"
