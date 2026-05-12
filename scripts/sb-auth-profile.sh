#!/usr/bin/env bash
# shellcheck shell=bash
#
# Superblocks CLI "profile" helper backed by the shared repository .env.
# Compatible with bash 4+ and zsh 5+. Usage:
#
#   source ./scripts/sb-auth-profile.sh
#   sb_auth_profile list           # show profiles and auth.json paths (alias: ls)
#   sb_auth_profile cloud          # use SUPERBLOCKS_CLOUD_AUTH_FILE from .env
#   sb_auth_profile cloud_prem     # use SUPERBLOCKS_CLOUD_PREM_AUTH_FILE from .env
#
# Then run: superblocks config set domain ... && superblocks login
#
# This only sets SUPERBLOCKS_AUTH_FILE; it does not store secrets in the repo.
# The .env file is the single source of config for MCP, CLI, and the SCIM scripts.
#
# Do not use `set -euo pipefail` at file scope when this script is **sourced**: it would
# apply to your interactive shell and exit the whole session on the next failing command.

# Capture this script's directory at source time, portably across bash and zsh.
if [ -n "${ZSH_VERSION:-}" ]; then
  # In zsh, ${(%):-%N} expands to the current script path even when sourced.
  _SB_AUTH_PROFILE_SELF="${(%):-%N}"
elif [ -n "${BASH_SOURCE:-}" ]; then
  # shellcheck disable=SC3054
  _SB_AUTH_PROFILE_SELF="${BASH_SOURCE[0]}"
else
  _SB_AUTH_PROFILE_SELF="$0"
fi
_SB_AUTH_PROFILE_DIR=$(cd -- "$(dirname -- "$_SB_AUTH_PROFILE_SELF")" >/dev/null 2>&1 && pwd)
unset _SB_AUTH_PROFILE_SELF

# Fallback profiles root is resolved on every call (not cached at source time),
# so `unset SUPERBLOCKS_PROFILES_DIR` takes effect immediately without re-sourcing.
_sb_auth_profile_root() {
  printf '%s' "${SUPERBLOCKS_PROFILES_DIR:-$HOME/.superblocks/profiles}"
}

# Locate the repo .env: prefer the one next to scripts/, fall back to $PWD/.env.
_sb_auth_profile_env_file() {
  local candidate
  if [ -n "$_SB_AUTH_PROFILE_DIR" ]; then
    candidate=$(cd -- "$_SB_AUTH_PROFILE_DIR/.." 2>/dev/null && pwd)/.env
    if [ -f "$candidate" ]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  fi
  if [ -f "$PWD/.env" ]; then
    printf '%s\n' "$PWD/.env"
    return 0
  fi
  printf '%s\n' ""
}

# Expand $HOME / ${HOME} / leading ~ in a value. Echoes the result.
_sb_auth_profile_expand() {
  local val="$1"
  val=${val//\$\{HOME\}/$HOME}
  val=${val//\$HOME/$HOME}
  case "$val" in
    "~"*) val="$HOME${val:1}" ;;
  esac
  printf '%s' "$val"
}

# Read one key from a .env file (KEY=value or KEY="value"); echo the expanded value or empty.
_sb_auth_profile_read_env() {
  local file="$1" key="$2"
  [ -z "$file" ] || [ ! -f "$file" ] && return 0
  local line val
  line=$(grep -E "^[[:space:]]*${key}[[:space:]]*=" "$file" | tail -n1)
  [ -z "$line" ] && return 0
  val=${line#*=}
  # Trim leading/trailing whitespace.
  val="${val#"${val%%[![:space:]]*}"}"
  val="${val%"${val##*[![:space:]]}"}"
  # Strip surrounding quotes (zsh-safe: arithmetic done explicitly with $(( … ))).
  if [ ${#val} -ge 2 ]; then
    local first="${val:0:1}" last="${val: -1}" inner_len
    if [ "$first" = "$last" ] && { [ "$first" = '"' ] || [ "$first" = "'" ]; }; then
      inner_len=$(( ${#val} - 2 ))
      val="${val:1:$inner_len}"
    fi
  fi
  _sb_auth_profile_expand "$val"
}

# Echo "<path>\t<source>" for a profile. <source>: shell:VAR, dotenv:VAR, or fallback.
_sb_auth_profile_resolve() {
  local name="$1" env_file="$2"
  local env_key="" shell_val="" env_val=""
  case "$name" in
    cloud)
      env_key=SUPERBLOCKS_CLOUD_AUTH_FILE
      shell_val="${SUPERBLOCKS_CLOUD_AUTH_FILE:-}"
      ;;
    cloud_prem|cloud-prem)
      env_key=SUPERBLOCKS_CLOUD_PREM_AUTH_FILE
      shell_val="${SUPERBLOCKS_CLOUD_PREM_AUTH_FILE:-}"
      ;;
  esac

  if [ -n "$shell_val" ]; then
    printf '%s\t%s\n' "$(_sb_auth_profile_expand "$shell_val")" "shell:$env_key"
    return 0
  fi
  if [ -n "$env_key" ]; then
    env_val=$(_sb_auth_profile_read_env "$env_file" "$env_key")
    if [ -n "$env_val" ]; then
      printf '%s\t%s\n' "$env_val" "dotenv:$env_key"
      return 0
    fi
  fi
  printf '%s\t%s\n' "$(_sb_auth_profile_root)/$name/auth.json" "fallback"
}

sb_auth_profile() {
  local name="${1:-}"
  if [ -z "$name" ]; then
    echo "usage: sb_auth_profile <profile-name|list|ls>" >&2
    echo "  sb_auth_profile list   — print profiles and auth.json paths (same as ls)" >&2
    echo "  sb_auth_profile cloud | cloud_prem  — export SUPERBLOCKS_AUTH_FILE for that profile" >&2
    return 1
  fi

  local env_file
  env_file=$(_sb_auth_profile_env_file)

  if [ "$name" = "list" ] || [ "$name" = "ls" ]; then
    echo "Superblocks CLI auth profiles"
    if [ -n "$env_file" ]; then
      echo "shared .env: $env_file"
    else
      echo "shared .env: (not found — create one at the repo root)"
    fi
    local active="${SUPERBLOCKS_AUTH_FILE:-}"
    # NOTE: do not name a local var "path" — zsh ties lowercase `path` to $PATH (array).
    local any_fallback=0 label resolved auth_path origin

    for label in cloud cloud_prem; do
      resolved=$(_sb_auth_profile_resolve "$label" "$env_file")
      auth_path="${resolved%	*}"    # drop everything from the literal tab onward
      origin="${resolved##*	}"      # last field after the tab
      printf '  %-12s %s' "$label" "$auth_path"
      case "$origin" in
        shell:*)  printf '  [from $%s]' "${origin#shell:}" ;;
        dotenv:*) printf '  [from .env: %s]' "${origin#dotenv:}" ;;
        fallback) printf '  [fallback path — no .env entry]'; any_fallback=1 ;;
      esac
      if [ -f "$auth_path" ]; then
        printf '  [auth.json present]'
      else
        printf '  [no auth.json yet]'
      fi
      if [ -n "$active" ] && [ "$active" = "$auth_path" ]; then
        printf '  (active)'
      fi
      printf '\n'
    done

    if [ "$any_fallback" -eq 1 ]; then
      echo "fallback profiles root: $(_sb_auth_profile_root)"
      echo "  (used only when no .env entry or shell variable is set;"
      echo "   override with SUPERBLOCKS_PROFILES_DIR; unset it to revert to \$HOME/.superblocks/profiles)"
    fi
    if [ -z "$active" ]; then
      echo "SUPERBLOCKS_AUTH_FILE is unset in this shell."
    else
      echo "SUPERBLOCKS_AUTH_FILE=$active"
    fi
    return 0
  fi

  local resolved target
  resolved=$(_sb_auth_profile_resolve "$name" "$env_file")
  target="${resolved%	*}"

  mkdir -p "$(dirname "$target")"
  export SUPERBLOCKS_AUTH_FILE="$target"
  echo "SUPERBLOCKS_AUTH_FILE=$SUPERBLOCKS_AUTH_FILE"
}
