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

ZED_DB_PATH = Path.home() / "AppData/Local/Zed/db/0-stable/db.sqlite"


def is_wsl_path(p: str) -> bool:
    """Detect whether a path belongs to WSL."""
    return p.startswith("/home/") or p.startswith("/mnt/")


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

            results = []
            for wid, path in rows:
                if path and isinstance(path, str):
                    results.append(
                        {
                            "id": wid,
                            "path": path,
                            "is_wsl": is_wsl_path(path),
                        }
                    )

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
                    "IcoPath": "Images/zed.png",
                }
            ]

        filtered = (
            [w for w in workspaces if q in w["path"].lower()] if q else workspaces
        )

        results = []
        for w in filtered:
            name = Path(w["path"]).name

            # Label WSL workspaces clearly
            if w["is_wsl"]:
                title = f"{name}  (WSL)"
            else:
                title = name

            results.append(
                {
                    "Title": title,
                    "SubTitle": w["path"],
                    "IcoPath": "Images/zed.png",
                    "JsonRPCAction": {
                        "method": "open_workspace",
                        "parameters": [w["path"]],
                    },
                    "ContextData": [w["path"]],
                }
            )

        return results

    def open_workspace(self, path):
        """Open workspace respecting Windows vs WSL."""

        if is_wsl_path(path):
            # Open inside WSL
            subprocess.Popen(["wsl", "zed", path], shell=False)
            return

        # Normal Windows path
        p = Path(path)
        if p.exists():
            subprocess.Popen(["zed", str(p)], shell=False)
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
                "IcoPath": "Images/zed.png",
                "JsonRPCAction": {
                    "method": "open_in_zed",
                    "parameters": [path],
                },
            }
        ]

    def open_in_zed(self, path):
        """Open workspace using the appropriate environment."""
        if is_wsl_path(path):
            subprocess.Popen(["wsl", "zed", path], shell=False)
        else:
            subprocess.Popen(["zed", path], shell=False)


if __name__ == "__main__":
    ZedWorkspaceSearch()
