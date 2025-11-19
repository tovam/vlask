import os
import re
import json
import subprocess
import socket
import sys
import urllib.request
import urllib.error
from pathlib import Path

try:
    from flask import Flask, send_from_directory, redirect
except:
    print("Flask is missing")


VERSION = "0.1.2"

DEFAULT_FRONTEND_DIR = "frontend"
DEFAULT_PUBLIC_DIR = "public"
DEFAULT_BACKEND_PORT = 5000
DEFAULT_CONFIG_PATH = Path.home() / ".vlask.yml"


class Vlask(Flask):
    """
    Flask wrapper that wires a Vite + React frontend to a Flask backend.

    Layout:
      - server.py
      - vlask.py
      - frontend/   (Vite + React app)
      - public/     (Vite build output in production)
    """

    def __init__(
        self,
        import_name,
        project_root=None,
        frontend_dir=None,
        public_dir=None,
        prod=False,
        backend_port=5000,
        vite_port=None,
        auto_build=None,  # defaults depend on prod/dev
        watch=None,       # defaults depend on prod/dev
        **flask_kwargs
    ):
        # Project root defaults to current working directory
        self.project_root = Path(project_root or os.getcwd())

        self.frontend_dir = self.project_root / (frontend_dir or DEFAULT_FRONTEND_DIR)
        self.public_dir = self.project_root / (public_dir or DEFAULT_PUBLIC_DIR)

        self.prod = bool(prod)
        self.backend_port = int(backend_port)
        self.vite_port = int(vite_port) if vite_port is not None else 50000 + self.backend_port

        # Default behaviors depend on prod/dev, but remain overridable
        if auto_build is None:
            auto_build = False if self.prod else True
        if watch is None:
            watch = False if self.prod else True

        self.auto_build = bool(auto_build)
        self.watch = bool(watch)

        # Initial structure
        self._ensure_basic_structure()

        # Flask configuration
        flask_kwargs.setdefault("static_folder", str(self.public_dir))
        flask_kwargs.setdefault("static_url_path", "")

        super(Vlask, self).__init__(import_name, **flask_kwargs)

        # Default routes
        self._register_default_routes()

    # ------------ Project layout / scaffolding ------------

    def _ensure_basic_structure(self):
        """
        Ensure required frontend files and directories exist.

        Creates:
          - frontend/
          - frontend/src/App.jsx
          - frontend/src/main.jsx
          - frontend/src/style.css
          - frontend/index.html
          - frontend/package.json
          - frontend/vite.config.js
          - public/
        Existing files are never overwritten.
        """
        if not self.frontend_dir.exists():
            self.frontend_dir.mkdir(parents=True)
            print("[Vlask] Created directory:", self.frontend_dir)

        src_dir = self.frontend_dir / "src"
        if not src_dir.exists():
            src_dir.mkdir(parents=True)
            print("[Vlask] Created directory:", src_dir)

        app_x = src_dir / "App.jsx"
        if not app_x.exists():
            app_x.write_text(self._default_app_jsx(), encoding="utf-8")
            print("[Vlask] Created frontend/src/App.jsx")

        main_x = src_dir / "main.jsx"
        if not main_x.exists():
            main_x.write_text(self._default_main_jsx(), encoding="utf-8")
            print("[Vlask] Created frontend/src/main.jsx")

        style_css = src_dir / "style.css"
        if not style_css.exists():
            style_css.write_text(self._default_style_css(), encoding="utf-8")
            print("[Vlask] Created frontend/src/style.css")

        index_html_front = self.frontend_dir / "index.html"
        if not index_html_front.exists():
            index_html_front.write_text(self._default_frontend_index_html(), encoding="utf-8")
            print("[Vlask] Created frontend/index.html")

        package_json = self.frontend_dir / "package.json"
        if not package_json.exists():
            self._create_default_package_json(package_json)
            print("[Vlask] Created frontend/package.json")

        vite_config = self.frontend_dir / "vite.config.js"
        if not vite_config.exists():
            self._create_default_vite_config(vite_config)
            print("[Vlask] Created frontend/vite.config.js")

        if not self.public_dir.exists():
            self.public_dir.mkdir(parents=True)
            print("[Vlask] Created directory:", self.public_dir)

    def _create_default_package_json(self, package_json_path):
        """
        Minimal package.json for Vite + React.

        Only created if the file does not exist.
        """
        data = {
            "name": "vlask-frontend",
            "private": True,
            "scripts": {
                "dev": "vite",
                "build": "vite build",
                "preview": "vite preview"
            },
            "dependencies": {
                "react": "^18.0.0",
                "react-dom": "^18.0.0"
            },
            "devDependencies": {
                "vite": "^6.0.0",
                "@vitejs/plugin-react-swc": "^3.0.0"
            }
        }
        package_json_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _create_default_vite_config(self, vite_config_path):
        """
        Minimal Vite config:
          - React plugin
          - Dev server port taken from env (VLASK_PORT) or vite_port
          - Proxy /api to Flask backend (VLASK_BACKEND_PORT or backend_port)
          - Build output to ../public with:
              - index.html
              - bundle.js   (entry)
              - style.css   (main CSS asset)
        """
        content = """import { defineConfig } from "vite";
import react from "@vitejs/plugin-react-swc";

const backendPort = Number(process.env.VLASK_BACKEND_PORT || "%d");
const vitePort = Number(process.env.VLASK_PORT || "%d");

export default defineConfig({
  root: ".",
  plugins: [react()],
  server: {
    // host: '0.0.0.0', // uncomment to serve over network
    port: vitePort,
    strictPort: true,
    proxy: {
      "/api": {
        target: "http://localhost:" + backendPort,
        changeOrigin: true
      }
    }
  },
  build: {
    outDir: "../public",
    emptyOutDir: true,
    rollupOptions: {
      input: "index.html",
      output: {
        entryFileNames: "bundle.js",
        assetFileNames: (assetInfo) => {
          if (assetInfo.name && assetInfo.name.endsWith(".css")) {
            return "style.css";
          }
          return "[name][extname]";
        },
        chunkFileNames: "bundle-[hash].js"
      }
    }
  }
});
""" % (self.backend_port, self.vite_port)

        vite_config_path.write_text(content, encoding="utf-8")

    # ------------ Frontend / Vite orchestration ------------

    def _prepare_frontend(self):
        """
        Ensures frontend dependencies and build/dev state are up to date.

        - Runs npm install if node_modules is missing
        - In production: runs `npm run build` if a rebuild is needed
        - In development: starts Vite dev server once (in the correct process)
        """
        package_json = self.frontend_dir / "package.json"
        if not package_json.exists():
            print("[Vlask] No package.json found, skipping frontend setup.")
            return

        print("[Vlask] Preparing frontend (Vite) in", self.frontend_dir)

        node_modules = self.frontend_dir / "node_modules"
        if not node_modules.exists():
            print("[Vlask] node_modules not found, running npm install...")
            self._run_cmd(["npm", "install"], cwd=self.frontend_dir)

        if self.prod:
            if self._needs_build():
                print("[Vlask] Vite build required, running npm run build...")
                self._run_cmd(["npm", "run", "build"], cwd=self.frontend_dir)
            else:
                print("[Vlask] Existing Vite build is up to date.")
        else:
            if self.watch:
                self._start_vite_dev()
            else:
                print("[Vlask] Dev mode with watch disabled; Vite dev server not started.")

    def _ensure_prod_bundle(self):
        """
        In production, ensures at least public/bundle.js exists.

        If bundle.js is missing, runs a Vite build once (npm run build),
        regardless of _needs_build().
        """
        bundle = self.public_dir / "bundle.js"
        if bundle.exists():
            return

        package_json = self.frontend_dir / "package.json"
        if not package_json.exists():
            print("[Vlask] No package.json found; cannot build frontend.")
            return

        print("[Vlask] public/bundle.js not found; running a production build once.")
        node_modules = self.frontend_dir / "node_modules"
        if not node_modules.exists():
            print("[Vlask] node_modules not found, running npm install...")
            self._run_cmd(["npm", "install"], cwd=self.frontend_dir)

        self._run_cmd(["npm", "run", "build"], cwd=self.frontend_dir)

    def _is_port_in_use(self, port):
        """
        Returns True if a process is already listening on the given port.

        Checks both IPv4 (127.0.0.1) and IPv6 (::1).
        """
        # IPv4
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.2)
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    return True
        except OSError:
            pass

        # IPv6
        try:
            with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as s:
                s.settimeout(0.2)
                if s.connect_ex(("::1", port, 0, 0)) == 0:
                    return True
        except OSError:
            pass

        return False

    def _start_vite_dev(self):
        """
        Starts `npm run dev` (Vite dev server) on self.vite_port.

        If the port is already in use, Vite is assumed to be running; nothing is started.
        """
        if self._is_port_in_use(self.vite_port):
            print(
                "[Vlask] Vite dev server already running on port %d; not starting a new one."
                % self.vite_port
            )
            return

        print(
            "[Vlask] Starting Vite dev server on port %d (npm run dev)..."
            % self.vite_port
        )

        env = os.environ.copy()
        env["VLASK_BACKEND_PORT"] = str(self.backend_port)
        env["VLASK_PORT"] = str(self.vite_port)

        try:
            subprocess.Popen(
                ["npm", "run", "dev", "--", "--port", str(self.vite_port)],
                cwd=str(self.frontend_dir),
                env=env,
            )
        except FileNotFoundError:
            print("[Vlask] npm executable not found; cannot start Vite dev server.")
            raise

    def _needs_build(self):
        """
        Determines whether a Vite production build is out of date.

        Rebuild if:
          - public/ does not exist or is empty
          - any file in frontend/src, frontend/index.html or frontend/vite.config.js
            is newer than the files in public/
        """
        if not self.public_dir.exists():
            return True

        public_files = [p for p in self.public_dir.rglob("*") if p.is_file()]
        if not public_files:
            return True

        latest_public_mtime = max(p.stat().st_mtime for p in public_files)

        candidates = []
        src_dir = self.frontend_dir / "src"
        if src_dir.exists():
            candidates.extend([p for p in src_dir.rglob("*") if p.is_file()])

        front_index = self.frontend_dir / "index.html"
        if front_index.exists():
            candidates.append(front_index)

        vite_config = self.frontend_dir / "vite.config.js"
        if vite_config.exists():
            candidates.append(vite_config)

        if not candidates:
            return False

        latest_src_mtime = max(p.stat().st_mtime for p in candidates)
        return latest_src_mtime > latest_public_mtime

    def _run_cmd(self, cmd, cwd):
        """
        Runs a shell command and raises on failure.

        Adds VLASK_* environment variables so Vite can see backend and dev ports.
        """
        env = os.environ.copy()
        env["VLASK_BACKEND_PORT"] = str(self.backend_port)
        env["VLASK_PORT"] = str(self.vite_port)

        try:
            subprocess.run(
                cmd,
                cwd=str(cwd),
                check=True,
                env=env,
            )
        except FileNotFoundError:
            print("[Vlask] Command not found:", cmd[0])
            raise
        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                "[Vlask] Command failed (%s), cwd=%s" % (" ".join(cmd), cwd)
            ) from e

    # ------------ Flask.run override ------------

    def run(self, *args, **kwargs):
        """
        Overrides Flask.run to hook Vite only in the right process.

        - In production:
            - if auto_build=True  -> full build-if-needed
            - if auto_build=False -> only ensure public/bundle.js exists
        - In development:
            only prepares frontend (and starts Vite dev) in:
              * single-process runs (debug=False), or
              * the Werkzeug reloader child (WERKZEUG_RUN_MAIN == "true").
        """
        debug = kwargs.get("debug")
        if debug is None:
            debug = self.debug

        if self.prod:
            flag = os.environ.get("WERKZEUG_RUN_MAIN")
            if debug:
                if flag == "true":
                    if self.auto_build:
                        self._prepare_frontend()
                    else:
                        self._ensure_prod_bundle()
            else:
                if self.auto_build:
                    self._prepare_frontend()
                else:
                    self._ensure_prod_bundle()
        else:
            if self.auto_build:
                flag = os.environ.get("WERKZEUG_RUN_MAIN")

                if not debug:
                    self._prepare_frontend()
                else:
                    if flag == "true":
                        self._prepare_frontend()
                    else:
                        pass

        return super(Vlask, self).run(*args, **kwargs)

    # ------------ Routes ------------

    def _register_default_routes(self):
        """
        Default behavior for "/":
          - In dev: redirect to the Vite dev server (full Vite experience).
          - In prod: serve public/index.html if it exists.
        """

        @self.route("/")
        def index():
            if not self.prod:
                url = "http://localhost:%d" % self.vite_port
                return redirect(url, code=302)

            index_path = self.public_dir / "index.html"
            if index_path.exists():
                return send_from_directory(self.public_dir, "index.html")
            return "index.html not found in public/", 404

    # ------------ Default frontend content ------------

    def _default_frontend_index_html(self):
        return """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>Vlask + Vite + React</title>
  </head>
  <body>
    <div id="app"></div>
    <script type="module" src="/src/main.jsx"></script>
  </body>
</html>
"""

    def _default_app_jsx(self):
        return """import React from "react";

function App() {
  return (
    <main className="app-root">
      <section className="app-card">
        <h1>Vlask + Vite + React</h1>
        <p>Frontend served by Vite, backend by Flask.</p>
        <p className="hint">
          Edit <code>frontend/src/App.jsx</code> or <code>frontend/src/style.css</code>.
        </p>
      </section>
    </main>
  );
}

export default App;
"""

    def _default_main_jsx(self):
        return """import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./style.css";

const rootElement = document.getElementById("app");

if (rootElement) {
  const root = ReactDOM.createRoot(rootElement);
  root.render(
    <React.StrictMode>
      <App />
    </React.StrictMode>
  );
}
"""

    def _default_style_css(self):
        return """html,
body {
  margin: 0;
  padding: 0;
}

body {
  font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

.app-root {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #111827;
  color: #f9fafb;
}

.app-card {
  text-align: center;
  padding: 2.5rem 3rem;
  border-radius: 1.5rem;
  background: #020617;
  box-shadow:
    0 10px 30px rgba(15, 23, 42, 0.7),
    0 0 0 1px rgba(148, 163, 184, 0.15);
}

.app-card h1 {
  font-size: 2.4rem;
  margin-bottom: 0.75rem;
}

.app-card p {
  margin: 0.25rem 0;
}

.app-card .hint {
  opacity: 0.75;
  font-size: 0.9rem;
  margin-top: 0.75rem;
}
"""


# ------------ config / version helpers ------------


def _load_config(path: Path = DEFAULT_CONFIG_PATH):
    """
    Loads a minimal YAML-like config from the given path.

    Supports lines like:
      key: value
    Ignores empty lines and lines starting with '#'.
    """
    if not path.exists():
        return {}

    data = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip()
    return data


def _parse_version(s: str):
    """
    Parse a dotted version string into a tuple of ints for comparison.
    Non-numeric parts are treated as 0.
    """
    parts = []
    for part in s.strip().split("."):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def _extract_version_from_text(text: str):
    """
    Extract VERSION = "x.y.z" from a Python source string.
    """
    m = re.search(r'^VERSION\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    if not m:
        return None
    return m.group(1)


# ------------ CLI: python vlask.py ------------


HELP_TEXT = f"""Vlask helper script (version {VERSION})

Usage:
  python vlask.py create    Initialize a Vlask project in the current directory
  python vlask.py bundle    Build the production frontend into ./public
  python vlask.py update    Update vlask.py from the configured URL (see ~/.vlask.yml)
  python vlask.py use       Show notes about how to install and use Vlask
  python vlask.py help      Show this help (default)
"""

USE_TEXT = """Vlask usage notes

- Put vlask.py in a directory that is on your Python path (for example: a shared "libs" folder).
- Or add its directory to PYTHONPATH, e.g.:
    export PYTHONPATH="$PYTHONPATH:/path/to/your/libs"
- Import it in your projects with:
    from vlask import Vlask
- To have a CLI command, create a small script like:
    #!/usr/bin/env python3
    from vlask import main
    if __name__ == "__main__":
        main()
  and put it in a directory on your PATH (for example: ~/bin), then:
    export PATH="$PATH:$HOME/bin"
"""


def _create_server_py(project_root):
    """
    Creates a minimal server.py (if missing) that uses Vlask.
    """
    server_py = project_root / "server.py"
    if server_py.exists():
        print("[Vlask] server.py already exists, skipping.")
        return

    content = """import os
from flask import Flask, jsonify, request
try:
    from vlask import Vlask
    HAS_VLASK = True
except ImportError:
    Vlask = None
    HAS_VLASK = False

PORT = 5000
PORT = int(os.getenv("PORT", PORT))

ENV_PROD = os.getenv("PROD", "") == "1"
DEV_MODE = HAS_VLASK and (not ENV_PROD)

if DEV_MODE:
    app = Vlask(__name__, prod=False, backend_port=PORT)
else:
    app = Flask(__name__, static_folder="public", static_url_path="")

    @app.route("/")
    def index():
        return app.send_static_file("index.html")

    @app.errorhandler(404)
    def spa_fallback(e):
        if request.path.startswith("/api/"):
            return jsonify({"error": "not found"}), 404
        try:
            return app.send_static_file("index.html")
        except Exception:
            return "index.html not found in ./public", 404

# --- Routes ---
@app.route("/api/ping")
def ping():
    return {"ok": True}

if __name__ == "__main__":
    app.run(port=PORT, debug=DEV_MODE)
"""
    server_py.write_text(content, encoding="utf-8")
    print("[Vlask] Created server.py")


def _cmd_create():
    root = Path(os.getcwd())
    print("[Vlask] Initializing project in", root)

    _create_server_py(root)

    app = Vlask(
        __name__,
        project_root=root,
        prod=False,
        backend_port=DEFAULT_BACKEND_PORT,
        auto_build=True,
        watch=False,
    )

    if app:  # noqa: F841
        pass

    print("[Vlask] Done. Run `python server.py` to start Flask + Vite.")


def _cmd_bundle():
    root = Path(os.getcwd())
    print("[Vlask] Building production bundle in", root)

    app = Vlask(
        __name__,
        project_root=root,
        prod=True,
        backend_port=DEFAULT_BACKEND_PORT,
        auto_build=False,
        watch=False,
    )

    package_json = app.frontend_dir / "package.json"
    if not package_json.exists():
        print("[Vlask] No package.json found; cannot build frontend.")
        return

    node_modules = app.frontend_dir / "node_modules"
    if not node_modules.exists():
        print("[Vlask] node_modules not found, running npm install...")
        app._run_cmd(["npm", "install"], cwd=app.frontend_dir)

    app._run_cmd(["npm", "run", "build"], cwd=app.frontend_dir)

    if app:  # noqa: F841
        pass

    print("[Vlask] Production bundle built into ./public")


def _cmd_update():
    print(f"[Vlask] Current version: {VERSION}")

    config = _load_config()
    update_url = config.get("update_url")
    if not update_url:
        print(f"[Vlask] No update_url configured in {DEFAULT_CONFIG_PATH}")
        print('        Add a line like: update_url: https://example.com/path/to/vlask.py')
        return

    print(f"[Vlask] Fetching update from {update_url} ...")
    try:
        with urllib.request.urlopen(update_url) as resp:
            remote_text = resp.read().decode("utf-8")
    except urllib.error.URLError as e:
        print(f"[Vlask] Failed to fetch update: {e}")
        return

    remote_version = _extract_version_from_text(remote_text)
    if not remote_version:
        print("[Vlask] Remote file does not contain a VERSION; aborting.")
        return

    print(f"[Vlask] Remote version: {remote_version}")

    if _parse_version(remote_version) <= _parse_version(VERSION):
        print("[Vlask] Already up to date or newer; no update performed.")
        return

    module_path = Path(__file__).resolve()
    print(f"[Vlask] Updating {module_path} to version {remote_version} ...")

    try:
        module_path.write_text(remote_text, encoding="utf-8")
    except OSError as e:
        print(f"[Vlask] Failed to write updated file: {e}")
        return

    print("[Vlask] Update complete. Restart your process to use the new version.")


def _cmd_use():
    print(USE_TEXT)


def main():
    if len(sys.argv) == 2:
        cmd = sys.argv[1]
        if cmd == "create":
            _cmd_create()
        elif cmd == "bundle":
            _cmd_bundle()
        elif cmd == "update":
            _cmd_update()
        elif cmd == "use":
            _cmd_use()
        else:
            print(HELP_TEXT)
    else:
        print(HELP_TEXT)


if __name__ == "__main__":
    main()
