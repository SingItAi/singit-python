import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from datetime import datetime, timezone

import pytest
from eth_account import Account
from singit import PersistentLimiter, SingIt, SingItPaymentError, read_spend, spend_summary
from singit.cli import main

NOW = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
META = {"network": "base", "payer": "0x" + "1" * 40, "scheme": "upto", "resource": "https://user:password@example.com/path?q=secret#fragment"}


def limiter(tmp_path, **kwargs):
    return PersistentLimiter("1000", tmp_path / "private" / "spend.jsonl", now=lambda: NOW, **kwargs)


def test_accounting_and_scope(tmp_path):
    log = limiter(tmp_path, network=META["network"], payer=META["payer"])
    r = log.reserve("600", META)
    assert log.remaining_atomic() == 400
    with pytest.raises(SingItPaymentError, match="Daily spend cap reached: 600"):
        log.reserve("600", META)
    log.settle(r, "200", "600", tx="0xfake")
    assert log.remaining_atomic() == 800
    r2 = log.reserve("600", META)
    r2()
    r2()
    assert log.remaining_atomic() == 800
    log.reserve("1000", {**META, "payer": "other"})
    log.reserve("1000", {**META, "network": "solana"})
    assert log.remaining_atomic() == 800
    rows = read_spend(log.file)["lines"]
    assert [r["event"] for r in rows] == ["reserve", "settle", "reserve", "release", "reserve", "reserve"]
    assert rows[0]["resource"] == "https://example.com/path"
    assert rows[0]["id"] == rows[1]["id"]
    assert rows[1]["tx"] == "0xfake"
    summary = spend_summary(log.file, now=lambda: NOW)
    base = next(r for r in summary["days"] if r["payer"] == META["payer"] and r["network"] == "base")
    assert base == {"day": "2026-11-02", "network": "base", "payer": META["payer"], "payments": 2,
                    "spentAtomic": "200", "spentUsd": "0.000200", "settled": 1, "unresolved": 0, "released": 1}


def test_rollover_and_corruption(tmp_path):
    clock = [NOW]
    log = PersistentLimiter("1000", tmp_path / "spend", now=lambda: clock[0])
    release = log.reserve("900")
    clock[0] = NOW.replace(day=3)
    log.reserve("1000")
    release()
    assert log.remaining_atomic() == 0
    with log.file.open("ab") as f:
        f.write(b'garbage\n{"v":2}\n{"v":1')
    assert read_spend(log.file)["skippedLines"] == 3
    clock[0] = NOW.replace(day=4)
    log.reserve("100")
    assert log.remaining_atomic() == 900
    assert read_spend(log.file)["skippedLines"] == 3


def test_settle_wins_orphans_ignored(tmp_path):
    log = limiter(tmp_path)
    release = log.reserve("500")
    reserve = read_spend(log.file)["lines"][0]
    log._append({**reserve, "event": "release"})
    log._append({**reserve, "event": "settle", "amountAtomic": "100"})
    log._append({**reserve, "event": "settle", "id": "f" * 16, "amountAtomic": "999"})
    assert log.remaining_atomic() == 900


def test_permissions_and_construction_error(tmp_path):
    log = limiter(tmp_path)
    if os.name != "nt":
        assert log.file.stat().st_mode & 0o777 == 0o600
        assert log.file.parent.stat().st_mode & 0o777 == 0o700
    with pytest.raises(SingItPaymentError, match=r"spend_log=False.*writable path"):
        PersistentLimiter("1000", tmp_path)


def test_lock_stale_and_symlink(tmp_path):
    log = limiter(tmp_path)
    lock = Path(str(log.file) + ".lock")
    lock.write_text("old pid")
    os.utime(lock, (0, 0))
    log.reserve("1")
    target = tmp_path / "target"
    target.write_text("unchanged")
    lock.symlink_to(target)
    os.utime(lock, (0, 0), follow_symlinks=False)
    log.reserve("1")
    assert target.read_text() == "unchanged"


def test_append_failure_safe_before_and_after_payment(tmp_path, monkeypatch):
    log = limiter(tmp_path)
    release = log.reserve("500")
    def fail(row):
        raise OSError("disk full")
    monkeypatch.setattr(log, "_append", fail)
    with pytest.warns(RuntimeWarning, match="ceiling remains counted"):
        log.settle(release, "100", "500")
    assert log.remaining_atomic() == 500
    with pytest.raises(SingItPaymentError, match="Could not reserve"):
        log.reserve("100")


def test_defaults_and_disabled(tmp_path, monkeypatch):
    account = Account.create()
    args = dict(evm_account=account, network="base", max_per_call_usd=.01, max_per_day_usd=1)
    home = tmp_path / "custom"
    monkeypatch.setenv("SINGIT_HOME", str(home))
    SingIt(**args, spend_log=False)
    assert not home.exists()
    sdk = SingIt(**args)
    assert sdk.limiter.file == home / "spend.jsonl"
    assert sdk.payer == account.address.lower()


def test_process_restart_real_sdk_signing(tmp_path):
    env = {**os.environ, "SINGIT_HOME": str(tmp_path), "PYTHONPATH": os.pathsep.join((str(Path.cwd() / "src"), str(Path.cwd() / "tests")))}
    key = Account.create().key.hex()
    def run(key):
        result = subprocess.run([sys.executable, "tests/helpers/spend_child.py"], env={**env, "TEST_EVM_KEY": key}, text=True, capture_output=True, check=True)
        return json.loads(result.stdout)
    assert run(key) == {"ok": True, "paid": 1}
    second = run(key)
    assert not second["ok"] and second["paid"] == 0
    assert "Daily spend cap reached" in second["error"]
    assert run(Account.create().key.hex()) == {"ok": True, "paid": 1}


def test_concurrent_reservations(tmp_path):
    file = tmp_path / "spend"
    barrier = tmp_path / "go"
    code = '''import sys,time
from pathlib import Path
import re
from singit import PersistentLimiter,SingItPaymentError
log=PersistentLimiter("300", sys.argv[1])
while not Path(sys.argv[2]).exists(): time.sleep(.005)
try:
 log.reserve("100")
 print("ok")
except SingItPaymentError:
 print("cap")
'''
    children = [subprocess.Popen([sys.executable, "-c", code, str(file), str(barrier)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(8)]
    barrier.touch()
    outputs = []
    for child in children:
        out, err = child.communicate(timeout=15)
        assert child.returncode == 0, err
        outputs.append(out.strip())
    assert outputs.count("ok") == 3
    assert PersistentLimiter("300", file).remaining_atomic() == 0


def cli(args, env=None):
    out, err = io.StringIO(), io.StringIO()
    code = main(args, out, err, env or {})
    return code, out.getvalue(), err.getvalue()


def test_cli(tmp_path):
    file = tmp_path / "spend"
    assert cli(["spend", "summary", "--file", str(file)])[1] == f"No spend log yet at {file}.\n"
    log = PersistentLimiter("1000", file)
    release = log.reserve("500", {**META, "payer": "=formula", "resource": 'https://example.com/a,"b'})
    log.settle(release, "100", "500")
    data = json.loads(cli(["spend", "summary", "--file", str(file), "--json"])[1])
    assert data["days"][0]["spentAtomic"] == "100"
    text = cli(["spend", "summary", "--file", str(file)])[1]
    assert "Local client-side log: not on-chain enforcement." in text
    assert "Totals: 1 payments 0.000100 USDC 1 settled" in text
    import csv
    rows = list(csv.reader(io.StringIO(cli(["spend", "export", "--csv", "--file", str(file)])[1])))
    assert rows[0] == "day,time,id,network,payer,scheme,resource,status,ceilingAtomic,spentAtomic,tx".split(",")
    assert rows[1][4] == "'=formula"
    assert rows[1][6] == 'https://example.com/a,"b'
    assert cli(["spend", "summary", "--file", str(tmp_path)])[0] == 1
    for args in ([], ["wallet"], ["spend"], ["spend", "summary", "--days", "0"], ["spend", "summary", "--csv"], ["spend", "export"], ["spend", "summary", "--bad"]):
        assert cli(args)[0] == 2


def test_shared_fixture_and_javascript_interop(tmp_path):
    import shutil
    fixture = Path(__file__).parent / "fixtures" / "python-spend.jsonl"
    data = spend_summary(fixture, now=lambda: NOW)
    assert data["days"][0]["spentAtomic"] == "4500"
    js_fixture = Path(__file__).parent / "fixtures" / "spend-js.jsonl"
    assert spend_summary(js_fixture, now=lambda: NOW)["days"][0]["spentAtomic"] == "1000"
    twin = Path(__file__).resolve().parents[2] / "singit-js" / "src" / "spend.js"
    if not shutil.which("node") or not twin.exists():
        pytest.skip("JavaScript twin and Node required for live interoperability")
    file = tmp_path / "python.jsonl"
    log = PersistentLimiter("1000", file, now=lambda: NOW)
    release = log.reserve("600", META)
    log.settle(release, "200", "600")
    code = '''import {spendSummary} from %s;
console.log(JSON.stringify(spendSummary({file:process.argv[1],now:()=>new Date('2026-11-02T12:00:00Z')})));''' % json.dumps(twin.as_uri())
    result = subprocess.run(["node", "--input-type=module", "-e", code, str(file)], text=True, capture_output=True, check=True)
    assert json.loads(result.stdout) == spend_summary(file, now=lambda: NOW)


def test_cli_empty_and_skipped(tmp_path):
    file = tmp_path / "empty"
    file.write_bytes(b"")
    assert json.loads(cli(["spend", "summary", "--file", str(file), "--json"])[1])["days"] == []
    file.write_bytes(b"bad\npartial")
    assert "Skipped unreadable lines: 2" in cli(["spend", "summary", "--file", str(file)])[1]


def test_terminal_failure_does_not_hide_answer(tmp_path, monkeypatch):
    import singit.client as client_module
    from helpers.merchant import exact_offer, encode
    account = Account.create()
    sdk = SingIt(evm_account=account, network="base", max_per_call_usd=.01, max_per_day_usd=.01,
                 spend_log=tmp_path / "spend")
    offer = exact_offer(network=sdk.pin.caip, asset=sdk.pin.asset, pay_to=sdk.pay_to, amount="2000")
    original_append = sdk.limiter._append
    def append(row):
        if row["event"] != "reserve":
            raise OSError("disk full")
        original_append(row)
    monkeypatch.setattr(sdk.limiter, "_append", append)
    def post(url, body, headers):
        if not headers:
            return 402, {"payment-required": encode({"x402Version": 2, "resource": {"url": url}, "accepts": [offer]})}, b"{}"
        return 200, {}, b'{"billing":{"totalAtomic":"2000"},"answer":"paid"}'
    monkeypatch.setattr(client_module, "_post_json", post)
    with pytest.warns(RuntimeWarning):
        assert sdk.chat(messages=[{"role": "user", "content": "test"}], scheme="exact")["answer"] == "paid"
    assert sdk.limiter.remaining_atomic() == 8000


def test_process_restart_with_local_mock_merchant(tmp_path):
    from helpers.merchant import Merchant, exact_offer
    offer = exact_offer(network="eip155:84532", asset="0x036CbD53842c5426634e7929541eC2318f3dCF7e",
                        pay_to="0x" + "1" * 40, amount="2000")
    try:
        merchant = Merchant(lambda body: [offer])
    except PermissionError:
        pytest.skip("sandbox forbids loopback sockets; offline verifier subprocess test covers signing")
    try:
        env = {**os.environ, "SINGIT_HOME": str(tmp_path),
               "PYTHONPATH": os.pathsep.join((str(Path.cwd() / "src"), str(Path.cwd() / "tests")))}
        def run(key):
            result = subprocess.run([sys.executable, "tests/helpers/spend_child.py", merchant.url],
                                    env={**env, "TEST_EVM_KEY": key}, text=True, capture_output=True, check=True)
            return json.loads(result.stdout)
        key = Account.create().key.hex()
        assert run(key)["ok"]
        assert len(merchant.payment_headers) == 1
        result = run(key)
        assert not result["ok"] and "Daily spend cap reached" in result["error"]
        assert len(merchant.payment_headers) == 1
        assert run(Account.create().key.hex())["ok"]
        assert len(merchant.payment_headers) == 2
    finally:
        merchant.close()


def test_default_path_without_real_home(tmp_path, monkeypatch):
    from singit.spend import default_spend_path
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert default_spend_path({}) == tmp_path / ".singit" / "spend.jsonl"
    assert default_spend_path({"SINGIT_HOME": ""}) == tmp_path / ".singit" / "spend.jsonl"


def test_fresh_lock_timeout(tmp_path, monkeypatch):
    import singit.spend as module
    log = limiter(tmp_path)
    lock = Path(str(log.file) + ".lock")
    lock.write_text("busy")
    ticks = iter([0, 6])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(ticks))
    with pytest.raises(SingItPaymentError, match="Could not lock the spend log"):
        log.reserve("1")
    assert lock.read_text() == "busy"


@pytest.mark.parametrize("failure", ["sign", "refused", "connection", "http", "invoice", "receipt"])
def test_persistent_failure_outcomes(tmp_path, monkeypatch, failure):
    import singit.client as module
    from helpers.merchant import exact_offer, encode
    sdk = SingIt(evm_account=Account.create(), network="base", spend_log=tmp_path / "log",
                 max_per_call_usd=.003, max_per_day_usd=.003)
    offer = exact_offer(network=sdk.pin.caip, asset=sdk.pin.asset, pay_to=sdk.pay_to, amount="2000")
    paid = []
    def post(url, body, headers):
        if not headers:
            return 402, {"payment-required": encode({"x402Version": 2, "resource": {"url": url}, "accepts": [offer]})}, b"{}"
        paid.append(True)
        if failure == "connection":
            raise OSError("dropped")
        if failure == "http":
            return 500, {}, b"{}"
        if failure == "refused":
            return 402, {}, b"{}"
        receipt = {"payment-response": encode({"network": "wrong"})} if failure == "receipt" else {}
        return 200, receipt, b'{"billing":{"totalAtomic":"1000"}}' if failure == "invoice" else b'{"billing":{"totalAtomic":"2000"}}'
    monkeypatch.setattr(module, "_post_json", post)
    if failure == "sign":
        def fail(*args):
            raise SingItPaymentError("sign failed")
        monkeypatch.setattr(sdk, "_sign", fail)
    with pytest.raises(SingItPaymentError):
        sdk.chat(messages=[{"role": "user", "content": "test"}], scheme="exact")
    assert len(paid) == (0 if failure == "sign" else 1)
    events = [r["event"] for r in read_spend(sdk.limiter.file)["lines"]]
    assert events == (["reserve", "release"] if failure in ("sign", "refused") else ["reserve"])
    assert sdk.limiter.remaining_atomic() == (3000 if failure in ("sign", "refused") else 1000)


def test_csv_escapes_all_special_characters():
    from singit.spend_cli import _csv_cell
    import csv
    for value in ('=SUM(A1)', '+1', '-1', '@call', 'a\rb', 'a\nb', 'a,b', 'a"b'):
        row = next(csv.reader(io.StringIO(_csv_cell(value) + '\n', newline='')))
        assert row == [("'" + value) if value[0] in '=+-@' else value]


def test_lock_release_preserves_replacement(tmp_path):
    log = limiter(tmp_path)
    path = Path(str(log.file) + ".lock")
    with log._lock():
        assert re.fullmatch(r"\d+:[0-9a-f]{16}", path.read_text())
        path.write_text("another:0123456789abcdef")
    assert path.read_text() == "another:0123456789abcdef"


def test_stale_recovery_guard(tmp_path):
    log = limiter(tmp_path)
    lock = Path(str(log.file) + ".lock")
    guard = Path(str(lock) + ".recover")
    for path in (lock, guard):
        path.write_text("123:0123456789abcdef")
        os.utime(path, (0, 0))
    log.reserve("1")
    assert len(read_spend(log.file)["payments"]) == 1
    assert not guard.exists()
    assert not lock.exists()


def test_recovery_preserves_changed_main_token(tmp_path):
    from singit.spend import _recover_lock
    log = limiter(tmp_path)
    lock = Path(str(log.file) + ".lock")
    lock.write_text("replacement:0123456789abcdef")
    os.utime(lock, (0, 0))
    _recover_lock(lock, "observed:fedcba9876543210")
    assert lock.read_text() == "replacement:0123456789abcdef"
    assert not Path(str(lock) + ".recover").exists()


def test_fresh_recovery_guard_is_preserved(tmp_path, monkeypatch):
    import singit.spend as module
    log = limiter(tmp_path)
    lock = Path(str(log.file) + ".lock")
    guard = Path(str(lock) + ".recover")
    lock.write_text("old")
    os.utime(lock, (0, 0))
    guard.write_text("busy")
    ticks = iter([0, 6])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(ticks))
    with pytest.raises(SingItPaymentError, match="Could not lock the spend log"):
        log.reserve("1")
    assert lock.read_text() == "old"
    assert guard.read_text() == "busy"


def test_fsync_failure_rolls_back_reservation(tmp_path, monkeypatch):
    log = limiter(tmp_path)
    log.reserve("100")
    before = log.file.read_bytes()
    def fail(fd):
        raise OSError("fsync failed")
    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(SingItPaymentError, match="Could not reserve"):
        log.reserve("200")
    assert log.file.read_bytes() == before
    assert len(read_spend(log.file)["payments"]) == 1


@pytest.mark.parametrize("event", ["settle", "release"])
def test_fsync_failure_keeps_ceiling(tmp_path, monkeypatch, event):
    log = limiter(tmp_path)
    release = log.reserve("500")
    before = log.file.read_bytes()
    def fail(fd):
        raise OSError("fsync failed")
    monkeypatch.setattr(os, "fsync", fail)
    with pytest.warns(RuntimeWarning, match="ceiling remains counted"):
        if event == "settle":
            log.settle(release, "100", "500")
        else:
            release()
    assert log.file.read_bytes() == before
    assert read_spend(log.file)["payments"][0]["spentAtomic"] == "500"


def test_constructor_syncs_new_file_and_directory(tmp_path, monkeypatch):
    import stat
    original = os.fsync
    synced = []
    def sync(fd):
        synced.append(os.fstat(fd).st_mode)
        original(fd)
    monkeypatch.setattr(os, "fsync", sync)
    limiter(tmp_path)
    assert any(stat.S_ISREG(mode) for mode in synced)
    if os.name == "posix":
        assert sum(stat.S_ISDIR(mode) for mode in synced) >= 2


def test_reservation_fsync_failure_prevents_signing(tmp_path, monkeypatch):
    import singit.client as module
    from helpers.merchant import exact_offer, encode
    sdk = SingIt(evm_account=Account.create(), network="base", spend_log=tmp_path / "log",
                 max_per_call_usd=.003, max_per_day_usd=.003)
    offer = exact_offer(network=sdk.pin.caip, asset=sdk.pin.asset, pay_to=sdk.pay_to, amount="2000")
    def post(url, body, headers):
        assert not headers
        return 402, {"payment-required": encode({"x402Version": 2, "resource": {"url": url}, "accepts": [offer]})}, b"{}"
    def sign(*args, **kwargs):
        pytest.fail("must refuse before signing")
    def fail(fd):
        raise OSError("fsync failed")
    monkeypatch.setattr(module, "_post_json", post)
    monkeypatch.setattr(sdk, "_sign", sign)
    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(SingItPaymentError, match="Could not reserve"):
        sdk.chat(messages=[{"role": "user", "content": "test"}], scheme="exact")
    assert read_spend(sdk.limiter.file)["payments"] == []
