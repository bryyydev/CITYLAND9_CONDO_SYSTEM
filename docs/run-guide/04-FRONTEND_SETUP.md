# 04 — Frontend setup (React + Vite)

The frontend is a **React 19 + TypeScript + Tailwind CSS** app built with **Vite**, in
`frontend\`. In production it is **built once** into static files (`frontend\dist`) that the
**Flask server serves at `/app/`**. No Node.js process runs in production.

Fonts (Sora, Plus Jakarta Sans) and icons (Remix Icon) are bundled from npm packages, so the
app works on a LAN **without internet**.

## 1. Install the packages

```powershell
cd frontend
npm ci
```

`npm ci` installs the exact versions in `package-lock.json` (use `npm install` only when you
intend to add or upgrade a package). Requires Node.js **20.19+**.

## 2. Build for production

```powershell
cd frontend
npm run build
```

- Type-checks (`tsc`), then writes the optimised app to **`frontend\dist\`**.
- Flask serves it at **`http://<server>:5000/app/`** (`backend\app\routes\spa.py`); unknown
  `/app/...` paths return `index.html` so deep links work.
- `START_WINDOWS.bat` builds automatically when `frontend\dist` is missing, and
  `START_WINDOWS.bat --rebuild` rebuilds after you update the code.
- `frontend\dist` is not in git: every installation builds its own.
- No restart is needed after a rebuild: refresh the browser.

If `dist` is missing, `/app/` answers: *"The React frontend has not been built yet."*

## 3. Development (hot reload)

Start the backend first ([03 §4](03-BACKEND_SETUP.md)), then:

```powershell
cd frontend
npm run dev
```

Open **http://127.0.0.1:5173/app/**.

- Vite serves the app on port **5173** and **forwards every `/api` request to
  `http://127.0.0.1:5000`** (`vite.config.ts` → `server.proxy`), so the browser only ever talks to
  one origin. No CORS configuration and no `.env` are needed.
- Sign-in is shared with the classic screens on port 5000 (cookies are per host, not per port).
- Links to classic screens open `http://127.0.0.1:5000/...` in development.

## 4. Prototype mode (no backend, mock data)

```powershell
cd frontend
npm run dev:mock
```

Open **http://127.0.0.1:5173/app/** and click a name on the sign-in page.

- All six role workspaces with realistic mock data, and a **Role Switcher** in the top bar.
- Nothing is saved; reloading resets the data. Nothing reaches the database.
- Mock mode exists only in `npm run dev:mock`: the data source is chosen at build time
  (`@data-source` alias in `vite.config.ts`), so **production builds contain no mock data**.

> Run `npm` commands **inside the `frontend` folder**. From the project root you get
> `npm error enoent Could not read package.json`.

## 5. Scripts

| Command (in `frontend\`) | What |
|---|---|
| `npm run dev` | Development server on :5173, live data through the `/api` proxy |
| `npm run dev:mock` | Development server on :5173, mock data, Role Switcher |
| `npm run build` | Type-check + production build into `dist\` |
| `npm run typecheck` | Type-check only |
| `npm run preview` | Preview the built `dist\` (static only; use Flask for the real thing) |

## 6. How the frontend finds the server

It doesn't need to: every API call is **relative** (`/api/...`). Whatever address the user opened
(`http://127.0.0.1:5000`, `http://192.168.100.63:5000`, a hostname) is also where the API is.
Don't add a `VITE_API_BASE_URL`: it would tie the build to one IP address and create
cross-origin (CORS) problems.

## 7. Permissions copy

The menus use `frontend\src\config\permissions.json`, **generated** from the backend's
permission matrix (`backend\app\core\permissions.py`). After changing permissions run:

```powershell
.venv\Scripts\python.exe database\tools\export_permissions.py
```

A test fails if the copy is out of date. The server still checks every permission itself.

## 8. Checklist

- [ ] `node --version` → v20.19+
- [ ] `npm ci` and `npm run build` finish without errors (inside `frontend\`)
- [ ] `frontend\dist\index.html` exists
- [ ] `http://<server>:5000/app/` shows the CityLand 9 sign-in page
