"""Artifacts, error table and revert decoding (pure: no chain needed)."""

from __future__ import annotations

from eth_abi import encode as abi_encode
from eth_tester.exceptions import TransactionFailed
from eth_utils import keccak
from web3.exceptions import ContractCustomError, ContractLogicError, Web3RPCError

from sufficia.blockchain.artifacts import (
    ERROR_STRING_SELECTOR,
    abi_type,
    decode_revert,
    describe_revert,
    error_selector,
    error_table,
    extract_revert_data,
    is_revert_error,
    load_artifact,
)


def sel(signature: str) -> bytes:
    return keccak(text=signature)[:4]


def all_abi_errors(*names: str) -> set[str]:
    out = set()
    for name in names:
        for item in load_artifact(name)["abi"]:
            if item["type"] == "error":
                out.add(f"{item['name']}({','.join(abi_type(i) for i in item['inputs'])})")
    return out


def test_load_artifact_is_cached_and_complete():
    first = load_artifact("EvidenceGate")
    assert first is load_artifact("EvidenceGate")
    for name in ("EvidenceGate", "DemoSwapRouter", "DemoERC20"):
        artifact = load_artifact(name)
        assert artifact["abi"] and artifact["bytecode"]


def test_error_table_covers_every_error_of_every_abi():
    table = error_table()
    signatures = {spec.signature for spec in table.values()}
    expected = all_abi_errors("EvidenceGate", "DemoSwapRouter", "DemoERC20")
    assert expected <= signatures
    # Inherited OpenZeppelin errors of the gate are there too.
    for sig in (
        "AccessControlUnauthorizedAccount(address,bytes32)",
        "EnforcedPause()",
        "ExpectedPause()",
        "ReentrancyGuardReentrantCall()",
        "SafeERC20FailedOperation(address)",
        "ERC20InsufficientBalance(address,uint256,uint256)",
        "InsufficientOutput(uint256,uint256)",
        "Error(string)",
        "Panic(uint256)",
    ):
        assert sel(sig) in table, sig
    # A selector shared by several contracts is indexed once, with every source.
    assert set(table[sel("SafeERC20FailedOperation(address)")].sources) >= {"EvidenceGate", "DemoSwapRouter"}


def test_every_gate_error_decodes_to_its_name():
    for sig in all_abi_errors("EvidenceGate"):
        name = sig.split("(")[0]
        types = [t for t in sig[len(name) + 1 : -1].split(",") if t]
        if name == "ExecutionFailed":
            continue
        values = [{"address": "0x" + "11" * 20, "bytes32": b"\x22" * 32, "uint256": 7}[t] for t in types]
        decoded, args = decode_revert(sel(sig) + abi_encode(types, values))
        assert decoded == name, sig
        assert len(args) == len(types)


def test_decode_arguments_are_json_friendly():
    data = sel("SlippageExceeded(uint256,uint256)") + abi_encode(["uint256", "uint256"], [5, 10])
    assert decode_revert(data) == ("SlippageExceeded", {"received": 5, "minAmountOut": 10})
    data = sel("AccessControlUnauthorizedAccount(address,bytes32)") + abi_encode(
        ["address", "bytes32"], ["0x5cbdd86a2fa8dc4bddd8a8f69dba48572eec07fb", keccak(text="AUTHORIZER_ROLE")]
    )
    name, args = decode_revert("0x" + data.hex())
    assert name == "AccessControlUnauthorizedAccount"
    assert args["account"] == "0x5CbDd86a2FA8Dc4bDdd8a8f69dBa48572EeC07FB"  # checksummed
    assert args["neededRole"] == "0x" + keccak(text="AUTHORIZER_ROLE").hex()


def test_nested_execution_failed_decodes_inner_router_error():
    inner = sel("InsufficientOutput(uint256,uint256)") + abi_encode(["uint256", "uint256"], [233, 330])
    outer = sel("ExecutionFailed(bytes)") + abi_encode(["bytes"], [inner])
    name, args = decode_revert(outer)
    assert name == "ExecutionFailed(InsufficientOutput)"
    assert args["inner"] == {"error_name": "InsufficientOutput", "args": {"amountOut": 233, "minAmountOut": 330}}
    assert args["returndata"] == "0x" + inner.hex()
    assert "InsufficientOutput(amountOut=233, minAmountOut=330)" in describe_revert(name, args)


def test_nested_execution_failed_variants():
    wrap = lambda data: sel("ExecutionFailed(bytes)") + abi_encode(["bytes"], [data])  # noqa: E731
    assert decode_revert(wrap(b""))[0] == "ExecutionFailed"
    reason = ERROR_STRING_SELECTOR + abi_encode(["string"], ["nope"])
    name, args = decode_revert(wrap(reason))
    assert name == "ExecutionFailed(Error)" and args["inner"]["args"] == {"message": "nope"}
    # Test mock error (RevertingTarget) is indexed when its artifact is present.
    assert decode_revert(wrap(sel("TargetAlwaysReverts(uint256)") + abi_encode(["uint256"], [42])))[0] == (
        "ExecutionFailed(TargetAlwaysReverts)"
    )
    # Double nesting (a target that is itself a gate).
    assert decode_revert(wrap(wrap(sel("ActionHashMismatch()"))))[0] == "ExecutionFailed(ExecutionFailed(ActionHashMismatch))"
    unknown = wrap(b"\xde\xad\xbe\xef" + b"\x00" * 32)
    name, args = decode_revert(unknown)
    assert name == "ExecutionFailed(UnknownError)" and args["inner"]["args"]["selector"] == "0xdeadbeef"


def test_decode_edge_cases():
    assert decode_revert(b"") == (None, {})
    assert decode_revert(None) == (None, {})
    assert decode_revert(b"\x01\x02")[0] == "UnknownError"
    assert decode_revert(b"\xde\xad\xbe\xef")[1]["selector"] == "0xdeadbeef"
    # Known selector with a malformed payload: the name survives, the problem is reported.
    name, args = decode_revert(sel("SlippageExceeded(uint256,uint256)") + b"\x00" * 3)
    assert name == "SlippageExceeded" and "decode_error" in args
    assert describe_revert(None) == "reverted without data"
    assert error_selector("ActionHashMismatch()") == sel("ActionHashMismatch()")


def test_extract_from_web3_exceptions():
    data = sel("ActionHashMismatch()")
    hex_data = "0x" + data.hex()
    assert extract_revert_data(ContractCustomError(hex_data, data=hex_data)) == data
    assert extract_revert_data(ContractLogicError("execution reverted", data={"data": hex_data})) == data
    rpc = Web3RPCError("execution reverted", rpc_response={"error": {"code": 3, "message": "execution reverted", "data": hex_data}})
    assert extract_revert_data(rpc) == data
    assert is_revert_error(rpc)
    assert not is_revert_error(Web3RPCError("nonce too low", rpc_response={"error": {"message": "nonce too low"}}))


def test_extract_from_eth_tester_messages():
    inner = sel("InsufficientOutput(uint256,uint256)") + abi_encode(["uint256", "uint256"], [1, 2])
    outer = sel("ExecutionFailed(bytes)") + abi_encode(["bytes"], [inner])
    exc = TransactionFailed("execution reverted: " + repr(outer))
    assert extract_revert_data(exc) == outer
    assert decode_revert(extract_revert_data(exc))[0] == "ExecutionFailed(InsufficientOutput)"
    # Bytes whose repr contains quotes / backslashes.
    tricky = sel("SlippageExceeded(uint256,uint256)") + abi_encode(["uint256", "uint256"], [39, 92])
    assert extract_revert_data(TransactionFailed("execution reverted: " + repr(tricky))) == tricky
    # eth-tester already decoded an Error(string) reason: re-encoded so it decodes the same way.
    data = extract_revert_data(TransactionFailed("execution reverted: Ownable: nope"))
    assert decode_revert(data) == ("Error", {"message": "Ownable: nope"})
    # Empty revert.
    assert extract_revert_data(TransactionFailed("execution reverted: b''")) == b""
    # Wrapped in another exception (cause chain).
    try:
        try:
            raise TransactionFailed("execution reverted: " + repr(inner))
        except TransactionFailed as original:
            raise RuntimeError("wrapped") from original
    except RuntimeError as wrapped:
        assert extract_revert_data(wrapped) == inner
    assert extract_revert_data(RuntimeError("connection refused")) is None
