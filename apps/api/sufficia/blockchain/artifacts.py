"""Compiled contract artifacts and revert decoding.

Artifacts are produced by ``contracts/scripts/build.mjs`` and copied to ``sufficia/blockchain/artifacts``
(``{contractName, sourceName, abi, bytecode, deployedBytecode, compiler}``).

Revert decoding
---------------
Every custom error of every loaded ABI (EvidenceGate incl. inherited OpenZeppelin errors, DemoSwapRouter,
DemoERC20, and the test mocks when present) is indexed by its 4-byte selector, plus the Solidity builtins
``Error(string)`` and ``Panic(uint256)``.

``decode_revert(data)`` returns ``(error_name, args)``:
  * ``(None, {})`` for empty revert data (e.g. ``revert()`` / out of gas);
  * ``("ActionHashMismatch", {})``, ``("SlippageExceeded", {"received": 1, "minAmountOut": 2})`` ...;
  * ``ExecutionFailed(bytes)`` is unwrapped recursively: a router ``InsufficientOutput`` inside it becomes
    ``("ExecutionFailed(InsufficientOutput)", {"returndata": "0x...", "inner": {...}})``;
  * unknown selectors give ``("UnknownError", {"selector": "0x...", "data": "0x..."})``.

``extract_revert_data(exc)`` recovers the raw revert bytes from whatever web3 8 / eth-tester raised:
  * web3 ``ContractCustomError`` / ``ContractLogicError`` / ``ContractPanicError``: ``.data`` as a 0x-hex
    string or a ``{"data": "0x..."}`` dict;
  * ``Web3RPCError``: ``rpc_response["error"]["data"]``;
  * eth-tester ``TransactionFailed("execution reverted: b'...'")``: the Python repr of the raw bytes;
    an EIP-838 reason string that eth-tester already decoded is re-encoded as ``Error(string)``.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Optional

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_utils import is_address, keccak, to_checksum_address

ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"

#: Contracts the backend deploys / talks to.
CORE_CONTRACTS: tuple[str, ...] = ("EvidenceGate", "DemoSwapRouter", "DemoERC20")
#: Test mocks: indexed for revert decoding when their artifacts are present (never required).
OPTIONAL_CONTRACTS: tuple[str, ...] = ("RevertingTarget", "ReentrantTarget")

ERROR_STRING_SELECTOR = keccak(text="Error(string)")[:4]
PANIC_SELECTOR = keccak(text="Panic(uint256)")[:4]
_MAX_NESTING = 4
_HEX_RE = re.compile(r"^0x(?:[0-9a-fA-F]{2})*$")


class ArtifactNotFound(FileNotFoundError):
    pass


# ---------------------------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------------------------


@lru_cache(maxsize=None)
def load_artifact(name: str) -> dict[str, Any]:
    """Loads (once per process) the compiled artifact ``<name>.json``. Treat the result as read-only."""
    path = ARTIFACTS_DIR / f"{name}.json"
    if not path.is_file():
        raise ArtifactNotFound(
            f"missing contract artifact {path}; build it with: cd contracts && npm install && npm run build"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    for key in ("abi", "bytecode"):
        if key not in data:
            raise ValueError(f"artifact {path} has no '{key}'")
    return data


def contract_abi(name: str) -> list[dict[str, Any]]:
    return load_artifact(name)["abi"]


def contract_bytecode(name: str) -> str:
    code = load_artifact(name)["bytecode"]
    return code if code.startswith("0x") else "0x" + code


def abi_type(param: dict[str, Any]) -> str:
    """Canonical ABI type of an input/output entry (tuples expanded)."""
    t = param["type"]
    if t.startswith("tuple"):
        return "(" + ",".join(abi_type(c) for c in param["components"]) + ")" + t[len("tuple") :]
    return t


def encode_constructor(name: str, args: Iterable[Any]) -> str:
    """Deployment data: creation bytecode followed by the ABI-encoded constructor arguments."""
    abi = contract_abi(name)
    ctor = next((item for item in abi if item.get("type") == "constructor"), None)
    args = list(args)
    types = [abi_type(i) for i in ctor["inputs"]] if ctor else []
    if len(types) != len(args):
        raise ValueError(f"{name} constructor expects {len(types)} arguments, got {len(args)}")
    encoded = abi_encode(types, args) if types else b""
    return contract_bytecode(name) + encoded.hex()


# ---------------------------------------------------------------------------------------------
# Error table
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ErrorSpec:
    name: str
    signature: str
    types: tuple[str, ...]
    arg_names: tuple[str, ...]
    selector: bytes
    sources: tuple[str, ...]


def _spec_from_abi(item: dict[str, Any], source: str) -> ErrorSpec:
    types = tuple(abi_type(i) for i in item.get("inputs", []))
    names = tuple(i.get("name") or f"arg{idx}" for idx, i in enumerate(item.get("inputs", [])))
    signature = f"{item['name']}({','.join(types)})"
    return ErrorSpec(item["name"], signature, types, names, keccak(text=signature)[:4], (source,))


@lru_cache(maxsize=1)
def error_table() -> dict[bytes, ErrorSpec]:
    """selector -> ErrorSpec for every error entry of every available ABI, plus Error/Panic builtins."""
    table: dict[bytes, ErrorSpec] = {
        ERROR_STRING_SELECTOR: ErrorSpec("Error", "Error(string)", ("string",), ("message",), ERROR_STRING_SELECTOR, ("solidity",)),
        PANIC_SELECTOR: ErrorSpec("Panic", "Panic(uint256)", ("uint256",), ("code",), PANIC_SELECTOR, ("solidity",)),
    }
    names = list(CORE_CONTRACTS) + [n for n in OPTIONAL_CONTRACTS if (ARTIFACTS_DIR / f"{n}.json").is_file()]
    for contract in names:
        for item in contract_abi(contract):
            if item.get("type") != "error":
                continue
            spec = _spec_from_abi(item, contract)
            existing = table.get(spec.selector)
            if existing is None:
                table[spec.selector] = spec
            elif existing.signature == spec.signature and contract not in existing.sources:
                # Same error declared by several contracts (e.g. SafeERC20FailedOperation): one entry.
                table[spec.selector] = ErrorSpec(
                    existing.name, existing.signature, existing.types, existing.arg_names, existing.selector,
                    existing.sources + (contract,),
                )
            # A genuine 4-byte collision with a different signature keeps the first entry.
    return table


def error_selector(signature: str) -> bytes:
    return keccak(text=signature)[:4]


# ---------------------------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------------------------


def _to_bytes(data: Any) -> bytes:
    if data is None:
        return b""
    if isinstance(data, (bytes, bytearray, memoryview)):
        return bytes(data)
    if isinstance(data, str):
        text = data.strip()
        if text.startswith(("0x", "0X")):
            text = text[2:]
        return bytes.fromhex(text)
    raise TypeError(f"cannot interpret {type(data).__name__} as revert data")


def jsonable(value: Any) -> Any:
    """ABI value -> JSON-friendly value (bytes as 0x-hex, addresses checksummed, tuples as lists)."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "0x" + bytes(value).hex()
    if isinstance(value, str) and len(value) == 42 and is_address(value):
        return to_checksum_address(value)
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    return value


def decode_revert(data: bytes | bytearray | str | None, *, _depth: int = 0) -> tuple[Optional[str], dict[str, Any]]:
    """Decodes raw revert data into ``(error_name, args)``. See the module docstring for the shapes."""
    raw = _to_bytes(data)
    if not raw:
        return None, {}
    if len(raw) < 4:
        return "UnknownError", {"selector": None, "data": "0x" + raw.hex()}
    spec = error_table().get(raw[:4])
    if spec is None:
        return "UnknownError", {"selector": "0x" + raw[:4].hex(), "data": "0x" + raw.hex()}
    try:
        values = abi_decode(list(spec.types), raw[4:]) if spec.types else ()
    except Exception as exc:  # malformed payload for a known selector
        return spec.name, {"data": "0x" + raw.hex(), "decode_error": f"{type(exc).__name__}: {exc}"}
    args = {n: jsonable(v) for n, v in zip(spec.arg_names, values)}

    if spec.name == "ExecutionFailed" and spec.types == ("bytes",):
        inner_raw = bytes(values[0])
        if _depth >= _MAX_NESTING:
            inner_name, inner_args = ("UnknownError", {"data": "0x" + inner_raw.hex()}) if inner_raw else (None, {})
        else:
            inner_name, inner_args = decode_revert(inner_raw, _depth=_depth + 1)
        name = f"ExecutionFailed({inner_name})" if inner_name else "ExecutionFailed"
        return name, {"returndata": "0x" + inner_raw.hex(), "inner": {"error_name": inner_name, "args": inner_args}}
    return spec.name, args


def describe_revert(name: Optional[str], args: dict[str, Any] | None = None) -> str:
    """Short human description, e.g. ``SlippageExceeded(received=1, minAmountOut=2)``."""
    if not name:
        return "reverted without data"
    args = args or {}
    if name.startswith("ExecutionFailed"):
        inner = args.get("inner") or {}
        inner_name = inner.get("error_name")
        if inner_name:
            return f"ExecutionFailed: target reverted with {describe_revert(inner_name, inner.get('args'))}"
        return "ExecutionFailed: target reverted without data"
    if name == "Error":
        return f"Error({args.get('message')!r})"
    shown = {k: v for k, v in args.items() if k not in ("decode_error",)}
    if not shown:
        return name
    return f"{name}(" + ", ".join(f"{k}={v}" for k, v in shown.items()) + ")"


# ---------------------------------------------------------------------------------------------
# Exceptions -> raw revert data
# ---------------------------------------------------------------------------------------------

_REVERT_PREFIXES = ("execution reverted: ", "execution reverted:", "execution reverted")


def _data_from_value(value: Any) -> Optional[bytes]:
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    if isinstance(value, dict):
        for key in ("data", "result", "output"):
            if key in value:
                found = _data_from_value(value[key])
                if found is not None:
                    return found
        return None
    if isinstance(value, str):
        text = value.strip()
        if _HEX_RE.match(text):
            return bytes.fromhex(text[2:])
    return None


def _data_from_message(message: str, *, eth_tester: bool) -> Optional[bytes]:
    text = message.strip()
    had_prefix = False
    for prefix in _REVERT_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix) :].strip()
            had_prefix = True
            break
    if text.startswith(("b'", 'b"')):
        try:
            value = ast.literal_eval(text)
        except (ValueError, SyntaxError):
            value = None
        if isinstance(value, bytes):
            return value
    if _HEX_RE.match(text) and (had_prefix or len(text) >= 10):
        return bytes.fromhex(text[2:])
    if eth_tester and had_prefix:
        if not text:
            return b""
        # eth-tester already decoded an EIP-838 reason string: re-encode it as Error(string).
        return ERROR_STRING_SELECTOR + abi_encode(["string"], [text])
    return None


def _single(exc: BaseException) -> Optional[bytes]:
    found = _data_from_value(getattr(exc, "data", None))
    if found is not None:
        return found
    rpc = getattr(exc, "rpc_response", None)
    if isinstance(rpc, dict):
        found = _data_from_value((rpc.get("error") or {}).get("data"))
        if found is not None:
            return found
    eth_tester = type(exc).__name__ == "TransactionFailed"
    for arg in getattr(exc, "args", ()):
        if isinstance(arg, BaseException):
            found = _single(arg)
        elif isinstance(arg, (bytes, bytearray, memoryview)):
            found = bytes(arg)
        elif isinstance(arg, dict):
            found = _data_from_value(arg)
            if found is None and isinstance(arg.get("message"), str):
                found = _data_from_message(arg["message"], eth_tester=eth_tester)
        elif isinstance(arg, str):
            found = _data_from_message(arg, eth_tester=eth_tester)
        else:
            found = None
        if found is not None:
            return found
    return None


def extract_revert_data(exc: BaseException | None) -> Optional[bytes]:
    """Raw revert bytes carried by ``exc`` (or its cause chain); ``None`` if it carries none."""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        found = _single(exc)
        if found is not None:
            return found
        exc = exc.__cause__ or exc.__context__
    return None


def is_revert_error(exc: BaseException) -> bool:
    """True when ``exc`` is an EVM revert (as opposed to a transport / validation failure)."""
    from web3.exceptions import ContractLogicError, Web3RPCError

    if isinstance(exc, ContractLogicError) or type(exc).__name__ == "TransactionFailed":
        return True
    if isinstance(exc, Web3RPCError):
        message = (getattr(exc, "message", None) or str(exc)).lower()
        return "revert" in message
    return False


def decode_exception(exc: BaseException) -> tuple[Optional[str], dict[str, Any], Optional[bytes]]:
    """``(error_name, args, raw_data)`` for a revert exception."""
    data = extract_revert_data(exc)
    name, args = decode_revert(data)
    return name, args, data
