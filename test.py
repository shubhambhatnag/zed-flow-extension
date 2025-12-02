import sys
import webbrowser  # Not external
from pathlib import Path

from flowlauncher import FlowLauncher  # external library

plugindir = Path.absolute(Path(__file__).parent)
paths = (".", "lib", "plugin")
sys.path = [str(plugindir / p) for p in paths] + sys.path


class HelloWorld(FlowLauncher):
    def query(self, query):
        return [
            {
                "Title": "Hello World, this is where title goes. {}".format(
                    ("Your query is: " + query, query)[query == ""]
                ),
                "SubTitle": "This is where your subtitle goes, press enter to open Flow's url",
                "IcoPath": "Images/app.png",
                "ContextData": ["foo", "bar"],
                "JsonRPCAction": {
                    "method": "open_url",
                    "parameters": ["https://github.com/Flow-Launcher/Flow.Launcher"],
                },
            }
        ]

    def open_url(self, url):
        webbrowser.open(url)

    def context_menu(self, data):
        return [
            {
                "Title": "Hello World Python's Context menu",
                "SubTitle": "Press enter to open Flow the plugin's repo in GitHub",
                "IcoPath": "Images/app.png",
                "JsonRPCAction": {
                    "method": "open_url",
                    "parameters": [
                        "https://github.com/Flow-Launcher/Flow.Launcher.Plugin.HelloWorldPython"
                    ],
                },
            }
        ]


if __name__ == "__main__":
    HelloWorld()
