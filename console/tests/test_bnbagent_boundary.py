"""Tests for adapters/bnbagent_boundary.py against the LIVE console and the real SDK.

Run with the isolated venv that has `bnbagent` installed:

    ~/work/c3s-cache/bnbagent-venv/bin/python -m pytest tests/test_bnbagent_boundary.py -v

The inner wallet is a fake that records calls and returns dummy signatures: no key
exists anywhere in these tests, nothing is signed for real, nothing is broadcast. The
console at REFLEX_CONSOLE (default http://127.0.0.1:8765) must be running for the live
tests; they install `spend` policies over HTTP and put back whatever `spend` was before
(installing a policy resets every agent's circuit state on that console, both times).
Every test uses its own agent name.
"""

import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "adapters"))
from reflex_token import json_headers as _json_headers  # noqa: E402

pytest.importorskip("bnbagent", reason="the BNBAgent SDK lives in its own venv (~/work/c3s-cache/bnbagent-venv)")

from bnbagent.wallets import ExecutionContext, WalletProvider  # noqa: E402
from bnbagent.wallets.errors import UnsupportedWalletOperation  # noqa: E402
from bnbagent.wallets.local_executor import LocalExecutor  # noqa: E402
from bnbagent.x402 import X402Signer  # noqa: E402

import bnbagent_boundary as bb  # noqa: E402
from bnbagent_boundary import BoundaryRefused, BoundaryUnreachable, BoundaryWalletProvider  # noqa: E402

CONSOLE = os.environ.get("REFLEX_CONSOLE", "http://127.0.0.1:8765").rstrip("/")
CONFIRM_POLICY = {"class": "spend", "confirm_per_irreversible": True, "forbid_when_blocked": True, "min_gap_ticks": 2}

# Addresses only — no key behind any of them.
WALLET = "0x1111111111111111111111111111111111111111"
TOKEN = "0x2222222222222222222222222222222222222222"
PAYEE = "0x3333333333333333333333333333333333333333"


def console(path: str, payload=None) -> dict:
    req = urllib.request.Request(f"{CONSOLE}{path}", data=None if payload is None else json.dumps(payload).encode(),
                                 headers=_json_headers())
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())


class FakeWallet(WalletProvider):
    """A pure signer that signs nothing: it records each call and returns a dummy."""

    kind = "fake"

    def __init__(self):
        self.calls: list[tuple] = []

    @property
    def address(self) -> str:
        return WALLET

    def sign_transaction(self, transaction):
        self.calls.append(("sign_transaction", transaction))
        return {"rawTransaction": b"\x00", "hash": b"\x00" * 32, "r": 0, "s": 0, "v": 0}

    def sign_message(self, message):
        self.calls.append(("sign_message", message))
        return {"messageHash": b"\x00" * 32, "r": 0, "s": 0, "v": 0, "signature": b"\x00" * 65}

    def sign_typed_data(self, domain, types, message):
        self.calls.append(("sign_typed_data", domain, types, message))
        return {"messageHash": b"\x00" * 32, "r": 0, "s": 0, "v": 0, "signature": b"\x00" * 65}


class SelfBroadcastingFake(FakeWallet):
    """Shaped like TWAKProvider: it is its own executor and has its own x402 payer."""

    def make_executor(self, context):
        return self

    def make_x402_payer(self, **kw):
        return object()


def erc20_transfer(to=PAYEE, amount=10**18) -> dict:
    data = "0xa9059cbb" + to[2:].lower().rjust(64, "0") + hex(amount)[2:].rjust(64, "0")
    return {"to": TOKEN, "value": 0, "data": data, "gas": 60000, "gasPrice": 10**9, "nonce": 0, "chainId": 97}


def permit_typed() -> tuple[dict, dict, dict]:
    domain = {"name": "Token", "version": "1", "chainId": 97, "verifyingContract": TOKEN}
    types = {
        "EIP712Domain": [{"name": "name", "type": "string"}, {"name": "version", "type": "string"},
                         {"name": "chainId", "type": "uint256"}, {"name": "verifyingContract", "type": "address"}],
        "Permit": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"},
                   {"name": "value", "type": "uint256"}, {"name": "nonce", "type": "uint256"},
                   {"name": "deadline", "type": "uint256"}],
    }
    message = {"owner": WALLET, "spender": PAYEE, "value": 2**256 - 1, "nonce": 0, "deadline": 2**32}
    return domain, types, message


def agent_name() -> str:
    return f"test-bnb:{uuid.uuid4().hex[:8]}"


@pytest.fixture
def recorder(monkeypatch):
    """Every body the adapter sends, by path, through its one network function."""
    sent: list[tuple[str, dict]] = []
    real = bb._post

    def spy(url, payload, timeout=bb.TIMEOUT):
        sent.append((url.rsplit("/api", 1)[-1], dict(payload)))
        return real(url, payload, timeout)

    monkeypatch.setattr(bb, "_post", spy)
    return sent


@pytest.fixture(scope="module")
def live():
    try:
        state = console("/api/state")
    except (urllib.error.URLError, OSError) as e:
        pytest.skip(f"console not running at {CONSOLE}: {e}")
    before = (state.get("policies") or {}).get("spend")
    yield
    if before is None:
        console("/api/policy", {"class": "spend", "remove": True})
    elif before.get("deny_all"):
        console("/api/policy", {"class": "spend", "deny_all": True})
    else:
        console("/api/policy", dict(before["settings"], **{"class": "spend"}))


# -- 1 ------------------------------------------------------------------------------------

def test_deny_all_refuses_a_value_transfer_without_calling_inner(live, recorder):
    console("/api/policy", {"class": "spend", "deny_all": True})
    inner = FakeWallet()
    w = BoundaryWalletProvider(inner, agent=agent_name(), console=CONSOLE)
    with pytest.raises(BoundaryRefused) as exc:
        w.sign_transaction({"to": PAYEE, "value": 5 * 10**17, "gas": 21000, "nonce": 0, "chainId": 97})
    assert "class denied outright" in str(exc.value)
    assert str(exc.value).startswith("refused by the boundary at tick 1: ")
    assert isinstance(exc.value, PermissionError)
    assert inner.calls == []
    assert [p for p, _ in recorder] == ["/tool", "/request"]  # value > 0 armed irreversible first


# -- 2 ------------------------------------------------------------------------------------

def test_erc20_transfer_needs_a_fresh_confirm_each_time(live, recorder):
    console("/api/policy", CONFIRM_POLICY)
    inner, name = FakeWallet(), agent_name()
    w = BoundaryWalletProvider(inner, agent=name, console=CONSOLE)

    with pytest.raises(BoundaryRefused) as exc:
        w.sign_transaction(erc20_transfer())
    assert "irreversible, and no unspent confirm" in str(exc.value)
    assert inner.calls == []

    console("/api/tool", {"agent": name, "confirm": 1})  # the test plays the person
    signed = w.sign_transaction(erc20_transfer())
    assert signed["hash"] == b"\x00" * 32
    assert [c[0] for c in inner.calls] == ["sign_transaction"]

    with pytest.raises(BoundaryRefused) as exc:
        w.sign_transaction(erc20_transfer())
    assert "irreversible, and no unspent confirm" in str(exc.value)
    assert len(inner.calls) == 1
    assert all(body.get("irreversible") == 1 for path, body in recorder if path == "/tool")
    assert sum(1 for path, _ in recorder if path == "/tool") == 3


# -- 3 ------------------------------------------------------------------------------------

def test_zero_value_unlisted_selector_is_granted_without_confirm_then_cooldown(live, recorder):
    console("/api/policy", CONFIRM_POLICY)
    inner = FakeWallet()
    w = BoundaryWalletProvider(inner, agent=agent_name(), console=CONSOLE)
    call = {"to": TOKEN, "value": 0, "data": "0x70a08231" + "00" * 32, "gas": 50000, "nonce": 0, "chainId": 97}

    w.sign_transaction(call)  # balanceOf(address): not on the list
    assert len(inner.calls) == 1
    assert [p for p, _ in recorder] == ["/request"]  # nothing armed

    with pytest.raises(BoundaryRefused) as exc:
        w.sign_transaction(call)
    assert "cooldown" in str(exc.value)
    assert len(inner.calls) == 1


# -- 4 ------------------------------------------------------------------------------------

def test_typed_data_permit_is_irreversible(live, recorder):
    console("/api/policy", CONFIRM_POLICY)
    inner, name = FakeWallet(), agent_name()
    w = BoundaryWalletProvider(inner, agent=name, console=CONSOLE)

    with pytest.raises(BoundaryRefused) as exc:
        w.sign_typed_data(*permit_typed())
    assert "irreversible, and no unspent confirm" in str(exc.value)
    assert inner.calls == []
    assert recorder[0] == ("/tool", {"agent": name, "irreversible": 1})

    console("/api/tool", {"agent": name, "confirm": 1})
    w.sign_typed_data(*permit_typed())
    assert [c[0] for c in inner.calls] == ["sign_typed_data"]


def test_plain_message_is_ticked_without_irreversible(live, recorder):
    console("/api/policy", CONFIRM_POLICY)
    inner = FakeWallet()
    w = BoundaryWalletProvider(inner, agent=agent_name(), console=CONSOLE)
    w.sign_message("0x" + "ab" * 32)
    assert [c[0] for c in inner.calls] == ["sign_message"]
    assert [p for p, _ in recorder] == ["/request"]
    assert recorder[0][1]["class"] == "spend"


# -- 5 ------------------------------------------------------------------------------------

def test_console_unreachable_raises_unless_fail_open(monkeypatch):
    monkeypatch.delenv("REFLEX_FAIL_OPEN", raising=False)
    dead = "http://127.0.0.1:1"
    inner = FakeWallet()
    with pytest.raises(BoundaryUnreachable):
        BoundaryWalletProvider(inner, agent="t", console=dead).sign_transaction(erc20_transfer())
    assert inner.calls == []

    BoundaryWalletProvider(inner, agent="t", console=dead, fail_open=True).sign_transaction(erc20_transfer())
    assert len(inner.calls) == 1

    monkeypatch.setenv("REFLEX_FAIL_OPEN", "1")
    BoundaryWalletProvider(inner, agent="t", console=dead).sign_message("hi")
    assert len(inner.calls) == 2


# -- 6 ------------------------------------------------------------------------------------

def test_adapter_never_sends_confirm_or_blocked(live, recorder):
    console("/api/policy", CONFIRM_POLICY)
    inner, name = FakeWallet(), agent_name()
    w = BoundaryWalletProvider(inner, agent=name, console=CONSOLE)
    for step in range(6):
        if step == 3:
            console("/api/tool", {"agent": name, "confirm": 1})  # the person, not the adapter
        for sign in (lambda: w.sign_transaction(erc20_transfer()),
                     lambda: w.sign_transaction({"to": PAYEE, "value": 1, "nonce": 0, "chainId": 97}),
                     lambda: w.sign_typed_data(*permit_typed()),
                     lambda: w.sign_message("quote")):
            try:
                sign()
            except BoundaryRefused:
                pass
    assert len(recorder) > 20
    for _, body in recorder:
        assert not {"confirm", "confirm_b", "blocked"} & set(body), body
        assert set(body) <= {"agent", "intent", "class", "reason", "irreversible"}, body


# -- x402 and the SDK's own seams ----------------------------------------------------------

def test_x402_signer_signs_through_the_wrapped_provider(live, recorder):
    """bnbagent 0.4.6 has no local payer that consumes an HTTP 402 (the default
    make_x402_payer raises, wallet_provider.py:159), so there is no challenge to fake at
    the HTTP level. The SDK's x402 signing path is X402Signer.sign_payment, which calls
    wallet.sign_typed_data (x402/signer.py:214). This builds the EIP-3009 authorisation
    a 402 `accepts` entry (asset=TOKEN, payTo=PAYEE, amount) would produce and pays it
    through that path, with the boundary as the wallet."""
    inner, name = FakeWallet(), agent_name()
    w = BoundaryWalletProvider(inner, agent=name, console=CONSOLE)
    signer = X402Signer(w, max_value_per_call={TOKEN: 10**6})  # passes the SDK's supports() gate
    accepts = {"scheme": "exact", "network": "eip155:97", "asset": TOKEN, "payTo": PAYEE, "amount": "1000"}
    domain = {"name": "USD Coin", "version": "2", "chainId": 97, "verifyingContract": accepts["asset"]}
    types = {
        "EIP712Domain": [{"name": "name", "type": "string"}, {"name": "version", "type": "string"},
                         {"name": "chainId", "type": "uint256"}, {"name": "verifyingContract", "type": "address"}],
        "TransferWithAuthorization": [{"name": n, "type": t} for n, t in (
            ("from", "address"), ("to", "address"), ("value", "uint256"),
            ("validAfter", "uint256"), ("validBefore", "uint256"), ("nonce", "bytes32"))],
    }
    message = {"from": WALLET, "to": accepts["payTo"], "value": int(accepts["amount"]),
               "validAfter": 0, "validBefore": 2**32, "nonce": "0x" + "00" * 32}

    console("/api/policy", {"class": "spend", "deny_all": True})
    with pytest.raises(BoundaryRefused):
        signer.sign_payment(domain=domain, types=types, message=message, expected_to=PAYEE)
    assert inner.calls == []
    assert recorder[0] == ("/tool", {"agent": name, "irreversible": 1})
    assert signer.budget.spent(TOKEN) == 0  # the SDK rolled the refused payment's reservation back

    console("/api/policy", CONFIRM_POLICY)
    console("/api/tool", {"agent": name, "confirm": 1})
    signer.sign_payment(domain=domain, types=types, message=message, expected_to=PAYEE)
    assert inner.calls[0][0] == "sign_typed_data" and inner.calls[0][3]["to"] == PAYEE
    assert signer.budget.spent(TOKEN) == 1000


@pytest.mark.skip(reason="finding, not a gap in the test: TWAKProvider.make_x402_payer returns TwakX402Payer "
                         "(wallets/twak_provider.py:689-698), which builds and signs the payment inside the twak "
                         "CLI process; no sign_* call reaches any WalletProvider, so no wrapper can gate it. "
                         "BoundaryWalletProvider refuses make_x402_payer for such an inner instead "
                         "(test_self_broadcasting_inner_is_refused_at_its_seams).")
def test_twak_delegated_x402_payer_goes_through_the_provider():
    pass


def test_self_broadcasting_inner_is_refused_at_its_seams():
    w = BoundaryWalletProvider(SelfBroadcastingFake(), agent="t", console="http://127.0.0.1:1")
    with pytest.raises(UnsupportedWalletOperation):
        w.make_executor(ExecutionContext(web3=None))
    with pytest.raises(UnsupportedWalletOperation):
        w.make_x402_payer()


def test_erc8183_executor_signs_through_the_boundary():
    """ContractClientMixin._execute_intent builds the executor with
    wallet_provider.make_executor (core/contract_mixin.py:182); for a pure signer that is
    a LocalExecutor whose sign calls go to the wallet it was built from."""
    w = BoundaryWalletProvider(FakeWallet(), agent="t", console="http://127.0.0.1:1")
    ex = w.make_executor(ExecutionContext(web3=None))
    assert isinstance(ex, LocalExecutor) and ex.wallet_provider is w


def test_capabilities_are_the_inner_wallets_and_no_bypass_is_forwarded():
    class MessageOnly(WalletProvider):
        @property
        def address(self):
            return WALLET

        def sign_message(self, message):
            return {}

    w = BoundaryWalletProvider(MessageOnly(), agent="t")
    assert w.capabilities() == frozenset({"sign.message"})
    with pytest.raises(UnsupportedWalletOperation):
        X402Signer(w)  # the SDK's own composition gate still sees the truth
    for name in ("_DANGEROUS_sign_typed_data_no_policy", "export_private_key", "export_keystore", "execute"):
        assert not hasattr(w, name)
    with pytest.raises(ValueError):
        BoundaryWalletProvider(w)


def test_selectors_are_classified():
    w = BoundaryWalletProvider(FakeWallet(), agent="t")
    assert bb._selector(bytes.fromhex("a9059cbb") + b"\x00" * 64) == "0xa9059cbb"
    assert bb._selector("0x095EA7B3" + "00" * 64) == "0x095ea7b3"
    assert bb._selector("0x") is None
    assert bb._as_int("0x0de0b6b3a7640000") == 10**18
    assert "0xa22cb465" in w._selectors and "0xf242432a" in w._selectors
