import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from flowlauncher import FlowLauncher

# Set up plugin paths
plugindir = Path.absolute(Path(__file__).parent)
paths = (".", "lib", "plugin")
sys.path = [str(plugindir / p) for p in paths] + sys.path


def find_zed_db_path() -> Optional[Path]:
    """Find Zed database path dynamically to support different versions/channels."""
    zed_dir = Path.home() / "AppData/Local/Zed/db"
    if not zed_dir.exists():
        return None

    # Look for any db.sqlite file in subdirectories
    db_files = list(zed_dir.glob("*/db.sqlite"))
    if db_files:
        # Prefer stable, then sort by name
        stable = [f for f in db_files if "stable" in f.parent.name]
        return stable[0] if stable else db_files[0]

    # Fallback to expected path
    return zed_dir / "0-stable/db.sqlite"


ZED_DB_PATH = find_zed_db_path()


def is_wsl_path(p: str) -> bool:
    """Detect whether a path belongs to WSL.

    WSL paths start with / (Unix-style absolute paths) or ~ (home directory).
    Windows paths typically start with drive letters like C:\ or are relative.
    """
    return p.startswith("/") or p.startswith("~")


def normalize(p: str) -> str:
    """Normalize path separators and remove redundant slashes.

    Note: Does NOT convert to lowercase to preserve case-sensitivity for WSL/Linux paths.
    """
    if not isinstance(p, str):
        return ""
    s = p.replace("\\", "/")

    while "//" in s:
        s = s.replace("//", "/")

    if len(s) > 1 and s.endswith("/"):
        s = s.rstrip("/")
    return s


def normalize_for_comparison(p: str) -> str:
    """Normalize path for comparison, converting to lowercase for Windows paths only."""
    normalized = normalize(p)
    # Only lowercase Windows paths (contain drive letters)
    if ":" in normalized or not is_wsl_path(p):
        return normalized.lower()
    return normalized


class WorkspaceCache:
    """Cache for workspace data with TTL and file modification detection."""

    def __init__(self, ttl_seconds: float = 5.0):
        self.ttl_seconds = ttl_seconds
        self._cache: Optional[List[Dict[str, Any]]] = None
        self._cache_time: float = 0
        self._last_mtime: Optional[float] = None

    def get(self, db_path: Path) -> Optional[List[Dict[str, Any]]]:
        """Get cached workspaces if still valid."""
        if self._cache is None:
            return None

        current_time = time.time()

        # Check TTL
        if current_time - self._cache_time > self.ttl_seconds:
            return None

        # Check if database file was modified
        if db_path and db_path.exists():
            try:
                current_mtime = db_path.stat().st_mtime
                if self._last_mtime is not None and current_mtime != self._last_mtime:
                    return None
            except Exception:
                # If we can't check mtime, invalidate cache
                return None

        return self._cache

    def set(self, db_path: Path, workspaces: List[Dict[str, Any]]) -> None:
        """Update cache with new workspace data."""
        self._cache = workspaces
        self._cache_time = time.time()

        if db_path and db_path.exists():
            try:
                self._last_mtime = db_path.stat().st_mtime
            except Exception:
                self._last_mtime = None

    def invalidate(self) -> None:
        """Clear the cache."""
        self._cache = None
        self._cache_time = 0
        self._last_mtime = None


class ZedWorkspaceSearch(FlowLauncher):
    def __init__(self):
        super().__init__()
        self.cache = WorkspaceCache(ttl_seconds=5.0)

    def _load_workspaces(self) -> List[Dict[str, Any]]:
        """Load workspaces from Zed database with caching."""
        if not ZED_DB_PATH or not ZED_DB_PATH.exists():
            return []

        # Check cache first
        cached = self.cache.get(ZED_DB_PATH)
        if cached is not None:
            return cached

        try:
            with sqlite3.connect(str(ZED_DB_PATH), timeout=5.0) as con:
                cur = con.cursor()
                cur.execute("SELECT workspace_id, paths FROM workspaces")
                rows = cur.fetchall()

            by_normalized: Dict[str, Dict[str, Any]] = {}

            for wid, path in rows:
                if not path or not isinstance(path, str):
                    continue

                norm = normalize_for_comparison(path)

                # If we've already seen this normalized path, prefer the shortest original path
                if norm not in by_normalized:
                    by_normalized[norm] = {
                        "id": wid,
                        "path": path,
                        "normalized": norm,
                        "is_wsl": is_wsl_path(path),
                    }
                else:
                    existing = by_normalized[norm]["path"]
                    if len(path) < len(existing):
                        by_normalized[norm] = {
                            "id": wid,
                            "path": path,
                            "normalized": norm,
                            "is_wsl": is_wsl_path(path),
                        }

            results = list(by_normalized.values())

            # Sort results by workspace name
            results.sort(key=lambda r: Path(r["path"]).name.lower() if r["path"] else "")

            # Update cache
            self.cache.set(ZED_DB_PATH, results)

            return results

        except sqlite3.OperationalError as e:
            # Database is locked or inaccessible
            return []
        except Exception as e:
            # Other database errors
            return []

    def query(self, query: str) -> List[Dict[str, Any]]:
        """Query workspaces and return Flow Launcher results."""
        q = query.lower().strip()
        workspaces = self._load_workspaces()

        if not workspaces:
            return [
                {
                    "Title": "No Zed workspaces found",
                    "SubTitle": str(ZED_DB_PATH) if ZED_DB_PATH else "Database not found",
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

        return results

    def open_workspace(self, path: str) -> None:
        """Open workspace in Zed using the appropriate environment."""
        try:
            if is_wsl_path(path):
                # Open inside WSL
                subprocess.Popen(
                    ["wsl", "zed", path],
                    shell=False,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            else:
                # Normal Windows path
                subprocess.Popen(
                    ["zed", path],
                    shell=False,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
        except FileNotFoundError:
            # Zed or WSL command not found
            pass
        except Exception:
            # Other subprocess errors
            pass

    def context_menu(self, data: List[str]) -> List[Dict[str, Any]]:
        """Provide context menu options for workspaces."""
        path = data[0]
        is_wsl = is_wsl_path(path)

        label = "(WSL)" if is_wsl else "(Windows)"

        return [
            {
                "Title": f"Open in Zed {label}",
                "SubTitle": path,
                "IcoPath": "assets/zed.png",
                "JsonRPCAction": {
                    "method": "open_workspace",
                    "parameters": [path],
                },
            }
        ]


if __name__ == "__main__":
    ZedWorkspaceSearch()
