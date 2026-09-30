// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {EvidenceGate} from "../../src/EvidenceGate.sol";

/// @notice Test target that, while being called by the gate, tries to re-enter
///         EvidenceGate.executeAuthorized with a second authorization whose executor is this contract.
///         Without the reentrancy guard that inner call would be valid; with it, it must fail.
contract ReentrantTarget {
    EvidenceGate public immutable gate;
    bytes public lastRevertData;
    bool public reentered;

    constructor(EvidenceGate gate_) {
        gate = gate_;
    }

    /// @notice Re-enters and bubbles the inner revert (the outer execution then fails).
    function attack(bytes32 authorizationId, EvidenceGate.Action calldata action) external returns (bool) {
        gate.executeAuthorized(authorizationId, action);
        reentered = true;
        return true;
    }

    /// @notice Re-enters, swallows and records the inner revert (the outer execution then succeeds).
    function attackAndRecord(bytes32 authorizationId, EvidenceGate.Action calldata action) external returns (bool) {
        try gate.executeAuthorized(authorizationId, action) {
            reentered = true;
        } catch (bytes memory reason) {
            lastRevertData = reason;
        }
        return true;
    }
}
