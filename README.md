# demo

A small full-stack application for deleting files on the server via a web UI. The Spring Boot backend exposes a single REST endpoint that deletes files relative to its working directory, while the React frontend provides a simple textarea-based interface for submitting paths and viewing per-file results.

## Stack

Spring Boot 3.2 / Java 17 / React 18 / Vite 5

## Layout

```
.
├── backend/
│   ├── pom.xml
│   └── src/
│       └── main/
│           ├── java/com/example/demo/
│           │   ├── DemoApplication.java
│           │   └── controller/ApiController.java
│           └── resources/application.properties
└── frontend/
    ├── index.html
    ├── package.json
    ├── vite.config.js
    └── src/
        ├── App.jsx
        ├── App.css
        └── main.jsx
```

## How to run

**Backend** (listens on `http://localhost:8080`):

```bash
cd backend
mvn spring-boot:run
```

**Frontend** (listens on `http://localhost:5173`, proxies `/api` to the backend):

```bash
cd frontend
npm install
npm run dev
```

Then open `http://localhost:5173` in your browser.

## API

### `POST /api/delete`

Deletes one or more files relative to the server's working directory.

**Request body:**

```json
{
  "paths": ["tmp/a.txt", "tmp/b.txt"]
}
```

**Response body:**

```json
{
  "results": [
    { "path": "tmp/a.txt", "deleted": true, "error": null },
    { "path": "tmp/b.txt", "deleted": false, "error": "not found" }
  ]
}
```

Each entry in `results` reports whether the individual path was deleted and, if not, the reason (`empty path`, `path escapes base directory`, `not found`, `is a directory`, or an I/O error message).

## Notes

- Paths are resolved against the backend process's working directory (`user.dir`) and normalized. Any path that resolves outside that base directory is rejected.
- Only regular files and symbolic links are deleted. Directories are refused with `is a directory` — recursive deletion is not supported.
- There is no authentication or authorization. The endpoint will delete any file the backend process has permission to remove within its working directory. Do not expose this service on an untrusted network.
- Multipart uploads are disabled (`spring.servlet.multipart.enabled=false`); the API only accepts JSON.
- The frontend assumes the Vite dev proxy is in place. For production, serve the built `frontend/dist` behind a reverse proxy that forwards `/api` to the backend, or configure CORS explicitly.