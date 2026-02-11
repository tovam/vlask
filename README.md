# Vlask

**Vlask** is a tiny helper around **Vite + Flask**.

It gives you:

- a standard project layout: `server.py`, `frontend/`, `public/`
- auto-managed Vite dev server (HMR) when you run `python server.py`
- a simple Vite build flow that outputs `public/index.html`, `public/bundle.js`, `public/style.css`
- a few small CLI utilities


## Setup notes

`curl -fsSL https://raw.githubusercontent.com/tovam/vlask/refs/heads/master/vlask.py | python - use`

## Developer quickstart

### What to expect

You’ll get a project that looks like this:

```

your-project/
server.py        # Flask entrypoint (API + dev/prod wiring)
frontend/        # Vite + React app (dev server + source)
public/          # Production build output (static files)

````

- **In development** you run Flask, and Vlask **starts Vite** for you.
  - Visiting `/` in Flask redirects you to the Vite dev server (HMR).
  - Vite proxies `/api/*` calls back to your Flask backend.
- **In production** you build the frontend into `public/` and Flask serves it as static files.

### The mental model

- Flask owns your backend routes, e.g. `/api/ping`.
- Vite owns the frontend dev experience (hot reload, fast builds).
- Vlask just wires the two together:
  - **dev:** `GET /` → redirect to Vite (`http://localhost:<vite_port>`)
  - **dev:** Vite proxies `/api` → `http://localhost:<backend_port>`
  - **prod:** `GET /` → serve `public/index.html` (and `bundle.js`, `style.css`)

### Use it in seconds

1) Install Vlask

2) Initialize a project scaffold:

```bash
vlask init
````

This creates (if missing):

* `server.py`
* `frontend/` with a minimal React app + Vite config
* `public/` (empty until you build)

3. Start dev mode:

```bash
python server.py
```

* Flask runs on `http://localhost:5000`
* Vite runs on `http://localhost:55000` (default is `50000 + backend_port`)
* Open `http://localhost:5000/` → you’ll be redirected to Vite (HMR)

4. Try the API:

```bash
curl http://localhost:5000/api/ping
# -> {"ok": true}
```

### Building for production

Build the frontend into `./public`:

```bash
vlask bundle
```

Then run your Flask server in production mode (the scaffolded `server.py` uses `PROD=1`):

```bash
PROD=1 python server.py
```

Now Flask serves the built SPA from `public/`.

### Ports and configuration

* **Backend port**: defaults to `5000` (controlled by `PORT` env in `server.py`)
* **Vite dev port**: defaults to `50000 + backend_port` (so `55000` for `5000`)
* Vlask also exports these env vars to Vite:

  * `VLASK_BACKEND_PORT` (Flask port)
  * `VLASK_PORT` (Vite port)

### Where do I edit things?

* Frontend UI: `frontend/src/App.jsx` and `frontend/src/style.css`
* Backend API: add routes in `server.py` under the `# --- Routes ---` section
