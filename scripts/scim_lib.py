"""
Shared HTTP helpers for Superblocks SCIM 2.0 bulk scripts.

Base URL is the Superblocks deployment origin (same as superblocksBaseUrl in auth.json),
for example https://app.superblocks.com or https://<company>.superblocks.com.

SCIM path prefix: /scim/v2

Authentication: /scim/v2 always uses its own bearer token (Organization access token from
Superblocks org settings for SCIM / org admin). It is not the Cursor MCP server Bearer or the
CLI auth.json session token; those are for product APIs/MCP and often return HTTP 401 on SCIM.
See README.md "SCIM access tokens (separate from MCP)".

Shared .env: scripts also read base URLs and CLI auth-file paths from the same .env so the
MCP servers, CLI, and these scripts can be driven by one file. Recognized keys:

  SUPERBLOCKS_CLOUD_BASE_URL              # https://app.superblocks.com (or regional SaaS)
  SUPERBLOCKS_CLOUD_PREM_BASE_URL         # https://<company>.superblocks.com
  SUPERBLOCKS_CLOUD_AUTH_FILE             # absolute path used by CLI + MCP (Cloud)
  SUPERBLOCKS_CLOUD_PREM_AUTH_FILE        # absolute path used by CLI + MCP (Cloud Prem)
  SUPERBLOCKS_SCIM_TOKEN_CLOUD            # SCIM token for Cloud (required for Cloud-side SCIM calls)
  SUPERBLOCKS_SCIM_TOKEN_CLOUD_PREM       # SCIM token for Cloud Prem (required for Cloud Prem SCIM)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

# Repository root (parent of scripts/) for default .env discovery.
REPO_ROOT = Path(__file__).resolve().parent.parent

# Canonical migration artifact paths. The Phase 1 scripts read/write these exact files —
# they are not user-overridable so the README, SKILL, and tooling can refer to one location.
ARTIFACTS_DIR = REPO_ROOT / "migration-artifacts"
USER_GAP_CSV = ARTIFACTS_DIR / "cloud-prem-user-gap-selection.csv"
GROUP_GAP_CSV = ARTIFACTS_DIR / "cloud-prem-group-gap-selection.csv"
GROUP_MEMBERSHIP_GAP_CSV = ARTIFACTS_DIR / "cloud-prem-group-membership-gap-selection.csv"

# Names this loader will copy from .env into os.environ. Anything else is ignored.
_DOTENV_KEYS = frozenset(
    {
        "SUPERBLOCKS_SCIM_TOKEN_CLOUD",
        "SUPERBLOCKS_SCIM_TOKEN_CLOUD_PREM",
        "SUPERBLOCKS_CLOUD_BASE_URL",
        "SUPERBLOCKS_CLOUD_PREM_BASE_URL",
        "SUPERBLOCKS_CLOUD_AUTH_FILE",
        "SUPERBLOCKS_CLOUD_PREM_AUTH_FILE",
    }
)

SCIM_PREFIX = "/scim/v2"


def scim_url(base: str, path: str) -> str:
    b = base.rstrip("/")
    p = path if path.startswith("/") else f"/{path}"
    return f"{b}{SCIM_PREFIX}{p}"


def scim_request(
    base: str,
    token: str,
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    timeout: int = 120,
) -> tuple[int, Any]:
    url = scim_url(base, path)
    data = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/scim+json, application/json",
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/scim+json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
            raw = resp.read().decode()
            code = resp.getcode()
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        code = e.code
        try:
            parsed: Any = json.loads(raw) if raw.strip() else None
        except json.JSONDecodeError:
            parsed = raw
        raise ScimHttpError(code, url, parsed) from e

    if not raw.strip():
        return code, None
    try:
        return code, json.loads(raw)
    except json.JSONDecodeError:
        return code, raw


class ScimHttpError(Exception):
    def __init__(self, code: int, url: str, body: Any) -> None:
        self.code = code
        self.url = url
        self.body = body
        super().__init__(f"HTTP {code} {url}: {body!r}")


def scim_list_all(
    base: str,
    token: str,
    resource_path: str,
    *,
    page_size: int = 100,
    sleep_s: float = 0.0,
) -> list[dict[str, Any]]:
    """GET {resource_path} with SCIM pagination (startIndex 1-based)."""

    rp = resource_path if resource_path.startswith("/") else f"/{resource_path}"
    out: list[dict[str, Any]] = []
    start = 1
    while True:
        path = f"{rp}?startIndex={start}&count={page_size}"
        _, payload = scim_request(base, token, "GET", path)
        if not isinstance(payload, dict):
            break
        resources = payload.get("Resources") or []
        if not isinstance(resources, list) or not resources:
            break
        for r in resources:
            if isinstance(r, dict):
                out.append(r)
        if len(resources) < page_size:
            break
        start += len(resources)
        if sleep_s:
            time.sleep(sleep_s)
    return out


def scim_get_first_by_filter(
    base: str,
    token: str,
    resource: str,
    filter_expr: str,
) -> dict[str, Any] | None:
    enc = quote(filter_expr, safe="")
    path = f"/{resource}?filter={enc}&count=1"
    try:
        _, payload = scim_request(base, token, "GET", path)
    except ScimHttpError:
        return None
    if not isinstance(payload, dict):
        return None
    resources = payload.get("Resources") or []
    if isinstance(resources, list) and resources and isinstance(resources[0], dict):
        return resources[0]
    return None


def normalize_email(s: str) -> str:
    return (s or "").strip().lower()


def user_primary_email(u: dict[str, Any]) -> str:
    """
    Best-effort primary email for a SCIM user resource. Prefers an email entry marked
    primary=true, then any email with a value, then userName (which is typically the
    email on Superblocks-style tenants). Returns "" if nothing usable is present.
    """
    emails = u.get("emails")
    if isinstance(emails, list):
        primary = next(
            (e for e in emails if isinstance(e, dict) and e.get("primary") and e.get("value")),
            None,
        )
        if primary:
            return str(primary["value"]).strip()
        for e in emails:
            if isinstance(e, dict) and e.get("value"):
                return str(e["value"]).strip()
    return str(u.get("userName") or "").strip()


def split_display_name(display: str) -> tuple[str, str]:
    d = (display or "").strip()
    if not d:
        return "User", "."
    parts = d.split(None, 1)
    if len(parts) == 1:
        return parts[0], "."
    return parts[0], parts[1]


SCIM_CORE_USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
# Superblocks org-level role: JSON property `role` on this extension object (not top-level).
SCIM_ENTERPRISE_USER_SCHEMA = "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User"

# Superblocks SCIM org role values (see docs: Okta / Entra SCIM role attribute).
_SCIM_ORG_ROLES = frozenset({"owner", "admin", "developer", "end_user"})


def cloud_org_role_to_scim_role(role: str | None) -> str | None:
    """
    Map a Cloud/UI org role label or SCIM role key to the lowercase SCIM value
    sent in urn:ietf:params:scim:schemas:extension:enterprise:2.0:User role.

    Known built-ins normalize to owner | admin | developer | end_user.
    Other non-empty slugs (a-z, digits, underscore) are returned as-is for custom org roles.
    """
    if not role:
        return None
    raw = str(role).strip()
    if not raw:
        return None
    key = raw.lower().replace(" ", "_").replace("-", "_")
    synonyms = {
        "owner": "owner",
        "admin": "admin",
        "developer": "developer",
        "end_user": "end_user",
        "enduser": "end_user",
        "org_admin": "admin",
        "organization_admin": "admin",
        "org_owner": "owner",
        "organization_owner": "owner",
    }
    if key in synonyms:
        return synonyms[key]
    if key in _SCIM_ORG_ROLES:
        return key
    # Custom org role keys from Superblocks (Roles UI) use stable lowercase identifiers.
    if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key):
        return key
    return None


def csv_row_included_for_provision(row: dict[str, str], mode: str) -> bool:
    """
    mode:
      - recommended: row has recommended_action=provision_on_cloud_prem OR migrate_to_cloud_prem truthy
      - all: any row with email
    """
    email = normalize_email(row.get("email") or "")
    if not email:
        return False
    if mode == "all":
        return True
    rec = (row.get("recommended_action") or "").strip().lower()
    if rec == "provision_on_cloud_prem":
        return True
    mig = (row.get("migrate_to_cloud_prem") or "").strip().lower()
    if mig in ("y", "yes", "true", "1", "x"):
        return True
    return False


def csv_row_included_for_group_provision(row: dict[str, str], mode: str) -> bool:
    """
    Same selection rules as csv_row_included_for_provision, but requires display_name
    (or displayName) instead of email.
    """
    name = (row.get("display_name") or row.get("displayName") or "").strip()
    if not name:
        return False
    if mode == "all":
        return True
    rec = (row.get("recommended_action") or "").strip().lower()
    if rec == "provision_on_cloud_prem":
        return True
    mig = (row.get("migrate_to_cloud_prem") or "").strip().lower()
    if mig in ("y", "yes", "true", "1", "x"):
        return True
    return False


_SCIM_TOKEN_README = 'README.md section "SCIM access tokens (separate from MCP)".'


def parse_dotenv_file(path: Path) -> dict[str, str]:
    """Parse a .env file; return only the keys recognized by this loader."""
    text = path.read_text(encoding="utf-8")
    out: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, rest = line.partition("=")
        key = key.strip()
        if key not in _DOTENV_KEYS:
            continue
        val = rest.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        out[key] = val
    return out


# Back-compat alias for older imports.
parse_scim_dotenv_file = parse_dotenv_file


def load_env_dotenv(
    explicit: Path | str | None = None,
    *,
    skip: bool = False,
    override: bool = False,
) -> list[Path]:
    """
    Load Superblocks shared config (SCIM tokens, base URLs, auth file paths) from .env into
    os.environ before resolution.

    Discovery when explicit is None: ./.env then <repo>/.env (repo = parent of scripts/).
    Existing non-empty process environment variables are not replaced unless override=True.
    Set SUPERBLOCKS_SCIM_NO_DOTENV=1 (or pass skip=True / CLI --no-dotenv) to disable.
    """
    if skip:
        return []
    if os.environ.get("SUPERBLOCKS_SCIM_NO_DOTENV", "").strip().lower() in ("1", "true", "yes"):
        return []

    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise SystemExit(f".env file not found: {path}")
        paths = (path,)
    else:
        paths = (Path.cwd() / ".env", REPO_ROOT / ".env")

    loaded: list[Path] = []
    for path in paths:
        if not path.is_file():
            continue
        for key, value in parse_dotenv_file(path).items():
            if not override:
                existing = (os.environ.get(key) or "").strip()
                if existing:
                    continue
            os.environ[key] = value
        loaded.append(path)
    return loaded


# Back-compat alias.
load_scim_env_dotenv = load_env_dotenv


def add_scim_dotenv_cli_args(ap: argparse.ArgumentParser) -> None:
    """Register --dotenv / --no-dotenv; call apply_scim_dotenv_cli_args after parse_args."""
    ap.add_argument(
        "--dotenv",
        type=Path,
        default=None,
        metavar="PATH",
        help="Load Superblocks shared config (SCIM tokens, base URLs, auth-file paths) from "
        "this .env file. If omitted, tries ./.env then repository .env.",
    )
    ap.add_argument(
        "--no-dotenv",
        action="store_true",
        help="Do not load Superblocks shared config from .env files.",
    )


def apply_scim_dotenv_cli_args(args: argparse.Namespace) -> list[Path]:
    """Use with add_scim_dotenv_cli_args: load .env after argparse."""
    return load_env_dotenv(args.dotenv, skip=args.no_dotenv)


def expanduser_str(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip()
    if not v:
        return None
    return str(Path(os.path.expandvars(v)).expanduser())


def resolve_base_url(
    explicit: str | None,
    env_name: str,
    *,
    label: str,
    fallback: Callable[[], str | None] | None = None,
) -> str:
    """
    Pick the Superblocks origin for one side (Cloud or Cloud Prem).

    Order: explicit CLI value → env_name → fallback (e.g. auth.json or MCP json) → SystemExit.
    """
    if explicit:
        return explicit.rstrip("/")
    env_val = (os.environ.get(env_name) or "").strip()
    if env_val:
        return env_val.rstrip("/")
    if fallback is not None:
        got = fallback()
        if got:
            return got.rstrip("/")
    raise SystemExit(
        f"Missing {label} base URL: set {env_name} (e.g. via ./.env or repository .env), "
        "or pass an explicit CLI override."
    )


def resolve_auth_file(
    explicit: str | os.PathLike[str] | None,
    env_name: str,
    *,
    default: Path | None = None,
) -> Path | None:
    """Resolve an absolute path to a CLI auth.json (explicit CLI arg → env var → default)."""
    if explicit:
        return Path(os.path.expandvars(str(explicit))).expanduser()
    env_val = expanduser_str(os.environ.get(env_name))
    if env_val:
        return Path(env_val)
    return default


def read_token(
    path: str | None,
    env: str | tuple[str, ...] = "SUPERBLOCKS_SCIM_TOKEN_CLOUD_PREM",
) -> str:
    """
    Resolve the SCIM Bearer token for /scim/v2 requests.

    env may be a single variable name or a tuple of names tried in order (first wins).
    Tokens are host-specific: use SUPERBLOCKS_SCIM_TOKEN_CLOUD for Cloud calls and
    SUPERBLOCKS_SCIM_TOKEN_CLOUD_PREM for Cloud Prem calls (issue separate org SCIM tokens
    in each deployment's org settings).
    """
    env_names: tuple[str, ...] = (env,) if isinstance(env, str) else env

    if path:
        t = Path(path).read_text().strip()
        if not t:
            raise SystemExit(f"Empty token file: {path}")
        return t
    for name in env_names:
        t = (os.environ.get(name) or "").strip()
        if t:
            return t
    listed = ", ".join(env_names)
    raise SystemExit(
        "Missing SCIM bearer token for /scim/v2: pass --token or --token-file, or set one of: "
        f"{listed}, "
        "or define them in a .env file (./.env or repository .env, or pass --dotenv PATH). "
        "Create an Organization access token in Superblocks org settings (SCIM-capable); "
        "do not use the MCP server Bearer or expect CLI auth.json to work. "
        f"See {_SCIM_TOKEN_README}"
    )


def load_base_url_cloud_prem_auth(path: str) -> str:
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    return str(data["superblocksBaseUrl"]).rstrip("/")


def load_mcp_remote_base(mcp_json: str, server_key: str) -> str:
    cfg = json.loads(Path(mcp_json).read_text())
    srv = cfg.get("mcpServers", {}).get(server_key)
    if not srv:
        raise SystemExit(f"No mcpServers.{server_key} in {mcp_json}")
    url = str(srv.get("url") or "")
    if "/mcp" not in url:
        raise SystemExit(f"Expected MCP url containing /mcp for {server_key}")
    return url.split("/mcp")[0].rstrip("/")


def run_with_backoff(
    fn: Callable[[], Any],
    *,
    retries: int = 3,
    base_delay_s: float = 1.5,
) -> Any:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            return fn()
        except ScimHttpError as e:
            last = e
            if e.code not in (429, 502, 503, 504) or attempt == retries - 1:
                raise
        except OSError as e:
            last = e
            if attempt == retries - 1:
                raise
        time.sleep(base_delay_s * (2**attempt))
    assert last is not None
    raise last
