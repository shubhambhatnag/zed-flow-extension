import sqlite3
import subprocess
import sys
import webbrowser
from pathlib import Path, PurePosixPath

from flowlauncher import FlowLauncher

# Set up plugin paths
plugindir = Path.absolute(Path(__file__).parent)
paths = (".", "lib", "plugin")
sys.path = [str(plugindir / p) for p in paths] + sys.path

ZED_DB_PATH = Path.home() / "AppData/Local/Zed/db/0-stable/db.sqlite"


def is_wsl_path(p: str) -> bool:
    """Detect whether a path belongs to WSL."""
    return p.startswith("/home/") or p.startswith("/mnt/")


def normalize_path_for_dedupe(p: str) -> str:
    """
    Normalize paths so small differences don't create duplicates:
    - convert backslashes to slashes
    - remove trailing slashes
    - collapse duplicate slashes
    - lowercase for stable comparison
    NOTE: We do NOT call Path.resolve() because the path may not exist.
    """
    if not isinstance(p, str):
        return ""
    s = p.replace("\\", "/")
    # collapse multiple slashes
    while "//" in s:
        s = s.replace("//", "/")
    # strip trailing slash (but keep root '/')
    if len(s) > 1 and s.endswith("/"):
        s = s.rstrip("/")
    return s.lower()


class ZedWorkspaceSearch(FlowLauncher):
    def _load_workspaces(self):
        if not ZED_DB_PATH.exists():
            return []

        try:
            con = sqlite3.connect(ZED_DB_PATH)
            cur = con.cursor()

            cur.execute("SELECT workspace_id, paths FROM workspaces")
            rows = cur.fetchall()
            con.close()

            # Deduplicate by normalized path first (most robust)
            by_normalized = {}
            # Also keep a fallback by workspace_id (shortest path)
            by_workspace_id = {}

            for wid, path in rows:
                if not path or not isinstance(path, str):
                    continue

                norm = normalize_path_for_dedupe(path)

                # If we've already seen this normalized path, prefer the earliest record
                if norm not in by_normalized:
                    by_normalized[norm] = {
                        "id": wid,
                        "path": path,
                        "normalized": norm,
                        "is_wsl": is_wsl_path(path),
                    }
                else:
                    # keep the shortest 'path' string for readability (prefer root-like)
                    existing = by_normalized[norm]["path"]
                    if len(path) < len(existing):
                        by_normalized[norm] = {
                            "id": wid,
                            "path": path,
                            "normalized": norm,
                            "is_wsl": is_wsl_path(path),
                        }

                # track shortest path per workspace_id as fallback
                if wid not in by_workspace_id or len(path) < len(
                    by_workspace_id[wid]["path"]
                ):
                    by_workspace_id[wid] = {
                        "id": wid,
                        "path": path,
                        "normalized": norm,
                        "is_wsl": is_wsl_path(path),
                    }

            # Prefer the normalized-set (unique paths). If normalized list empty, fallback to workspace ids
            results = (
                list(by_normalized.values())
                if by_normalized
                else list(by_workspace_id.values())
            )

            # Sort results by folder name (stable)
            results.sort(key=lambda r: Path(r["path"]).name.lower())

            return results

        except Exception as e:
            return [{"id": -1, "path": f"<Error reading DB: {e}>", "is_wsl": False}]

    def query(self, query):
        q = query.lower().strip()
        workspaces = self._load_workspaces()

        if not workspaces:
            return [
                {
                    "Title": "No Zed workspaces found",
                    "SubTitle": str(ZED_DB_PATH),
                    "IcoPath": "assets/zed.png",
                }
            ]

        filtered = (
            [w for w in workspaces if q in w["path"].lower()] if q else workspaces
        )

        results = []
        for w in filtered:
            # Use the path's last part for name
            try:
                name = Path(w["path"]).name or w["path"]
            except Exception:
                name = w["path"]

            # Label WSL workspaces clearly
            if w["is_wsl"]:
                title = f"{name}  (WSL)"
            else:
                title = name

            results.append(
                {
                    "Title": title,
                    "SubTitle": w["path"],
                    "IcoPath": "assets/zed.png",
                    "JsonRPCAction": {
                        "method": "open_workspace",
                        "parameters": [w["path"]],
                    },
                    "ContextData": [w["path"]],
                }
            )

        # Final defensive dedupe by Title+SubTitle
        unique = []
        seen = set()
        for r in results:
            key = (r["Title"].strip().lower(), r["SubTitle"].strip().lower())
            if key not in seen:
                seen.add(key)
                unique.append(r)

        return unique

    def open_workspace(self, path):
        """Open workspace respecting Windows vs WSL."""

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
        is_wsl = is_wsl_path(path)

        label = "(WSL)" if is_wsl else "(Windows)"

        return [
            {
                "Title": f"Open in Zed {label}",
                "SubTitle": path,
                "IcoPath": "assets/zed.png",
                "JsonRPCAction": {
                    "method": "open_in_zed",
                    "parameters": [path],
                },
            }
        ]

    def open_in_zed(self, path):
        """Open workspace using the appropriate environment."""
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
