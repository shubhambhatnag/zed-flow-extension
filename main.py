import sqlite3
import sys
import webbrowser
from pathlib import Path

from flowlauncher import FlowLauncher

# Set up plugin paths
plugindir = Path.absolute(Path(__file__).parent)
paths = (".", "lib", "plugin")
sys.path = [str(plugindir / p) for p in paths] + sys.path


ZED_DB_PATH = Path.home() / "AppData/Local/Zed/db/0-stable/db.sqlite"


class ZedWorkspaceSearch(FlowLauncher):
    def _load_workspaces(self):
        """Load workspace paths from Zed's SQLite database."""
        if not ZED_DB_PATH.exists():
            return []

        try:
            con = sqlite3.connect(ZED_DB_PATH)
            cur = con.cursor()

            # The column name based on your screenshot is `paths`
            cur.execute("SELECT workspace_id, paths FROM workspaces")

            rows = cur.fetchall()
            con.close()

            results = []
            for wid, path in rows:
                if path and isinstance(path, str):
                    results.append({"id": wid, "path": path})

            return results

        except Exception as e:
            return [{"id": -1, "path": f"<Error reading DB: {e}>"}]

    def query(self, query):
        """Respond to user search requests."""
        q = query.lower().strip()

        workspaces = self._load_workspaces()

        if not workspaces:
            return [
                {
                    "Title": "No Zed workspaces found",
                    "SubTitle": str(ZED_DB_PATH),
                    "IcoPath": "Images/app.png",
                }
            ]

        # Filter results
        filtered = (
            [w for w in workspaces if q in w["path"].lower()] if q else workspaces
        )

        results = []
        for w in filtered:
            results.append(
                {
                    "Title": Path(w["path"]).name,
                    "SubTitle": w["path"],
                    "IcoPath": "Images/app.png",
                    "JsonRPCAction": {
                        "method": "open_workspace",
                        "parameters": [w["path"]],
                    },
                    "ContextData": [w["path"]],
                }
            )

        return results

    def open_workspace(self, path):
        """Open folder in Explorer when pressing ENTER."""
        path = Path(path)
        if path.exists():
            # Open Zed directly?
            # os.system(f'zed "{path}"')     # if zed.exe is in PATH
            webbrowser.open(path.as_uri())
        else:
            webbrowser.open("file:///")  # fallback

    def context_menu(self, data):
        """Right-click menu: Open in Zed."""
        path = data[0]
        return [
            {
                "Title": f"Open '{Path(path).name}' in Zed",
                "SubTitle": path,
                "IcoPath": "Images/app.png",
                "JsonRPCAction": {
                    "method": "open_in_zed",
                    "parameters": [path],
                },
            }
        ]

    def open_in_zed(self, path):
        """Open workspace directly in Zed."""
        import subprocess

        subprocess.Popen(["zed", path], shell=True)


if __name__ == "__main__":
    ZedWorkspaceSearch()
