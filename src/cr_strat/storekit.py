"""What the stores share: one GET with the standard library, its checks, the block rule's clock, and the writes.

``rbi_store`` (build-order item 4 of ``docs/rates_data_sources.md``) and the
macro sources (``macro_sources``, item 5) ask public web servers for data on a
schedule. Both must be polite in the same way, so the pieces they share live
here; ``rbi_store`` imports them under the names it always had.

* ``urllib_transport(url, headers, timeout)`` sends one GET with the standard
  library (the environment's proxy and certificate store) and never follows a
  redirect: a 3xx comes back as its own status, since following it would be a
  second request, perhaps to another host. Every answer, an error status
  included, is a ``FeedResponse``; a network failure is one with ``status``
  ``None`` and the error's text. At most ``max_bytes`` of the body are read
  (default 5,000,000, what RBI's feed poller always read); a caller that
  allows more passes more. ``ssl_context`` replaces the default TLS context,
  for instance with ``legacy_renegotiation_context()``: MoSPI's official API
  client says api.mospi.gov.in needs legacy TLS renegotiation (not observed
  through this environment's proxy). That context keeps certificate checks;
  nothing here ever turns them off.
* ``decode_body`` undoes ``Content-Encoding: gzip`` (the only encoding the
  pollers ask for) with a cap on the decompressed size.
* ``check_user_agent`` and ``check_timeout`` refuse, before anything is sent
  or recorded, a client name or a timeout the standard library would refuse
  or garble only once the request is under way.
* ``resume_time(now)``: after a block (HTTP 403, 418 or 429, or a refusal
  page), no request before 08:00 India time on the India day after ``now``;
  ``MAX_COOLDOWN`` is the longest wait that rule can give (a block at 00:00
  India time waits 32 hours), so a longer one recorded means the clock moved
  back. ``STOP_AFTER_BLOCKS`` blocks in a row stop a poller until a person
  resumes it.
* The stores' writes. ``write_atomic`` writes a temporary file beside the
  target, flushes it to disk and renames it over the target. A
  ``Transaction`` stages a command's files, writes the temporary files,
  records their names in a journal (the commit point), renames them into
  place and deletes the journal; ``recover`` finishes the renames of a commit
  interrupted after its journal was written, or drops the temporary files of
  one interrupted before. ``store_lock`` holds a store's lock file for one
  command (created exclusively, holding the command, process id, host and
  time) and never breaks one left behind: ``lock_message`` says who held it,
  since when, and whether it looks stale. Each store passes its own
  temporary-file suffix, journal, directories and the name its messages use.

Nothing here reads the clock: ``now`` is an argument.
"""

from __future__ import annotations

import datetime as dt
import gzip
import http.client
import io
import json
import math
import os
import socket
import ssl
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from cr_strat.ustime import IST

DEFAULT_MAX_BYTES = 5_000_000  # bytes of a body read from the socket, unless the caller allows more
MAX_TIMEOUT = 600.0  # seconds: a poll holds its store's lock while it waits for the answer
MAX_USER_AGENT = 200  # characters of the client name
RESUME_HOUR = 8  # India time: after a block, no request before this hour of the next India day
MAX_COOLDOWN = dt.timedelta(hours=24 + RESUME_HOUR)  # a block at 00:00 India time waits until 08:00 the next day
STOP_AFTER_BLOCKS = 2  # blocks in a row after which a poller stops until the operator resumes it
BLOCK_STATUSES = frozenset({403, 418, 429})


@dataclass(frozen=True)
class FeedResponse:
    """What a transport got back: HTTP status (``None`` when no answer came), headers (lower-case names), body."""

    status: int | None
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    error: str = ""


Transport = Callable[[str, dict[str, str], float], FeedResponse]


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Turn a redirect into its HTTP error: following it would be a second request, perhaps to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def lower_headers(headers) -> dict[str, str]:
    """Response headers with lower-case names (the last value of a repeated header)."""
    return {k.lower(): v for k, v in headers.items()} if headers is not None else {}


def urllib_transport(url: str, headers: dict[str, str], timeout: float, *, opener=None,
                     ssl_context: ssl.SSLContext | None = None, max_bytes: int = DEFAULT_MAX_BYTES) -> FeedResponse:
    """One GET with the standard library (the environment's proxy and certificates); no redirect is followed.

    At most ``max_bytes + 1`` bytes of the body are read, so a caller can tell
    an answer that was cut off. ``ssl_context`` replaces the default TLS
    context (``opener``, a test's fake, takes precedence over both).
    """
    if opener is None:
        handlers = [NoRedirectHandler()]
        if ssl_context is not None:
            handlers.append(urllib.request.HTTPSHandler(context=ssl_context))
        opener = urllib.request.build_opener(*handlers)
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with opener.open(request, timeout=timeout) as response:
            return FeedResponse(response.status, lower_headers(response.headers), response.read(max_bytes + 1))
    except urllib.error.HTTPError as exc:  # an answer with an error status: 304, 418, 403, 5xx, a redirect
        try:
            body = exc.read(max_bytes + 1)
        except (OSError, http.client.HTTPException):
            body = b""
        return FeedResponse(exc.code, lower_headers(exc.headers), body)
    except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError, OverflowError) as exc:
        return FeedResponse(None, error=f"{type(exc).__name__}: {getattr(exc, 'reason', exc)}")


def legacy_renegotiation_context() -> ssl.SSLContext:
    """The default TLS context (certificates and host names checked) with legacy renegotiation allowed.

    For a server that needs it (MoSPI's client says api.mospi.gov.in does);
    pass it to ``urllib_transport`` as ``ssl_context``. Verification stays on.
    """
    context = ssl.create_default_context()
    context.options |= getattr(ssl, "OP_LEGACY_SERVER_CONNECT", 0x4)  # OpenSSL's SSL_OP_LEGACY_SERVER_CONNECT
    return context


def printable(text: str) -> bool:
    """Visible ASCII characters and spaces only: what a URL or a header value may carry unencoded."""
    return all(" " <= c <= "~" for c in text)


def check_user_agent(user_agent: str) -> str:
    """``user_agent`` if it can be sent as the client name: one non-empty line of visible ASCII and spaces.

    Anything else (a tab, a character outside ASCII) the standard library
    would refuse, or garble, only once the request is under way.
    """
    if not isinstance(user_agent, str) or not user_agent.strip() or not printable(user_agent) \
            or len(user_agent) > MAX_USER_AGENT:
        raise ValueError(f"user_agent must be one non-empty line of visible ASCII characters and spaces, at most "
                         f"{MAX_USER_AGENT} long, not {user_agent!r}")
    return user_agent


def check_timeout(timeout: float) -> float:
    """``timeout`` if a poll may wait that long for the answer: more than 0 and at most ``MAX_TIMEOUT`` seconds."""
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) \
            or not 0 < timeout <= MAX_TIMEOUT:
        raise ValueError(f"timeout must be a positive number of seconds, at most {MAX_TIMEOUT:g} (a poll holds the "
                         f"store's lock while it waits), not {timeout!r}")
    return timeout


def decode_body(body: bytes, headers: dict[str, str], max_bytes: int) -> bytes:
    """``body`` with its ``Content-Encoding`` (none or gzip) undone; ``ValueError`` past ``max_bytes`` or unreadable."""
    if len(body) > max_bytes:
        raise ValueError(f"the answer is larger than {max_bytes} bytes")
    encoding = headers.get("content-encoding", "").strip().lower()
    if encoding in ("", "identity"):
        return body
    if encoding in ("gzip", "x-gzip"):
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(body)) as f:
                data = f.read(max_bytes + 1)
        except (OSError, EOFError) as exc:
            raise ValueError(f"the gzip body cannot be read ({exc})") from None
        if len(data) > max_bytes:
            raise ValueError(f"the answer is larger than {max_bytes} bytes once decompressed")
        return data
    raise ValueError(f"content-encoding {encoding!r} cannot be decoded (only gzip is asked for)")


def resume_time(now: dt.datetime) -> dt.datetime:
    """08:00 India time on the day after ``now``'s India day: the first time a poll may ask after a block."""
    india = now.astimezone(IST)
    return dt.datetime.combine(india.date() + dt.timedelta(days=1), dt.time(RESUME_HOUR), tzinfo=IST)


# --- The stores' writes: atomic files, a commit journal, a lock ------------------------------------------

def utc_stamp(now: dt.datetime) -> str:
    """``now`` in UTC as the stores' state files write it: ``2026-11-18T03:09:51Z``."""
    return now.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_atomic(path: Path, data: bytes, suffix: str) -> None:
    """Write ``data`` to a temporary file beside ``path`` (its name plus ``suffix``), flush it to disk, then rename it
    over ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + suffix)
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class Transaction:
    """Files staged by one command, written so that the store gets all of them or none."""

    def __init__(self, root: Path, journal: Path, suffix: str):
        self.root = root
        self.journal = journal
        self.suffix = suffix
        self.files: dict[Path, bytes] = {}

    def put(self, path: Path, data: bytes) -> None:
        self.files[path] = data

    def commit(self) -> list[str]:
        """Write the staged files whose bytes differ from what is on disk; their paths, relative to the store."""
        changed = {p: d for p, d in sorted(self.files.items()) if not (p.exists() and p.read_bytes() == d)}
        if not changed:
            return []
        relative = [str(p.relative_to(self.root)) for p in changed]
        for path, data in changed.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path.with_name(path.name + self.suffix), "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
        write_atomic(self.journal, json.dumps(relative, indent=1).encode("utf-8"), self.suffix)  # the commit point
        for path in changed:
            os.replace(path.with_name(path.name + self.suffix), path)
        self.journal.unlink()
        return relative


def recover(root: Path, journal: Path, suffix: str, directories: Iterable[Path],
            error: type[Exception] = RuntimeError) -> list[str]:
    """Finish the renames of a commit interrupted after its journal was written; drop stray temporary files.

    ``directories`` are searched (not recursively) for temporary files left by a commit interrupted before its
    journal; an unreadable journal raises ``error``.
    """
    done = []
    if journal.exists():
        try:
            relative = json.loads(journal.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise error(f"{journal}: unreadable journal of an interrupted commit ({exc})") from None
        for rel in relative:
            target = root / rel
            tmp = target.with_name(target.name + suffix)
            if tmp.exists():
                os.replace(tmp, target)
                done.append(rel)
        journal.unlink()
    for directory in directories:
        if directory.is_dir():
            for stray in directory.glob(f"*{suffix}"):
                stray.unlink()
    return done


def _utc_or_none(text: object) -> dt.datetime | None:
    if not isinstance(text, str):
        return None
    try:
        return dt.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def process_alive(pid: int) -> bool:
    """Whether a process of this id runs on this machine (a process one may not signal counts as running)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def lock_message(root: Path, lock: Path, now: dt.datetime | None, product: str, stale_after: dt.timedelta) -> str:
    """Who holds the lock of the store at ``root``, since when, and whether it looks stale (it is never broken here).

    ``product`` names the commands in the message (``rbi-store``: "If no rbi-store command is running, ...").
    """
    try:
        info = json.loads(lock.read_text(encoding="utf-8"))
        if not isinstance(info, dict):
            raise ValueError("not an object")
    except (OSError, ValueError) as exc:
        return (f"store {root} is locked ({lock} exists but cannot be read: {exc}). If no "
                f"{product} command is running, delete {lock} and run again.")
    since, pid, host = _utc_or_none(info.get("since")), info.get("pid"), info.get("host")
    held = f"by {info.get('command') or 'a command'}"
    if pid is not None or host:
        held += f" (pid {pid if pid is not None else 'unknown'} on {host or 'an unknown host'})"
    if info.get("since"):
        held += f" since {info['since']}"
    why = []
    if now is not None and since is not None and now - since > stale_after:
        why.append(f"it is {(now - since).total_seconds() / 3600:.1f} hours old")
    if host == socket.gethostname() and isinstance(pid, int) and not process_alive(pid):
        why.append(f"process {pid} is not running")
    if why:
        return (f"store {root} has a stale lock {held}: {' and '.join(why)}. If no {product} command is "
                f"running, delete {lock} and run again; the next command completes or drops an "
                "interrupted commit.")
    return (f"store {root} is locked {held}. Wait for that command to end; if none is running, "
            f"delete {lock}.")


@contextmanager
def store_lock(root: Path, lock: Path, now: dt.datetime, command: str, *, product: str,
               stale_after: dt.timedelta, locked_error: type[Exception], recover_first: Callable[[], object]
               ) -> Iterator[None]:
    """Hold ``lock`` for one command, created exclusively; ``recover_first`` runs once it is held (a store finishes or
    drops an interrupted commit there). A lock already there raises ``locked_error`` with ``lock_message``."""
    root.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        raise locked_error(lock_message(root, lock, now, product, stale_after)) from None
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"command": command, "pid": os.getpid(), "host": socket.gethostname(), "since": utc_stamp(now)}, f)
    try:
        recover_first()
        yield
    finally:
        try:
            lock.unlink()
        except FileNotFoundError:
            pass
