param(
    [switch]$WithBody
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$safeRepoRoot = $repoRoot -replace "\\", "/"

function Get-CodexPath {
    $cmd = Get-Command codex -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }

    $candidates = @(
        (Join-Path $env:USERPROFILE ".vscode\extensions\openai.chatgpt-26.415.20818-win32-x64\bin\windows-x86_64\codex.exe"),
        (Join-Path $env:USERPROFILE ".vscode\extensions")
    )

    if (Test-Path $candidates[0]) {
        return $candidates[0]
    }

    if (Test-Path $candidates[1]) {
        $latest = Get-ChildItem $candidates[1] -Directory -Filter "openai.chatgpt-*" |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1
        if ($latest) {
            $nested = Join-Path $latest.FullName "bin\windows-x86_64\codex.exe"
            if (Test-Path $nested) {
                return $nested
            }
        }
    }

    throw "Unable to find codex.exe. Install the Codex CLI or add it to PATH."
}

$codexPath = Get-CodexPath

$nameOnly = git -C $repoRoot -c "safe.directory=$safeRepoRoot" diff --staged --name-only
if (-not $nameOnly) {
    Write-Error "No staged changes found."
    exit 1
}

$stat = git -C $repoRoot -c "safe.directory=$safeRepoRoot" diff --staged --stat | Out-String
$patch = git -C $repoRoot -c "safe.directory=$safeRepoRoot" diff --staged | Out-String

$outputRule = if ($WithBody) {
@"
Output only:
1. A Conventional Commit subject line
2. A blank line
3. A short bullet list body
"@
} else {
    "Output only the commit subject line."
}

$prompt = @"
Write a concise Conventional Commit message for these staged git changes.

Rules:
- Use format: type(scope): summary
- Prefer: feat, fix, refactor, test, docs, chore
- Keep the subject under 72 characters when possible
- Base the message only on the staged changes below
- Do not use markdown fences
- $outputRule

STAGED STAT:
$stat

STAGED DIFF:
$patch
"@

$promptFile = Join-Path ([System.IO.Path]::GetTempPath()) ("gcmsg-prompt-" + [System.Guid]::NewGuid().ToString("N") + ".txt")
$tmpFile = Join-Path ([System.IO.Path]::GetTempPath()) ("gcmsg-" + [System.Guid]::NewGuid().ToString("N") + ".txt")
$stdoutFile = Join-Path ([System.IO.Path]::GetTempPath()) ("gcmsg-stdout-" + [System.Guid]::NewGuid().ToString("N") + ".txt")
$stderrFile = Join-Path ([System.IO.Path]::GetTempPath()) ("gcmsg-stderr-" + [System.Guid]::NewGuid().ToString("N") + ".txt")

try {
    Set-Content -LiteralPath $promptFile -Value $prompt -NoNewline

    $arguments = @(
        "exec"
        "--cd", $repoRoot
        "--skip-git-repo-check"
        "--sandbox", "read-only"
        "--color", "never"
        "--output-last-message", $tmpFile
        "-"
    )

    $process = Start-Process `
        -FilePath $codexPath `
        -ArgumentList $arguments `
        -RedirectStandardInput $promptFile `
        -RedirectStandardOutput $stdoutFile `
        -RedirectStandardError $stderrFile `
        -NoNewWindow `
        -PassThru `
        -Wait

    if ($process.ExitCode -ne 0) {
        $stderr = if (Test-Path $stderrFile) { Get-Content $stderrFile -Raw } else { "" }
        throw ("Codex exited with code " + $process.ExitCode + "." + $(if ($stderr) { " " + $stderr.Trim() } else { "" }))
    }

    if (-not (Test-Path $tmpFile)) {
        throw "Codex did not return a commit message."
    }

    Get-Content $tmpFile -Raw
}
finally {
    if (Test-Path $promptFile) {
        Remove-Item -LiteralPath $promptFile -Force
    }
    if (Test-Path $tmpFile) {
        Remove-Item -LiteralPath $tmpFile -Force
    }
    if (Test-Path $stdoutFile) {
        Remove-Item -LiteralPath $stdoutFile -Force
    }
    if (Test-Path $stderrFile) {
        Remove-Item -LiteralPath $stderrFile -Force
    }
}
