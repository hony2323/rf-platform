# Thin wrapper for developers in the repo. End users on a `pip install` should
# just run `rf-agent setup windows` directly.
$ErrorActionPreference = "Stop"
rf-agent setup windows @args
exit $LASTEXITCODE
