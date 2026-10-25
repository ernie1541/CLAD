#!/usr/bin/env bash
# Installs the three Traveler bots into OpenClaw:
#   cad-lead-engineer (gemma4:26b), cad-sub-engineer (gemma4:e4b), cad-machinist (gemma4:26b)
# Copies each agent workspace + the cadflow skill, pulls the Gemma models, and prints the config to merge.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
OC="${OPENCLAW_HOME:-$HOME/.openclaw}"

echo "Installing Traveler agents into $OC"
for agent in cad-lead-engineer cad-sub-engineer cad-machinist; do
  ws="$OC/workspace-$agent"
  mkdir -p "$ws/skills"
  for f in AGENTS.md SOUL.md IDENTITY.md TOOLS.md; do
    if [ -f "$ws/$f" ] && ! cmp -s "$HERE/agents/$agent/$f" "$ws/$f"; then
      cp "$ws/$f" "$ws/$f.bak"
      echo "  backed up existing $ws/$f → $f.bak"
    fi
    cp "$HERE/agents/$agent/$f" "$ws/$f"
  done
  rm -rf "$ws/skills/cadflow"
  cp -R "$HERE/skills/cadflow" "$ws/skills/cadflow"
  echo "  ✓ $agent → $ws"
done

if command -v ollama >/dev/null 2>&1; then
  for m in gemma4:26b gemma4:e4b; do
    if ollama list | awk '{print $1}' | grep -qx "$m"; then
      echo "  ✓ $m already pulled"
    else
      printf "Pull %s now? It is a large download. [y/N] " "$m"
      read -r yn
      case "$yn" in [Yy]*) ollama pull "$m" ;; *) echo "  skipped (run: ollama pull $m)";; esac
    fi
  done
else
  echo "  ! Ollama not found. Install it from https://ollama.com, then: ollama pull gemma4:26b && ollama pull gemma4:e4b"
fi

echo
PY=python3
command -v python3 >/dev/null 2>&1 || PY="$HERE/../.venv/bin/python"
if "$PY" "$HERE/merge_config.py" --config "$OC/openclaw.json"; then
  cat <<DONE

Done. Last steps:
  1. openclaw gateway restart
  2. openclaw doctor                 (fix anything it reports)
  3. cd "$HERE/.." && ./start.sh     (the sidebar should say "Agents via OpenClaw")
DONE
else
  echo "Merge $HERE/openclaw.fragment.json5 into $OC/openclaw.json by hand, then run: openclaw gateway restart"
fi
