import json
import sqlite3
import subprocess
import sys
import webbrowser
from pathlib import Path

from flowlauncher import FlowLauncher

# Set up plugin paths
plugindir = Path.absolute(Path(__file__).parent)
paths = (".", "lib", "plugin")
sys.path = [str(plugindir / p) for p in paths] + sys.path

RECENTS_FILE = plugindir / "last_opened.json"
MAX_RECENTS = 100


def is_wsl_path(p: str) -> bool:
    """Detect whether a path belongs to WSL (only when not an SSH remote)."""
    return p.startswith("/home/") or p.startswith("/mnt/")


def build_ssh_uri(
    host: str, path: str, user: str | None = None, port: int | None = None
) -> str:
    """Build SSH URI for Zed: ssh://[user@]host[:port]/path"""
    uri = "ssh://"
    if user:
        uri += f"{user}@"
    uri += host
    if port:
        uri += f":{port}"
    uri += path
    return uri


def normalize(p: str) -> str:
    if not isinstance(p, str):
        return ""
    s = p.replace("\\", "/")

    while "//" in s:
        s = s.replace("//", "/")

    if len(s) > 1 and s.endswith("/"):
        s = s.rstrip("/")
    return s.lower()


def recent_key(path, is_ssh=False, ssh_host=None, ssh_user=None, ssh_port=None) -> str:
    parts = [normalize(path), str(bool(is_ssh)).lower(), str(ssh_host or ""), str(ssh_user or ""), str(ssh_port or "")]
    return "|".join(parts)


class ZedWorkspaceSearch(FlowLauncher):
    def __init__(self):
        # Parse settings from the JSON-RPC request before FlowLauncher.__init__
        # calls query(), since the base class doesn't expose settings
        self._plugin_settings = {}
        if len(sys.argv) > 1:
            from json import loads

            try:
                rpc = loads(sys.argv[1])
                self._plugin_settings = rpc.get("settings", {})
            except Exception:
                pass
        super().__init__()

    def _get_db_path(self):
        default = "AppData/Local/Zed/db/0-stable/db.sqlite"
        return Path.home() / self._plugin_settings.get("dbFilePath", default)

    def _load_workspaces(self):
        db_path = self._get_db_path()
        if not db_path.exists():
            return []

        try:
            con = sqlite3.connect(db_path)
            cur = con.cursor()

            # JOIN with remote_connections to get SSH info
            cur.execute("""
                SELECT
                    w.workspace_id,
                    w.paths,
                    rc.kind,
                    rc.host,
                    rc.port,
                    rc.user
                FROM workspaces w
                LEFT JOIN remote_connections rc ON w.remote_connection_id = rc.id
            """)
            rows = cur.fetchall()
            con.close()

            by_normalized = {}
            by_workspace_id = {}

            for wid, path, rc_kind, rc_host, rc_port, rc_user in rows:
                if not path or not isinstance(path, str):
                    continue

                norm = normalize(path)

                # Determine connection type
                is_ssh = rc_kind == "ssh" and rc_host is not None
                # Only consider WSL if NOT an SSH remote
                is_wsl = not is_ssh and is_wsl_path(path)

                workspace_data = {
                    "id": wid,
                    "path": path,
                    "normalized": norm,
                    "is_wsl": is_wsl,
                    "is_ssh": is_ssh,
                    "ssh_host": rc_host if is_ssh else None,
                    "ssh_user": rc_user if is_ssh else None,
                    "ssh_port": rc_port if is_ssh else None,
                }

                # If we've already seen this normalized path, prefer the earliest record
                if norm not in by_normalized:
                    by_normalized[norm] = workspace_data
                else:
                    existing = by_normalized[norm]["path"]
                    if len(path) < len(existing):
                        by_normalized[norm] = workspace_data

                # track shortest path per workspace_id as fallback
                if wid not in by_workspace_id or len(path) < len(
                    by_workspace_id[wid]["path"]
                ):
                    by_workspace_id[wid] = workspace_data

            # Prefer the normalized-set
            results = (
                list(by_normalized.values())
                if by_normalized
                else list(by_workspace_id.values())
            )

            # Sort results
            results.sort(key=lambda r: Path(r["path"]).name.lower())

            return results

        except Exception as e:
            return [
                {
                    "id": -1,
                    "path": f"<Error reading DB: {e}>",
                    "is_wsl": False,
                    "is_ssh": False,
                }
            ]

    def _load_recents(self):
        try:
            data = json.loads(RECENTS_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return [item for item in data if isinstance(item, str)] if isinstance(data, list) else []

    def _add_recent(self, path, is_ssh=False, ssh_host=None, ssh_user=None, ssh_port=None):
        key = recent_key(path, is_ssh, ssh_host, ssh_user, ssh_port)
        recents = [item for item in self._load_recents() if item != key]
        recents.append(key)
        try:
            RECENTS_FILE.write_text(json.dumps(recents[-MAX_RECENTS:], indent=2), encoding="utf-8")
        except OSError:
            pass

    def _recent_score(self, path, is_ssh=False, ssh_host=None, ssh_user=None, ssh_port=None):
        recents = self._load_recents()
        key = recent_key(path, is_ssh, ssh_host, ssh_user, ssh_port)
        return (recents.index(key) + 1) * 1000 if key in recents else 0

    def query(self, query):
        q = query.lower().strip()
        workspaces = self._load_workspaces()

        if not workspaces:
            return [
                {
                    "Title": "No Zed workspaces found",
                    "SubTitle": str(self._get_db_path()),
                    "IcoPath": "assets/zed.png",
                }
            ]

        filtered = (
            [w for w in workspaces if q in w["path"].lower()] if q else workspaces
        )

        results = []
        for w in filtered:
            # Use the path's last part for name, capitalize it
            try:
                name = Path(w["path"]).name or w["path"]
                # Capitalize the project name
                name = name.capitalize() if name else name
            except Exception:
                name = w["path"]

            # Label SSH and WSL workspaces clearly
            if w.get("is_ssh"):
                ssh_host = w.get("ssh_host", "remote")
                title = f"{name} (SSH: {ssh_host})"
            elif w.get("is_wsl"):
                title = f"{name} (WSL)"
            else:
                title = name

            results.append(
                {
                    "Title": title,
                    "SubTitle": w["path"],
                    "IcoPath": "assets/zed.png",
                    "Score": self._recent_score(
                        w["path"],
                        w.get("is_ssh", False),
                        w.get("ssh_host"),
                        w.get("ssh_user"),
                        w.get("ssh_port"),
                    ),
                    "JsonRPCAction": {
                        "method": "open_workspace",
                        "parameters": [
                            w["path"],
                            w.get("is_ssh", False),
                            w.get("ssh_host"),
                            w.get("ssh_user"),
                            w.get("ssh_port"),
                        ],
                    },
                    "ContextData": [
                        w["path"],
                        w.get("is_ssh", False),
                        w.get("ssh_host"),
                        w.get("ssh_user"),
                        w.get("ssh_port"),
                    ],
                }
            )

        unique = []
        seen = set()
        for r in results:
            key = (r["Title"].strip().lower(), r["SubTitle"].strip().lower())
            if key not in seen:
                seen.add(key)
                unique.append(r)

        return sorted(unique, key=lambda r: r.get("Score", 0), reverse=True)

    def open_workspace(
        self, path, is_ssh=False, ssh_host=None, ssh_user=None, ssh_port=None
    ):
        self._add_recent(path, is_ssh, ssh_host, ssh_user, ssh_port)

        # SSH remote - use zed ssh://[user@]host[:port]/path
        if is_ssh and ssh_host:
            ssh_uri = build_ssh_uri(ssh_host, path, ssh_user, ssh_port)
            subprocess.Popen(
                ["zed", ssh_uri],
                shell=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return

        if is_wsl_path(path):
            # Open inside WSL
            subprocess.Popen(
                ["wsl", "zed", path],
                shell=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return

        # Normal Windows path
        p = Path(path)
        if p.exists():
            subprocess.Popen(
                ["zed", str(p)], shell=False, creationflags=subprocess.CREATE_NO_WINDOW
            )
        else:
            webbrowser.open("file:///")

    def context_menu(self, data):
        path = data[0]
        is_ssh = data[1] if len(data) > 1 else False
        ssh_host = data[2] if len(data) > 2 else None
        ssh_user = data[3] if len(data) > 3 else None
        ssh_port = data[4] if len(data) > 4 else None

        if is_ssh:
            label = f"(SSH: {ssh_host})"
        elif is_wsl_path(path):
            label = "(WSL)"
        else:
            label = "(Windows)"

        return [
            {
                "Title": f"Open in Zed {label}",
                "SubTitle": path,
                "IcoPath": "assets/zed.png",
                "JsonRPCAction": {
                    "method": "open_in_zed",
                    "parameters": [path, is_ssh, ssh_host, ssh_user, ssh_port],
                },
            }
        ]

    def open_in_zed(
        self, path, is_ssh=False, ssh_host=None, ssh_user=None, ssh_port=None
    ):
        """Open workspace using the appropriate environment."""
        self._add_recent(path, is_ssh, ssh_host, ssh_user, ssh_port)

        # SSH remote
        if is_ssh and ssh_host:
            ssh_uri = build_ssh_uri(ssh_host, path, ssh_user, ssh_port)
            subprocess.Popen(
                ["zed", ssh_uri],
                shell=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return

        if is_wsl_path(path):
            subprocess.Popen(
                ["wsl", "zed", path],
                shell=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            subprocess.Popen(
                ["zed", path], shell=False, creationflags=subprocess.CREATE_NO_WINDOW
            )


if __name__ == "__main__":
    ZedWorkspaceSearch()
