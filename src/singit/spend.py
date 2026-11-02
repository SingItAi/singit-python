"""Append-only local spend accounting shared across processes on one host."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import secrets
import socket
import time
import warnings
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

from .guards import SingItPaymentError


def default_spend_path(env=None):
    env = os.environ if env is None else env
    return Path(env.get("SINGIT_HOME") or Path.home() / ".singit") / "spend.jsonl"


def _now():
    return datetime.now(timezone.utc)


def _usd(amount):
    return f"{amount // 1_000_000}.{amount % 1_000_000:06d}"


def _valid(row):
    if not isinstance(row, dict) or type(row.get("v")) is not int or row["v"] != 1:
        return False
    if not all(isinstance(row.get(k), str) for k in ("t", "day", "id", "event", "network", "payer", "amountAtomic")):
        return False
    if not re.fullmatch(r"[0-9a-f]{16}", row["id"]) or not re.fullmatch(r"(?:0|[1-9][0-9]*)", row["amountAtomic"]):
        return False
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z", row["t"]):
        return False
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", row["day"]):
        return False
    try:
        int(row["amountAtomic"])
        datetime.strptime(row["day"], "%Y-%m-%d")
        datetime.strptime(row["t"], "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError:
        return False
    if row["network"] not in ("base", "base-sepolia", "solana") or not row["payer"]:
        return False
    if row["event"] == "reserve":
        return (row.get("scheme") in ("upto", "exact") and isinstance(row.get("resource"), str)
                and type(row.get("pid")) is int)
    return row["event"] in ("settle", "release") and ("tx" not in row or isinstance(row["tx"], str))


def read_spend(file=None):
    """Return validated lines, folded payments and skippedLines; missing files are empty."""
    path = Path(file) if file is not None else default_spend_path()
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        data = b""
    complete = data.split(b"\n")
    tail = complete.pop()
    rows, skipped = [], int(bool(tail))
    for line in complete:
        try:
            row = json.loads(line.decode("utf-8"))
            if not _valid(row):
                raise ValueError()
            rows.append(row)
        except (ValueError, UnicodeError):
            skipped += 1
    return {"file": str(path), "lines": rows, "payments": _payments(rows), "skippedLines": skipped}


def _payments(lines):
    reserves, endings = {}, {}
    for row in lines:
        key = (row["id"], row["day"], row["network"], row["payer"])
        if row["event"] == "reserve":
            reserves.setdefault(key, row)
        else:
            endings.setdefault(key, {})[row["event"]] = row
    result = []
    for key, reserve in reserves.items():
        end = endings.get(key, {})
        settled = end.get("settle")
        status = "settled" if settled else "released" if "release" in end else "unresolved"
        spent = int(settled["amountAtomic"]) if settled else 0 if status == "released" else int(reserve["amountAtomic"])
        result.append({**reserve, "status": status, "ceilingAtomic": reserve["amountAtomic"], "spentAtomic": str(spent), "tx": settled.get("tx", "") if settled else ""})
    return result  # file order, oldest first, the same as the JS SDK


def spend_summary(file=None, days=7, *, now=None):
    if type(days) is not int or days < 1:
        raise ValueError("days must be a positive integer")
    data = read_spend(file)
    today = (now or _now)().astimezone(timezone.utc).date()
    first = (today - timedelta(days=min(days - 1, today.toordinal() - 1))).isoformat()
    groups = {}
    for p in _payments(data["lines"]):
        if not first <= p["day"] <= today.isoformat():
            continue
        key = (p["day"], p["network"], p["payer"])
        row = groups.setdefault(key, {"day": key[0], "network": key[1], "payer": key[2], "payments": 0,
                                      "spentAtomic": 0, "spentUsd": "", "settled": 0, "unresolved": 0, "released": 0})
        row["payments"] += 1
        row["spentAtomic"] += int(p["spentAtomic"])
        row[p["status"]] += 1
    rows = [groups[k] for k in sorted(groups, key=lambda k: (-int(k[0].replace("-", "")), k[1], k[2]))]
    for row in rows:
        row["spentUsd"] = _usd(row["spentAtomic"])
        row["spentAtomic"] = str(row["spentAtomic"])
    return {"file": data["file"], "days": rows, "skippedLines": data["skippedLines"]}


def _hostname():
    return socket.gethostname().replace(":", "_")


def _lock_token():
    return f"{os.getpid()}:{secrets.token_hex(8)}:{_hostname()}"


def _owner_dead(token):
    if token == "":
        return True  # Only used after the 30-second age check.
    match = re.fullmatch(r"([1-9][0-9]*)(?::([0-9a-f]{16})(?::([^:]+))?)?", token)
    if not match or (match[3] is not None and match[3] != _hostname()):
        return False
    if len(match[1]) > 10:
        return False
    pid = int(match[1])
    if os.name == "nt":
        # os.kill(pid, 0) terminates processes on Windows.
        try:
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            kernel.GetExitCodeProcess.restype = wintypes.BOOL
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            if pid > 0xffffffff:
                return False
            handle = kernel.OpenProcess(0x1000, False, pid)
            if not handle:
                return ctypes.get_last_error() == 87  # ERROR_INVALID_PARAMETER: no such PID.
            try:
                code = wintypes.DWORD()
                return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value != 259
            finally:
                kernel.CloseHandle(handle)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except (OSError, OverflowError, ValueError):
        pass
    return False


def _release_lock(path, token):
    try:
        if path.read_text(encoding="utf-8", errors="replace") == token:
            path.unlink()
    except FileNotFoundError:
        pass


def _recover_lock(path, observed):
    """Only a recovery guard holder may remove a stale main lock."""
    guard = Path(str(path) + ".recover")
    token = _lock_token()
    try:
        fd = os.open(guard, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        try:
            if time.time() - guard.lstat().st_mtime > 30:
                stale = guard.read_text(encoding="utf-8", errors="replace")
                if _owner_dead(stale):
                    _release_lock(guard, stale)
        except FileNotFoundError:
            pass
        return
    try:
        try:
            os.write(fd, token.encode("utf-8"))
        finally:
            os.close(fd)
        try:
            if (path.read_text(encoding="utf-8", errors="replace") == observed and time.time() - path.lstat().st_mtime > 30
                    and _owner_dead(observed)):
                path.unlink()
        except FileNotFoundError:
            pass
    finally:
        _release_lock(guard, token)


def _sync_directory(path):
    try:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


class PersistentDailyLimiter:
    def __init__(self, cap_atomic, file=None, *, network="base", payer="unknown", now=None):
        self.cap_atomic = int(cap_atomic)
        self.file = Path(file) if file is not None else default_spend_path()
        self.network, self.payer = network, payer
        self.now = now or _now
        try:
            missing = []
            parent = self.file.parent
            while not parent.exists():
                missing.append(parent)
                parent = parent.parent
            for directory in reversed(missing):
                directory.mkdir(mode=0o700, exist_ok=True)
                _sync_directory(directory.parent)
            fd = os.open(self.file, os.O_CREAT | os.O_APPEND | os.O_RDWR, 0o600)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            _sync_directory(self.file.parent)
            self.file.read_bytes()
        except OSError as error:
            raise SingItPaymentError(f"Could not prepare spend log {self.file}: {error}. Pass spend_log=False (in-memory, resets on restart) or a writable path.") from error

    @contextmanager
    def _lock(self):
        path = Path(str(self.file) + ".lock")
        token = _lock_token()
        deadline = time.monotonic() + 5
        while True:
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                break
            except FileExistsError:
                try:
                    if time.time() - path.lstat().st_mtime > 30:
                        _recover_lock(path, path.read_text(encoding="utf-8", errors="replace"))
                except FileNotFoundError:
                    pass
                if time.monotonic() >= deadline:
                    raise SingItPaymentError(f"Could not lock the spend log {self.file}. If no other SingIt process is running, delete {self.file}.lock.")
                time.sleep(0.015)
            except OSError as error:
                raise SingItPaymentError(f"Could not lock the spend log {self.file}: {error}") from error
        try:
            try:
                os.write(fd, token.encode("utf-8"))
            finally:
                os.close(fd)
            yield
        finally:
            _release_lock(path, token)

    def _append(self, row):
        line = (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        if len(line) >= 4096:
            raise SingItPaymentError(f"Spend log entry too long for {self.file}.")
        fd = os.open(self.file, os.O_CREAT | os.O_APPEND | os.O_RDWR, 0o600)
        try:
            size = os.lseek(fd, 0, os.SEEK_END)
            if size:
                os.lseek(fd, size - 1, os.SEEK_SET)
            prefix = b"\n" if size and os.read(fd, 1) != b"\n" else b""
            data = prefix + line
            try:
                if os.write(fd, data) != len(data):
                    raise OSError("incomplete spend log write")
                os.fsync(fd)
            except OSError:
                os.ftruncate(fd, size)
                try:
                    os.fsync(fd)
                except OSError:
                    pass
                raise
        finally:
            os.close(fd)

    def _spent(self, day, network, payer):
        return sum(int(p["spentAtomic"]) for p in _payments(read_spend(self.file)["lines"])
                   if (p["day"], p["network"], p["payer"]) == (day, network, payer))

    def remaining_atomic(self):
        return max(0, self.cap_atomic - self._spent(self.now().astimezone(timezone.utc).strftime("%Y-%m-%d"), self.network, self.payer))

    def reserve(self, worst_case_atomic, meta=None):
        meta = meta or {"network": self.network, "payer": self.payer, "scheme": "exact", "resource": ""}
        now = self.now().astimezone(timezone.utc)
        parts = urlsplit(meta["resource"])
        host = (parts.hostname or "").encode("idna").decode("ascii").lower()
        if ":" in host:
            host = "[" + host + "]"
        port = parts.port
        if port is not None and (parts.scheme, port) not in (("https", 443), ("http", 80)):
            host += f":{port}"
        resource = urlunsplit((parts.scheme, host, parts.path or ("/" if host else ""), "", ""))
        row = {"v": 1, "t": now.isoformat(timespec="milliseconds").replace("+00:00", "Z"), "day": now.strftime("%Y-%m-%d"),
               "id": secrets.token_hex(8), "event": "reserve", "network": meta["network"], "payer": meta["payer"],
               "amountAtomic": str(worst_case_atomic), "scheme": meta["scheme"], "resource": resource, "pid": os.getpid()}
        amount = int(worst_case_atomic)
        try:
            with self._lock():
                spent = self._spent(row["day"], row["network"], row["payer"])
                if spent + amount > self.cap_atomic:
                    raise SingItPaymentError(f"Daily spend cap reached: {spent} already spent today, {amount} more would exceed {self.cap_atomic} micro-USDC.")
                self._append(row)
        except OSError as error:
            raise SingItPaymentError(f"Could not reserve in spend log {self.file}: {error}") from error
        state = {"done": False}

        def finish(event, actual, tx=None):
            if state["done"]:
                return
            state["done"] = True
            entry = {k: row[k] for k in ("v", "t", "day", "id", "event", "network", "payer", "amountAtomic")}
            entry.update(t=self.now().astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"), event=event, amountAtomic=str(actual))
            if tx:
                entry["tx"] = tx
            try:
                with self._lock():
                    self._append(entry)
            except Exception:
                # Warning filters must not turn a bookkeeping failure into loss of a paid answer.
                try:
                    warnings.warn(f"Could not append {event} to spend log {self.file}; ceiling remains counted.", RuntimeWarning)
                except Warning:
                    pass

        def release():
            finish("release", worst_case_atomic)
        release.day = row["day"]
        release.finish = finish
        return release

    def settle(self, release, actual_atomic, worst_case_atomic, tx=None):
        release.finish("settle", actual_atomic if actual_atomic is not None else worst_case_atomic, tx)

# Both names describe the same persistent daily limiter.
PersistentLimiter = PersistentDailyLimiter
