"""A BNBAgent SDK wallet that asks the boundary console before it signs anything.

    from bnbagent.wallets import EVMWalletProvider
    from bnbagent_boundary import BoundaryWalletProvider

    wallet = BoundaryWalletProvider(EVMWalletProvider(...), agent="studio:my-agent")
    # hand `wallet` to ERC8183Client / ERC8183JobOps / X402Signer instead of the inner one

What the SDK looks like (read from bnbagent 0.4.6, site-packages/bnbagent/, not guessed):

* `bnbagent.wallets.wallet_provider.WalletProvider` (wallet_provider.py:20) is a plain,
  synchronous ABC. Its only abstract member is the property `address -> str` (:186).
  The signing methods are concrete defaults that raise `UnsupportedWalletOperation`:
  `sign_transaction(transaction: dict) -> dict` (:195),
  `sign_message(message: str) -> dict` (:221, EIP-191),
  `sign_typed_data(domain: dict, types: dict, message: dict) -> dict` (:244, EIP-712).
  There is no send/broadcast method on the provider; no balance or chain-id method either.
* `capabilities()` (:74) derives `sign.*` from which of those methods a subclass
  overrides (:91-99). A wrapper that overrides all three would claim all three, so this
  one reports the inner wallet's capabilities instead.
* `make_executor(context)` (:125) builds a `LocalExecutor(wallet_provider=self)`, and
  `make_x402_payer()` (:159) raises. `EVMWalletProvider` (evm_wallet_provider.py:39)
  signs with an in-memory eth_account key: `sign_transaction` :267, `sign_message` :278,
  `sign_typed_data` :295 (runs its SigningPolicy, then `_raw_sign_typed_data`); it also
  has `_DANGEROUS_sign_typed_data_no_policy` :310 and `export_private_key` :358, which is
  why nothing here forwards attributes it has not named.

Where the SDK signs, traced:

* ERC-8183 escrow and settlement (create/fund/submit/complete via `CommerceClient`,
  `RouterClient`, `PolicyClient`) run `ContractClientMixin._execute_intent`
  (core/contract_mixin.py:182) -> `wallet_provider.make_executor(...)` ->
  `LocalExecutor._send_self_pay` / `_try_sponsored`, which call
  `self.wallet_provider.sign_transaction(transaction)` (wallets/local_executor.py:395,
  :321) and then broadcast. The older `_send_tx` path does the same
  (contract_mixin.py:269). ERC-8004 registration goes through the same executor
  (erc8004/contract.py:82,139). So both go through the provider, for any wallet that
  uses the default executor. `fund` is preceded by an ERC-20 `approve` unless the
  wallet sets `fund_bundles_approval` (erc8183/client.py:361).
* ERC-8183 negotiation signs its quote hash with `wallet_provider.sign_message`
  (erc8183/negotiation.py:759): through the provider.
* x402: the SDK ships no HTTP 402 client for a local wallet. The signing path it does
  ship is `X402Signer.sign_payment`, which calls `self._wallet.sign_typed_data(domain,
  types, message)` (x402/signer.py:214) on an EIP-3009 `TransferWithAuthorization`
  (primary types in signing/policy.py:38-43): through the provider. The delegated path,
  `TWAKProvider.make_x402_payer` -> `TwakX402Payer` (wallets/twak_provider.py:689-698),
  builds and signs the payment inside the external twak process: NOT through the
  provider. Likewise `TWAKProvider.make_executor` returns the provider itself and
  `execute(intent)` signs inside twak (:702-722).

What this guarantees. Every `sign_transaction`, `sign_message` and `sign_typed_data`
call on this object is one tick of the agent's `spend` circuit (or the class you
configure) on the console, and the inner wallet is called only if that tick is granted.
Before a tick whose signature moves value — a transaction with `value > 0`, a contract
creation, a call whose 4-byte selector is on the list, or typed data that is a permit or
a transfer authorization — it arms the tool-layer bit `irreversible`, so a
`confirm_per_irreversible` rule refuses it unless a person has left a confirm. A refusal
raises `BoundaryRefused` (a `PermissionError`) with the rule's words and the inner
wallet is never touched. If the console cannot be reached it raises, unless `fail_open`
or `REFLEX_FAIL_OPEN=1` says to sign unchecked; if the console arms the bit and then
rejects the request itself (HTTP 4xx), the bit stays armed for the agent's next tick. It never writes `confirm`, `confirm_b`
or `blocked`: those belong to a person or to the layer above the agent. A self-
broadcasting inner wallet (one that overrides `make_executor` or `make_x402_payer`,
like twak) is refused at those two calls rather than handed its ungated executor.

What it does not guarantee. It gates signatures that go through this object, and
nothing else. An agent that holds another key, holds a reference to the inner wallet
(in-process Python can always reach `_inner`), runs its own twak CLI, or uses an SDK or
third-party path that signs outside the provider is not gated. Which calls count as
irreversible is this layer's promise — the selector and typed-data lists below — not
the circuit's: a value-moving call through an unlisted selector (a router `swap`, an
8183 `fund` whose `approve` already went through) is ticked without `irreversible`, and
only the class's other rules apply to it. On-chain enforcement, where the circuit's
verdict is checked by the account that holds the funds, is `ReflexModule` — the Safe
module on the `boundary` branch of ~/work/c3s-reflex, docs/ONCHAIN-SELF-DEPLOY.md.

Environment:
    REFLEX_CONSOLE    console URL when `console` is not given (default http://127.0.0.1:8765)
    REFLEX_AGENT      agent name when `agent` is not given
    REFLEX_FAIL_OPEN  set to 1 to sign unchecked when the console is unreachable
    REFLEX_AGENT_TOKEN this wallet's own token (I-3), sent as `X-Reflex-Agent-Token`. The
                      console binds the agent name to it on the first request and then
                      refuses that name to anything that cannot send it — so another
                      program on the machine cannot spend the confirm a person left for
                      this wallet. Read from the environment only, never from a file.
                      Unset = unbound, and everything works as before.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from typing import Any, ClassVar

from bnbagent.wallets.capabilities import SIGN_TRANSACTION, X402_PAY
from bnbagent.wallets.errors import UnsupportedWalletOperation
from bnbagent.wallets.wallet_provider import WalletProvider

# Every selector below was produced by `cast sig "<signature>"` (foundry), not recalled.
DEFAULT_IRREVERSIBLE_SELECTORS: frozenset[str] = frozenset({
    "0xa9059cbb",  # transfer(address,uint256)                                    ERC-20
    "0x23b872dd",  # transferFrom(address,address,uint256)                        ERC-20 / ERC-721
    "0x095ea7b3",  # approve(address,uint256)                                     ERC-20 / ERC-721
    "0x39509351",  # increaseAllowance(address,uint256)                           OZ ERC-20
    "0x42842e0e",  # safeTransferFrom(address,address,uint256)                    ERC-721
    "0xb88d4fde",  # safeTransferFrom(address,address,uint256,bytes)              ERC-721
    "0xf242432a",  # safeTransferFrom(address,address,uint256,uint256,bytes)      ERC-1155
    "0x2eb2c2d6",  # safeBatchTransferFrom(address,address,uint256[],uint256[],bytes) ERC-1155
    "0xa22cb465",  # setApprovalForAll(address,bool)                              ERC-721 / ERC-1155
    "0xd505accf",  # permit(address,address,uint256,uint256,uint8,bytes32,bytes32) EIP-2612
    "0x2b67b570",  # permit(address,((address,uint160,uint48,uint48),address,uint256),bytes)   Permit2 single
    "0x2a2d80d1",  # permit(address,((address,uint160,uint48,uint48)[],address,uint256),bytes) Permit2 batch
    "0xe3ee160e",  # transferWithAuthorization(address,address,uint256,uint256,uint256,bytes32,uint8,bytes32,bytes32) EIP-3009
})

# EIP-712 struct names that authorise moving tokens. Names as the SDK itself lists them
# (bnbagent/signing/policy.py:38-76): EIP-3009 (x402's payment authorisation), EIP-2612,
# and both Permit2 families.
DEFAULT_IRREVERSIBLE_TYPED: frozenset[str] = frozenset({
    "TransferWithAuthorization", "ReceiveWithAuthorization",
    "Permit", "PermitSingle", "PermitBatch",
    "PermitTransferFrom", "PermitBatchTransferFrom", "PermitWitnessTransferFrom",
})

DEFAULT_CONSOLE = "http://127.0.0.1:8765"
TIMEOUT = 15


class BoundaryRefused(PermissionError):
    """The console's circuit did not grant this signature; the inner wallet was not called."""

    def __init__(self, reason: str, verdict: dict | None = None):
        super().__init__(reason)
        self.reason = reason
        self.verdict = verdict or {}


class BoundaryUnreachable(ConnectionError):
    """The console could not be asked, and failing open was not chosen."""


def _post(url: str, payload: dict, timeout: float = TIMEOUT) -> dict:
    """The only network call in this file. Tests replace it to record every body.

    The wallet's own token (I-3) rides on every call when REFLEX_AGENT_TOKEN is set — from
    the environment, never from a file, and read here rather than kept on the object so
    that rotating it does not mean rebuilding the wallet. The operator's token is not here
    and must not be: this process is the agent's side of the boundary."""
    headers = {"content-type": "application/json"}
    token = os.environ.get("REFLEX_AGENT_TOKEN")
    if token:
        headers["X-Reflex-Agent-Token"] = token
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _as_int(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return int(value, 16) if value.lower().startswith("0x") else int(value or "0")
    return int(value)


def _selector(data: Any) -> str | None:
    if data is None:
        return None
    if isinstance(data, (bytes, bytearray)):
        return "0x" + bytes(data[:4]).hex() if len(data) >= 4 else None
    text = str(data).lower()
    text = text[2:] if text.startswith("0x") else text
    return "0x" + text[:8] if len(text) >= 8 else None


def _typed_names(types: dict) -> list[str]:
    return [k for k in (types or {}) if k != "EIP712Domain"]


class BoundaryWalletProvider(WalletProvider):
    """Wraps any `WalletProvider`; each signature is one tick of a console circuit."""

    kind: ClassVar[str] = "boundary"

    def __init__(
        self,
        inner: WalletProvider,
        agent: str | None = None,
        console: str | None = None,
        cls: str = "spend",
        fail_open: bool = False,
        irreversible_selectors: frozenset[str] | set[str] | None = None,
        irreversible_typed: frozenset[str] | set[str] | None = None,
    ):
        if isinstance(inner, BoundaryWalletProvider):
            raise ValueError("already wrapped: one boundary per wallet, or every signature is two ticks")
        self._inner = inner
        self._agent = agent or os.environ.get("REFLEX_AGENT") or f"bnbagent:{inner.address[:10]}"
        self._console = (console or os.environ.get("REFLEX_CONSOLE") or DEFAULT_CONSOLE).rstrip("/")
        self._cls = cls
        self._fail_open = fail_open
        self._selectors = frozenset(s.lower() for s in (
            DEFAULT_IRREVERSIBLE_SELECTORS if irreversible_selectors is None else irreversible_selectors))
        self._typed = frozenset(DEFAULT_IRREVERSIBLE_TYPED if irreversible_typed is None else irreversible_typed)
        # `irreversible` is held per agent and consumed by the next tick; two signatures
        # racing through one wrapper must not spend each other's bit.
        self._lock = threading.Lock()

    # -- read-only: named, not forwarded wholesale -------------------------------------
    # A generic __getattr__ would hand out _DANGEROUS_sign_typed_data_no_policy,
    # export_private_key and friends: a signature with no tick.

    @property
    def address(self) -> str:
        return self._inner.address

    @property
    def agent(self) -> str:
        return self._agent

    @property
    def key_location(self) -> str | None:
        return self._inner.key_location

    def exists(self) -> bool:
        return self._inner.exists()

    def capabilities(self) -> frozenset[str]:
        return self._inner.capabilities()

    @property
    def fund_bundles_approval(self) -> bool:  # type: ignore[override]
        return bool(getattr(self._inner, "fund_bundles_approval", False))

    @property
    def signing_policy(self):
        return self._inner.signing_policy  # AttributeError if the inner has none, as before

    @property
    def expected_chain_id(self):
        return self._inner.expected_chain_id

    def describe(self) -> dict[str, Any]:
        d = self._inner.describe()
        d["boundary"] = {"console": self._console, "agent": self._agent, "class": self._cls}
        return d

    # -- execution seams ----------------------------------------------------------------

    def make_executor(self, context):
        if type(self._inner).make_executor is not WalletProvider.make_executor:
            raise UnsupportedWalletOperation(
                SIGN_TRANSACTION,
                reason=(f"the inner {self._inner.kind!r} wallet broadcasts through its own executor, "
                        "which would sign outside the boundary"),
                alternative="wrap a pure signer (EVM, Turnkey, ...) whose transactions go through sign_transaction",
            )
        return super().make_executor(context)  # LocalExecutor(wallet_provider=self): gated

    def make_x402_payer(self, **payer_kwargs: Any):
        if type(self._inner).make_x402_payer is not WalletProvider.make_x402_payer:
            raise UnsupportedWalletOperation(
                X402_PAY,
                reason=(f"the inner {self._inner.kind!r} wallet's x402 payer signs outside this provider, "
                        "so the boundary would not see the payment"),
                alternative="use bnbagent.x402.X402Signer(<this wallet>), which signs through sign_typed_data",
            )
        return super().make_x402_payer(**payer_kwargs)

    # -- signing: one tick each ------------------------------------------------------------

    def sign_transaction(self, transaction: dict[str, Any]) -> dict[str, Any]:
        to = transaction.get("to")
        value = _as_int(transaction.get("value"))
        selector = _selector(transaction.get("data", transaction.get("input")))
        irreversible = value > 0 or not to or (selector is not None and selector in self._selectors)
        reason = f"sign_transaction: to={to or '(create)'} value={value} selector={selector or '-'}"
        self._gate(reason, irreversible)
        return self._inner.sign_transaction(transaction)

    def sign_message(self, message: str) -> dict[str, Any]:
        text = message if isinstance(message, str) else repr(message)
        self._gate(f"sign_message: {text[:80]}", False)
        return self._inner.sign_message(message)

    def sign_typed_data(
        self,
        domain: dict[str, Any],
        types: dict[str, list[dict[str, str]]],
        message: dict[str, Any],
    ) -> dict[str, Any]:
        names = _typed_names(types)
        primary = names[0] if len(names) == 1 else None
        # More than one struct: the SDK's own policy would refuse (signing/checks.py:21),
        # but a wallet without that policy would sign. Fail closed: armed if any is listed.
        irreversible = any(n in self._typed for n in names) if primary is None else primary in self._typed
        reason = (f"sign_typed_data: {primary or '+'.join(names)} to={message.get('to') or message.get('spender')}"
                  f" value={message.get('value')} contract={domain.get('verifyingContract')}")
        self._gate(reason, irreversible)
        return self._inner.sign_typed_data(domain, types, message)

    # -- the console ------------------------------------------------------------------------

    def _gate(self, reason: str, irreversible: bool) -> None:
        fail_open = self._fail_open or os.environ.get("REFLEX_FAIL_OPEN") == "1"
        tag = " [irreversible]" if irreversible else ""
        body = {"agent": self._agent, "intent": 1, "class": self._cls, "reason": (reason + tag)[:160]}
        try:
            with self._lock:
                if irreversible:
                    _post(f"{self._console}/api/tool", {"agent": self._agent, "irreversible": 1})
                verdict = _post(f"{self._console}/api/request", body)
        except urllib.error.HTTPError as e:  # reached it, and it said no to the request itself
            try:
                detail = json.loads(e.read()).get("error", "")
            except Exception:
                detail = ""
            raise BoundaryRefused(f"refused by the boundary: the console rejected the request "
                                  f"(HTTP {e.code}): {detail or e.reason}") from e
        except (urllib.error.URLError, OSError, ValueError) as e:
            if fail_open:
                return
            raise BoundaryUnreachable(
                f"boundary console unreachable at {self._console} ({e}); refusing to sign rather than "
                "signing unchecked. Start it, or pass fail_open=True / REFLEX_FAIL_OPEN=1."
            ) from e
        if verdict.get("granted") is True:
            return
        why = "; ".join(verdict.get("why") or []) or "the circuit did not grant"
        raise BoundaryRefused(f"refused by the boundary at tick {verdict.get('tick')}: {why}", verdict)
