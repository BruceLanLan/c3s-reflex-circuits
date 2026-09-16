#!/usr/bin/env bash
# The whole on-chain half, end to end, on a local anvil fork of BNB Smart Chain.
#
#     scripts/onchain_local_fork.sh
#
# LOCAL FORK ONLY -- NOTHING IS BROADCAST TO ANY NETWORK. (本地分叉、未广播)
# anvil forks a public BSC endpoint into its own memory and mines locally; every
# transaction below goes to http://127.0.0.1:$PORT and is gone when anvil exits. No
# testnet, no mainnet, no wallet, no private key, no mnemonic: the senders are anvil's
# unlocked development accounts and `--unlocked` asks anvil to sign, so no key is read,
# stored or typed anywhere in this repository.
#
# What it proves is in docs/ONCHAIN-LOCAL-FORK.md. In order, this script:
#
#   1. starts anvil, forked from a public BSC endpoint at a pinned block, on $PORT;
#   2. exports the compiled policy from c3s/policy.py, verified before it leaves Python;
#   3. runs contracts/script/LocalForkProof.s.sol with --broadcast against that anvil: a
#      real Safe v1.4.1 from the real factory, the evaluator, the module, the module
#      enabled by its owner, and the decisive cases as transactions;
#   4. re-reads the resulting chain state with `cast`, independently of the script's own
#      assertions, including the one refusal that reverts with the module's own reason;
#   5. runs the 14 fork tests (contracts/test/ReflexModuleFork.t.sol) against the fork;
#   6. replays every Decision the module emitted through this repository's Python
#      evaluator and its independent reference (scripts/fork_agreement.py);
#   7. kills anvil.
#
# Environment (all optional):
#   PORT=8547          anvil's port; pick another if something already listens there
#   FORK_BLOCK=…       the BSC block to fork; 0 means "latest"
#   FORK_RPC=…         a specific upstream endpoint instead of trying the list below
#   ANVIL_LOG=…        where anvil's output goes (default $TMPDIR/c3s-reflex-anvil.log)
set -euo pipefail

export PATH="$HOME/.foundry/bin:$PATH"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PORT:-8547}"
FORK_BLOCK="${FORK_BLOCK:-122240000}"
ANVIL_LOG="${ANVIL_LOG:-${TMPDIR:-/tmp}/c3s-reflex-anvil.log}"
RPC="http://127.0.0.1:$PORT"
# Stdlib only: c3s/policy.py and c3s/netlist.py import nothing outside it.
if [ -n "${PYTHON:-}" ]; then :; elif [ -x "$ROOT/.venv/bin/python" ]; then PYTHON="$ROOT/.venv/bin/python"; else PYTHON="$(command -v python3)"; fi

# Public BSC endpoints, in the order they are tried. The first serves historical state,
# which is what lets the fork be pinned to a block and so be reproducible; the others are
# pruned, and with them FORK_BLOCK must be 0 (latest). None of them needs a key.
ENDPOINTS=(
  "https://bsc-mainnet.public.blastapi.io"
  "https://bsc-dataseed.bnbchain.org"
  "https://bsc-dataseed1.defibit.io"
  "https://bsc-rpc.publicnode.com"
)

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
die() { printf '\n!! %s\n' "$*" >&2; exit 1; }

command -v anvil >/dev/null || die "anvil not found; install Foundry (https://getfoundry.sh)"
command -v forge >/dev/null || die "forge not found; install Foundry"
command -v cast  >/dev/null || die "cast not found; install Foundry"
[ -d "$ROOT/contracts/lib/forge-std" ] \
  || die "contracts/lib/forge-std missing; run: cd contracts && forge install foundry-rs/forge-std --no-git"
lsof -i ":$PORT" >/dev/null 2>&1 && die "something already listens on port $PORT; set PORT=…"

say "0. a public BSC endpoint that answers"
UPSTREAM=""
for candidate in "${FORK_RPC:-}" "${ENDPOINTS[@]}"; do
  [ -z "$candidate" ] && continue
  height=$(curl -s --max-time 20 -X POST -H 'content-type: application/json' \
    --data '{"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}' "$candidate" \
    | "$PYTHON" -c 'import sys,json;d=json.load(sys.stdin);print(int(d["result"],16))' 2>/dev/null || true)
  if [ -n "$height" ]; then
    echo "   $candidate is at block $height"
    UPSTREAM="$candidate"
    break
  fi
  echo "   $candidate did not answer, trying the next"
done
[ -n "$UPSTREAM" ] || die "no public BSC endpoint answered; none of them needs a key, so this is a network problem here, not a missing credential"

say "1. anvil, forked, local"
# Pinning the block is what makes a run reproducible, but only an archive endpoint can
# serve state that old: the pruned ones keep about two minutes of it and would answer
# "missing trie node" at the first read, long after anvil looked healthy. Ask now.
PIN=""
if [ "$FORK_BLOCK" != "0" ]; then
  served=$(curl -s --max-time 20 -X POST -H 'content-type: application/json' \
    --data "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"eth_getCode\",\"params\":[\"0x41675C099F32341bf84BFc5382aF534df5C7461a\",\"$(printf '0x%x' "$FORK_BLOCK")\"]}" \
    "$UPSTREAM" | grep -c '"result":"0x6' || true)
  if [ "$served" = "1" ]; then
    PIN="--fork-block-number $FORK_BLOCK"
    echo "   $UPSTREAM serves block $FORK_BLOCK; pinning there"
  else
    echo "   $UPSTREAM does not serve state at block $FORK_BLOCK (not an archive); forking latest instead"
  fi
fi
# A background service writes to a log file. Never pipe anvil into anything.
# env -u …_PROXY: a local HTTP proxy in the environment makes long upstream reads hang.
nohup env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u http_proxy -u https_proxy -u all_proxy \
  anvil --fork-url "$UPSTREAM" $PIN --port "$PORT" --silent \
  > "$ANVIL_LOG" 2>&1 &
ANVIL_PID=$!
cleanup() { kill "$ANVIL_PID" 2>/dev/null || true; }
trap cleanup EXIT

for _ in $(seq 1 60); do
  cast block-number --rpc-url "$RPC" >/dev/null 2>&1 && break
  kill -0 "$ANVIL_PID" 2>/dev/null || die "anvil exited; see $ANVIL_LOG"
  sleep 1
done
cast block-number --rpc-url "$RPC" >/dev/null 2>&1 || die "anvil did not come up in 60s; see $ANVIL_LOG"
CHAIN_ID=$(cast chain-id --rpc-url "$RPC")
BLOCK=$(cast block-number --rpc-url "$RPC")
[ "$CHAIN_ID" = "56" ] || die "the fork says chain $CHAIN_ID, expected 56 (BNB Smart Chain)"
echo "   pid $ANVIL_PID, chain $CHAIN_ID, forked at block $BLOCK, log $ANVIL_LOG"
[ "$(cast code 0x41675C099F32341bf84BFc5382aF534df5C7461a --rpc-url "$RPC")" = "0x" ] \
  && die "the forked state has no Safe v1.4.1 singleton" || true
echo "   Safe v1.4.1 singleton present: $(cast call 0x41675C099F32341bf84BFc5382aF534df5C7461a 'VERSION()(string)' --rpc-url "$RPC")"

say "2. the policy, compiled and verified here"
POLICY_ENV=$(C3S_ROOT="$ROOT" "$PYTHON" - <<'PY'
import hashlib, os, sys
sys.path.insert(0, os.environ["C3S_ROOT"])
from c3s.policy import Policy
from c3s.netlist import to_bytes
p = Policy(min_gap_ticks=4, confirm_per_irreversible=True, forbid_when_blocked=True)
c = p.build()
v, pr = p.verify(c), p.properties(c)
assert v["outputs_match"], "the compiled circuit does not match the reference"
assert pr["holds"], "the circuit's own properties do not hold"
b = to_bytes(c)
print("export NETLIST=0x" + b.hex())
print("export NETLIST_SHA256=0x" + hashlib.sha256(b).hexdigest())
print("export N_IN=%d" % len(p.input_names()))
print("export N_STATE=%d" % p.state_bits)
print("export OPTIONAL_INPUTS=1")  # irreversible
print("# " + "; ".join(p.describe()), file=sys.stderr)
PY
)
eval "$POLICY_ENV"
echo "   $N_IN inputs, $N_STATE latches, netlist sha256 ${NETLIST_SHA256:0:18}…"

say "3. deploy and act, as transactions on the fork"
A0=0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266
PROOF_OUT="$(mktemp)"
( cd "$ROOT/contracts" && forge script script/LocalForkProof.s.sol \
    --rpc-url "$RPC" --broadcast --unlocked --sender "$A0" ) | tee "$PROOF_OUT"
grep -q "ONCHAIN EXECUTION COMPLETE & SUCCESSFUL" "$PROOF_OUT" || die "the fork proof script did not complete"
eval "$(grep -oE '^  FORK_[A-Z]+=0x[0-9a-fA-F]{40}$' "$PROOF_OUT" | tr -d ' ')"
rm -f "$PROOF_OUT"
echo "   Safe $FORK_SAFE   module $FORK_MODULE"

say "4. the same claims, read back off the chain with cast"
check() { # name expected actual
  if [ "$2" = "$3" ]; then printf '   ok   %-44s %s\n' "$1" "$3"
  else printf '   FAIL %-44s got %s, expected %s\n' "$1" "$3" "$2"; exit 1; fi
}
check "the Safe is a real Safe" '"1.4.1"' "$(cast call "$FORK_SAFE" 'VERSION()(string)' --rpc-url "$RPC")"
check "the module is enabled on it" true "$(cast call "$FORK_SAFE" "isModuleEnabled(address)(bool)" "$FORK_MODULE" --rpc-url "$RPC")"
check "the agent is not an owner" false "$(cast call "$FORK_SAFE" "isOwner(address)(bool)" "$FORK_AGENT" --rpc-url "$RPC")"
check "the Safe paid exactly once" 9000000000000000000 "$(cast balance "$FORK_SAFE" --rpc-url "$RPC")"
check "the recipient was paid once" 1000000000000000000 "$(cast balance "$FORK_RECIPIENT" --rpc-url "$RPC")"
check "the confirmation is spent" false "$(cast call "$FORK_MODULE" "confirmations(bytes32)(bool)" \
  "$(cast call "$FORK_MODULE" "confirmationKey(address,address,uint256,bytes)(bytes32)" \
     "$FORK_AGENT" "$FORK_RECIPIENT" 1000000000000000000 0x --rpc-url "$RPC")" --rpc-url "$RPC")"
check "the netlist on chain is the one exported" "$NETLIST_SHA256" "$(cast call "$FORK_MODULE" 'netlistSha256()(bytes32)' --rpc-url "$RPC")"

# The one refusal that is a revert, with the module's own reason, executed on the fork.
set +e
FORBIDDEN=$(cast call "$FORK_MODULE" "act(address,uint256,bytes)(bool,bytes)" "$FORK_SAFE" 0 \
  "$(cast calldata 'addOwnerWithThreshold(address,uint256)' "$FORK_AGENT" 1)" \
  --from "$FORK_AGENT" --rpc-url "$RPC" 2>&1)
set -e
case "$FORBIDDEN" in
  *"ForbiddenTarget(${FORK_SAFE}"*) printf '   ok   %-44s %s\n' "act(safe, addOwner) reverts" "ForbiddenTarget($FORK_SAFE)" ;;
  *) die "act() on the Safe itself did not revert with ForbiddenTarget: $FORBIDDEN" ;;
esac

check "the agent is named" true "$(cast call "$FORK_MODULE" "agents(address)(bool)" "$FORK_AGENT" --rpc-url "$RPC")"
check "the stranger is not named" false "$(cast call "$FORK_MODULE" "agents(address)(bool)" "$FORK_STRANGER" --rpc-url "$RPC")"

# The other refusal that is a revert with the module's own reason: an address nobody named.
set +e
UNNAMED=$(cast call "$FORK_MODULE" "act(address,uint256,bytes)(bool,bytes)" "$FORK_RECIPIENT" 0 0x \
  --from "$FORK_STRANGER" --rpc-url "$RPC" 2>&1)
set -e
case "$UNNAMED" in
  *"NotAgent(${FORK_STRANGER}"*) printf '   ok   %-44s %s\n' "act() from an unnamed address reverts" "NotAgent($FORK_STRANGER)" ;;
  *) die "an unnamed caller was not refused with NotAgent: $UNNAMED" ;;
esac

say "5. the fork tests: the ways around it"
( cd "$ROOT/contracts" && FORK_URL="$RPC" forge test --match-path 'test/ReflexModuleFork.t.sol' -vv \
    | grep -E '^(\[PASS|\[FAIL|Suite result|Ran )' )

say "6. the fork's verdicts against this repository's"
"$PYTHON" "$ROOT/scripts/fork_agreement.py" --rpc "$RPC" --module "$FORK_MODULE"

say "done"
cat <<'EOF'
   Everything above ran on a local anvil fork of BNB Smart Chain.
   NOTHING WAS BROADCAST TO ANY NETWORK. (本地分叉、未广播)
   anvil is being killed now; the Safe, the module and every transaction go with it.
EOF
