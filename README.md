# Spring Boot Project Generator

A secure Python utility that scaffolds a minimal, runnable Spring Boot application on disk. It creates a standard Maven project layout with a `pom.xml`, application entry point, sample REST controller, and `application.properties` — all written using hardened, symlink-safe filesystem operations.

---

## Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Usage](#usage)
- [API Reference](#api-reference)
- [Generated Project Structure](#generated-project-structure)
- [Security Model](#security-model)
- [Limitations](#limitations)
- [Examples](#examples)
- [License](#license)

---

## Overview

`generate_springboot_application()` produces a ready-to-build Spring Boot 3.2.5 project targeting Java 17. The generator is designed to be **safe by default**: it validates inputs, refuses to follow symlinks, verifies directory ownership, and writes files atomically so that partial failures never leave corrupt output behind.

This module is intended for use in tooling, CI pipelines, or scaffolding scripts where the output directory may be shared or untrusted.

---

## Features

- **Minimal Spring Boot scaffold** — `pom.xml`, `DemoApplication.java`, `HelloController.java`, and `application.properties`.
- **Strict package-name validation** — rejects empty names, malformed identifiers, and Java reserved keywords.
- **Symlink-safe filesystem operations** — uses `O_NOFOLLOW`, `dir_fd`-relative operations, and explicit `fstat` verification.
- **Ownership checks** — every created directory must be owned by the current effective user.
- **Atomic file writes** — content is written to a temp file, `fsync`ed, then `os.replace`d into place.
- **Clean failure semantics** — temp files are unlinked and directories `fsync`ed on error so retries are safe.
- **No external dependencies** — pure Python standard library.

---

## Requirements

- **Python 3.8+** (uses `dir_fd` and `src_dir_fd`/`dst_dir_fd` arguments to `os.replace`)
- **POSIX-like OS** for full security guarantees (`O_NOFOLLOW`, `O_DIRECTORY`, `geteuid`). On platforms lacking these, the module degrades gracefully but with reduced hardening.
- **JDK 17+** and **Maven 3.6+** to build and run the generated project (not required to *generate* it).

---

## Installation

Copy the module into your project, or install it as a package:

```bash
pip install .
```

There are no runtime dependencies.

---

## Usage

```python
from springboot_generator import generate_springboot_application

project_path = generate_springboot_application(
    output_dir="/tmp/workspace",
    package_name="com.acme.widgets",
)
print(f"Generated project at: {project_path}")
```

Then build and run:

```bash
cd /tmp/workspace/demo
mvn spring-boot:run
# Visit http://localhost:8080/
```

---

## API Reference

### `generate_springboot_application(output_dir: str, package_name: str = "com.example.demo") -> str`

Generates a Spring Boot project under `output_dir/demo` and returns the absolute path to the project root.

#### Parameters

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `output_dir` | `str` | — | An **existing** directory. Must not be a symlink. The project is created in a `demo/` subdirectory. |
| `package_name` | `str` | `"com.example.demo"` | A valid Java package name (dot-separated identifiers, no reserved keywords). |

#### Returns

`str` — Absolute path to the generated project root (`<output_dir>/demo`).

#### Raises

| Exception | Condition |
|-----------|-----------|
| `ValueError` | `output_dir` is empty, missing, not a directory, or a symlink. |
| `ValueError` | `package_name` is empty, malformed, or contains a Java keyword. |
| `ValueError` | Any path component is not a directory or is not owned by the current user. |
| `OSError` | Underlying filesystem error (permissions, disk full, etc.). |

---

## Generated Project Structure

```
<output_dir>/
└── demo/
    ├── pom.xml
    └── src/
        └── main/
            ├── java/
            │   └── <package/path>/
            │       ├── DemoApplication.java
            │       └── HelloController.java
            └── resources/
                └── application.properties
```

### Generated Files

| File | Purpose |
|------|---------|
| `pom.xml` | Maven build descriptor using `spring-boot-starter-parent:3.2.5`, `spring-boot-starter-web`, and the Spring Boot Maven plugin. |
| `DemoApplication.java` | `@SpringBootApplication` entry point. |
| `HelloController.java` | `@RestController` exposing `GET /` returning `"Hello from Spring Boot!"`. |
| `application.properties` | Sets `server.port=8080`. |

---

## Security Model

The generator treats the output directory as potentially hostile and applies defense-in-depth:

1. **Input validation**
   - `package_name` must match `^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$`.
   - No segment may be a Java reserved keyword (including `var`, `record`, `sealed`, `permits`, `yield`, `non-sealed`).
   - Path components must not be empty, `.`, `..`, or contain `/` or `\`.

2. **Symlink rejection**
   - `output_dir` is rejected if it is a symlink or resolves to one.
   - Directories are opened with `O_NOFOLLOW` where available.
   - Files are created with `O_NOFOLLOW` where available.

3. **Ownership verification**
   - Each opened directory is `fstat`ed and must be a directory owned by the current effective UID (`geteuid`).

4. **Atomic writes**
   - Content is written to a uniquely named temp file (`.{name}.tmp.{pid}.{random}`).
   - The temp file is `fsync`ed, then `os.replace`d into place.
   - The containing directory is `fsync`ed to persist the rename.
   - On failure, the temp file is unlinked and the directory `fsync`ed.

5. **Directory creation**
   - Directories are created with mode `0o700` and opened relative to their parent via `dir_fd`, preventing TOCTOU races on the path.

---

## Limitations

The following are **explicitly out of scope** for this module:

- **No build execution.** The function only writes source files. Building or running requires a JDK and Maven on the host.
- **No dependency resolution.** No network access, no version pinning beyond the Spring Boot parent POM.
- **No overwrite protection.** If `demo/` already exists, its contents are reused; existing files with the same names are atomically replaced.
- **POSIX-oriented.** Full hardening requires `O_NOFOLLOW`, `O_DIRECTORY`, and `geteuid`. On Windows, these fall back to best-effort behavior.
- **Fixed project name.** The generated project is always named `demo` with artifactId `demo`.

---

## Examples

### Basic usage

```python
import tempfile
from springboot_generator import generate_springboot_application

with tempfile.TemporaryDirectory() as tmp:
    path = generate_springboot_application(tmp)
    print(path)  # /tmp/.../demo
```

### Custom package

```python
path = generate_springboot_application(
    output_dir="/srv/projects",
    package_name="io.example.hello",
)
```

### Handling validation errors

```python
try:
    generate_springboot_application("/tmp/out", package_name="com.class.demo")
except ValueError as exc:
    print(f"Rejected: {exc}")
    # Rejected: Invalid Java package name: 'com.class.demo' (segment 'class' is a reserved keyword)
```

### Verifying the generated project

```bash
cd /tmp/out/demo
mvn -q package
java -jar target/demo-0.0.1-SNAPSHOT.jar
curl http://localhost:8080/
# Hello from Spring Boot!
```

---

## License

Specify your license here (e.g., MIT, Apache-2.0).