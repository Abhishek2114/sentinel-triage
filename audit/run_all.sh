#!/usr/bin/env bash
# Run every reproduction against a source tree and save the output.
#   audit/run_all.sh after                 # this checkout
#   audit/run_all.sh before <old-tree>     # e.g. `git archive <commit> | tar -x -C <dir>`
# Needs DATABASE_URL pointing at a migrated, disposable Postgres (tables are truncated).
set -u
label=$1
tree=${2:-$(cd "$(dirname "$0")/.." && pwd)}
here=$(cd "$(dirname "$0")" && pwd)
python=${PYTHON:-$here/../.venv/bin/python}
mkdir -p "$here/output"
for script in "$here"/repro_*.py; do
  name=$(basename "$script" .py)
  # The scripts import `app` from $tree, so one script set tests both versions
  (cd "$here" && PYTHONPATH="$tree:$here" EMBEDDING_MODEL=hashing "$python" "$script") \
    > "$here/output/$name.$label.txt" 2>&1
  echo "$name: exit $? — $(grep -E '^(NOT )?REPRODUCED' "$here/output/$name.$label.txt" || echo 'no verdict (crashed)')"
done
