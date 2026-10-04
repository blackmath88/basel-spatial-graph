"""Export the existing map UI with an explicitly configured routing API."""
import json
import os
import shutil
from pathlib import Path
from urllib.parse import urlsplit

root = Path(__file__).resolve().parents[1]
api = os.environ.get("BASEL_API_URL", "").strip().rstrip("/")
parsed = urlsplit(api)
if (parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password
        or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
    raise SystemExit("Set BASEL_API_URL to the HTTPS origin of the deployed routing backend.")

output = root / "pages-dist"
output.mkdir(exist_ok=True)
static = root / "basel-spatial-graph-v0/app/static"
html = (static / "index.html").read_text()
html = html.replace('<script src="/static/app.js"></script>',
                    '<script src="./deployment-config.js"></script>\n<script src="./app.js"></script>')
(output / "index.html").write_text(html)
shutil.copyfile(static / "app.js", output / "app.js")
(output / "deployment-config.js").write_text("window.BASEL_API_BASE = " + json.dumps(api) + ";\n")
(output / ".nojekyll").touch()
