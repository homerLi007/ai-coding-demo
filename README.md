# demo

A Spring Boot + React application for submitting file paths for deletion. The frontend provides a simple textarea-based interface, and the backend exposes a REST endpoint that currently rejects all deletion requests by design (see [Notes](#notes)).

## Stack

Spring Boot 3.2.5 / Java 17 / React 18 / Vite 5

## Layout

```
.
├── backend/
│   ├── pom.xml
│   └── src/main/
│       ├── java/com/example/demo/
│       │   ├── DemoApplication.java
│       │   └── controller/ApiController.java
│       └── resources/application.properties
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

### Backend

```bash
cd backend
mvn spring-boot:run
```

The API listens on `http://localhost:8080`.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

The dev server runs on `http://localhost:5173` and proxies `/api` requests to the backend.

## API

| Method | Path          | Description                                                                 |
|--------|---------------|-----------------------------------------------------------------------------|
| POST   | `/api/delete` | Accepts `{ "paths": ["..."] }` and returns a `DeleteResponse`. Currently returns `403 Forbidden` with an empty `results` array. |

**Request body**

```json
{ "paths": ["/path/to/file1", "/path/to/file2"] }
```

**Response body**

```json
{ "results": [] }
```

## Notes

- **Deletion is disabled.** The `/api/delete` endpoint is a stub that always responds with `403 Forbidden`. It is intentionally non-functional pending the following prerequisites:
  - Authentication and authorization
  - A sandboxed data directory to constrain which paths can be targeted
  - Audit logging of deletion attempts
- The frontend surfaces the `403` response as a request error; no files are ever deleted.
- `spring.jackson.default-property-inclusion=non_null` is set so null fields are omitted from JSON responses.