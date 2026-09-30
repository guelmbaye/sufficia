// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @notice Test target: `fail()` always reverts with a custom error, `ok()` succeeds and returns a counter.
contract RevertingTarget {
    error TargetAlwaysReverts(uint256 code);

    uint256 public calls;
    uint256 public lastValue;

    event Called(address indexed caller, uint256 value, uint256 calls);

    function fail() external pure {
        revert TargetAlwaysReverts(42);
    }

    function ok() external payable returns (uint256) {
        calls += 1;
        lastValue = msg.value;
        emit Called(msg.sender, msg.value, calls);
        return calls;
    }
}
