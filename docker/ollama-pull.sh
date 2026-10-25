#!/bin/sh
# One-shot for `--profile ollama`: pull every Ollama model listed in tomabotchi.toml into the ollama service.
set -eu
models=$(awk '
  /^\[\[models\]\]/ { if (prov == "ollama" && mod != "") print mod; prov = ""; mod = ""; next }
  /^provider[ ]*=/  { prov = $0; sub(/^[^"]*"/, "", prov); sub(/".*/, "", prov) }
  /^model[ ]*=/     { mod = $0; sub(/^[^"]*"/, "", mod); sub(/".*/, "", mod) }
  END               { if (prov == "ollama" && mod != "") print mod }
' /config/tomabotchi.toml)
for m in $models; do
    echo "pulling $m"
    ollama pull "$m"
done
