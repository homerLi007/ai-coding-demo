# secure_unlink

Symlink-safe, root-confined file deletion for Python.

`secure_unlink` deletes files beneath a caller-supplied root directory while refusing to follow symlinks and refusing to escape the root via `..` or absolute paths. It is intended for use in privileged contexts (e.g. cleanup routines running as root) where the directory tree may be influenced by an untrusted party.

---

## Table of Contents

- [Overview](#overview)
- [Security Model](#security-model)
  - [Strong Path (`openat2`)](#strong-path-openat2)
  - [Fallback Path](#fallback-path)
  - [Residual Risk](#residual-risk)
- [Requirements](#requirements)
- [Installation](#installation)
- [Usage](#usage)
  - [Python API](#python-api)
  - [Command Line](#command-line)
- [API Reference](#api-reference)
  - [`secure_unlink(root, paths)`](#secure_unlinkroot-paths)
- [Behavior Details](#behavior-details)
  - [Path Validation](#path-validation)
  - [Error Handling](#error-handling)
  - [`openat2` Probing and Caching](#openat2-probing-and-caching)
- [Platform Support](#platform-support)
- [Limitations](#limitations)
- [Best Practices](#best-practices)
- [License](#license)

---

## Overview

Deleting a file by path in a privileged process is a classic source of security bugs. A naive `os.unlink(os.path.join(root, user_path))` is vulnerable to:

- **Symlink traversal** — an attacker replaces a component with a symlink pointing outside `root`.
- **Path traversal** — `..` components escape the root.
- **Absolute paths** — a caller-supplied absolute path ignores `root` entirely.
- **TOCTOU races** — the directory tree is renamed between validation and deletion.

`secure_unlink` addresses these by:

1. Rejecting absolute paths, `..` components, and NUL bytes up front.
2. Resolving the *parent* directory of each target using `openat2(2)` with `RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS` on modern kernels.
3. Falling back to an `O_NOFOLLOW` component walk on older kernels.
4. Re-verifying parent identity (`st_dev`, `st_ino`) immediately before unlinking.

---

## Security Model

### Strong Path (`openat2`)

When `openat2(2)` is available, the parent directory of each target is resolved with:

```
RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS
```

- `RESOLVE_BENEATH` guarantees the resolved parent was beneath `root` **at resolution time**.
- `RESOLVE_NO_SYMLINKS` guarantees no symlink was traversed.

The file is then removed with `unlinkat(parent_fd, basename)`.

### Fallback Path

When `openat2(2)` is genuinely unavailable (older kernels, unsupported architectures), the parent is reached by walking components with `O_NOFOLLOW | O_DIRECTORY`. This path is **strictly weaker** than the strong path but still rejects symlinks and `..`.

### Residual Risk

> **There is no kernel primitive that atomically unlinks a name beneath a root.**

Once a directory fd is held, `unlinkat` operates on whatever directory that fd refers to — regardless of where that directory currently lives in the namespace. An attacker who can rename the parent directory out of `root` between the `openat2` resolution and the `unlinkat` call can cause a file outside `root` to be unlinked.

`secure_unlink` narrows this window by re-resolving the parent and comparing `(st_dev, st_ino)` immediately before the unlink, but **this is best-effort, not a guarantee**. The attacker's window is between the recheck and the `unlinkat`.

**Callers requiring a hard guarantee must ensure that neither `root` nor any of its ancestors is writable by an untrusted party.**

---

## Requirements

- Python 3.7+
- Linux (for `openat2` support; the fallback path works on any POSIX system with `O_NOFOLLOW` and `dir_fd` support in `os.open`/`os.unlink`)
- No third-party dependencies

---

## Installation

Copy `secure_unlink.py` into your project, or install it as a module:

```bash
pip install .
```

(If packaged; otherwise simply vendor the file.)

---

## Usage

### Python API

```python
from secure_unlink import secure_unlink

results = secure_unlink("/var/lib/myapp/uploads", [
    "user1/avatar.png",
    "user2/tmp/cache.bin",
    "user3/old.log",
])

for path, err in results:
    if err is None:
        print(f"deleted: {path}")
    else:
        print(f"failed:  {path}: {err}")
```

Each path is processed independently. A failure on one path does not abort the rest.

### Command Line

```bash
python secure_unlink.py ROOT PATH [PATH ...]
```

Example:

```bash
$ python secure_unlink.py /srv/data "a/b.txt" "c/d.txt"
unlinked: a/b.txt
unlinked: c/d.txt
```

Failures are reported on stderr:

```bash
$ python secure_unlink.py /srv/data "../etc/passwd"
failed:   ../etc/passwd: [Errno 22] '..' component not allowed: '../etc/passwd'
```

---

## API Reference

### `secure_unlink(root, paths)`

```python
def secure_unlink(
    root: str,
    paths: Iterable[str],
) -> List[Tuple[str, Optional[OSError]]]:
```

Delete each path in `paths` beneath `root`.

**Parameters**

| Name    | Type                | Description                                                       |
|---------|---------------------|-------------------------------------------------------------------|
| `root`  | `str`               | Directory beneath which all deletions must occur.                 |
| `paths` | `Iterable[str]`     | Relative paths (relative to `root`) to delete.                    |

**Returns**

A list of `(path, error)` tuples, in the same order as `paths`:

- `error is None` — the file was unlinked successfully.
- `error` is an `OSError` — the deletion failed; the exception describes why.

**Raises**

- `OSError` if `root` cannot be opened as a directory. This is a fatal, whole-call error and is not reported per-path.

**Example**

```python
results = secure_unlink("/srv/data", ["a.txt", "sub/b.txt"])
# [("a.txt", None), ("sub/b.txt", None)]
```

---

## Behavior Details

### Path Validation

Each path is validated before any filesystem operation:

| Condition                          | Result                                    |
|------------------------------------|-------------------------------------------|
| Empty string                       | `ValueError` (surfaced as `OSError`? No — see note) |
| Absolute path (`/foo`)             | `ValueError`                              |
| Contains `..` component            | `ValueError`                              |
| Contains NUL byte                  | `ValueError`                              |
| Resolves to nothing (`.` or `//`)  | `ValueError`                              |

> **Note:** `_split_components` raises `ValueError`, not `OSError`. Because `secure_unlink` only catches `OSError` per-path, a `ValueError` from a malformed path will propagate out of `secure_unlink` and abort the remaining paths. Callers that pass untrusted input should validate paths first, or wrap the call in a `try`/`except ValueError`.

### Error Handling

- Per-path failures are captured and returned as `(path, OSError)` tuples.
- `EAGAIN` is used to signal that the parent directory changed during resolution (a detected race). Callers may retry.
- `ENOENT`, `EACCES`, `EPERM`, `EISDIR`, etc. are propagated from the underlying syscalls.

### `openat2` Probing and Caching

The module probes for `openat2(2)` support lazily on first use:

- **`ENOSYS` / `EINVAL`** — treated as definitive evidence the syscall is unsupported; cached as `False`.
- **Transient errors** (`EMFILE`, `ENFILE`, `ENOMEM`, `EINTR`, `EAGAIN`, …) — **not** cached, so a transient failure cannot permanently downgrade the process to the weaker fallback path.
- **Success** — cached as `True`.

The probe uses the exact resolve flags used at the call sites, so a kernel that accepts `openat2` but rejects `RESOLVE_BENEATH` for non-mount-point roots is detected up front.

---

## Platform Support

| Platform / Arch      | `openat2` syscall number |
|----------------------|--------------------------|
| `x86_64`             | 437                      |
| `aarch64` / `arm64`  | 437                      |
| `riscv64`            | 437                      |
| `i386` / `i686`      | 437                      |
| `armv7l`             | 437                      |
| `ppc64` / `ppc64le`  | 437                      |
| `s390x`              | 437                      |

On architectures not listed, the module falls back to the `O_NOFOLLOW` walk. On non-Linux systems, `openat2` is unavailable and the fallback path is used.

`openat2(2)` requires Linux 5.6 or newer.

---

## Limitations

- **Not atomic.** See [Residual Risk](#residual-risk). The recheck narrows but does not close the rename-out-of-root race.
- **Fallback path is weaker.** The `O_NOFOLLOW` walk cannot close the same race and is used only when `openat2` is genuinely unavailable.
- **No directory removal.** Only files (and symlinks themselves) are unlinked; directories are not removed.
- **`ValueError` propagation.** Malformed paths raise `ValueError` which is not caught per-path (see [Path Validation](#path-validation)).
- **`root` must be openable.** If `root` cannot be opened as a directory, the whole call fails.

---

## Best Practices

1. **Do not make `root` or its ancestors writable by untrusted users.** This is the only way to obtain a hard guarantee against the rename race.
2. **Validate paths before calling** if they originate from untrusted input, to avoid `ValueError` aborting the batch.
3. **Handle `EAGAIN`** by retrying — it indicates a detected race, not a permanent failure.
4. **Prefer the strong path.** Ensure your kernel is ≥ 5.6 and your architecture is in the syscall table above.
5. **Run with the minimum privileges necessary.** `secure_unlink` is designed for privileged contexts but does not require root.

---

## License

Specify your license here (e.g. MIT, Apache-2.0).