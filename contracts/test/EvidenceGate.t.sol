// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Test.sol";

import {IAccessControl} from "@openzeppelin/contracts/access/IAccessControl.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

import {EvidenceGate} from "../src/EvidenceGate.sol";
import {DemoERC20} from "../src/DemoERC20.sol";
import {DemoSwapRouter} from "../src/DemoSwapRouter.sol";
import {RevertingTarget} from "./mocks/RevertingTarget.sol";
import {ReentrantTarget} from "./mocks/ReentrantTarget.sol";

/// @notice Foundry tests for EvidenceGate. Written against the forge-std 1.1.2 API subset
///         (vm.prank, vm.warp, vm.expectRevert(bytes4|bytes), vm.expectEmit(bool,bool,bool,bool), vm.assume)
///         so they compile with the pinned npm forge-std. In this repo they are compile-checked with solcjs
///         (`npm run check:foundry`); the executed suite is apps/api/tests/contracts (py-evm).
contract EvidenceGateTest is Test {
    uint256 internal constant USDC = 1e6;
    uint256 internal constant TOKENX = 1e18;
    uint256 internal constant RATE = 2.336e18; // 1 USDC = 2.336 TOKEN-X
    uint256 internal constant AMOUNT_IN = 100 * USDC;
    uint256 internal constant EXPECTED_OUT = 233.6e18;
    uint256 internal constant GATE_FUNDING = 1_000 * USDC;

    address internal admin = address(0xA11CE);
    address internal authorizer = address(0xA17);
    address internal pauser = address(0xBA05E);
    address internal executor = address(0xE8EC);
    address internal recipient = address(0xBEEF);
    address internal attacker = address(0xBAD);

    DemoERC20 internal usdc;
    DemoERC20 internal tokenx;
    DemoSwapRouter internal router;
    EvidenceGate internal gate;

    bytes32 internal constant INTENT_ID = keccak256("intent-1");
    bytes32 internal constant CONTRACT_HASH = keccak256("contract-1");
    bytes32 internal constant EVIDENCE_ROOT = keccak256("evidence-1");

    // Mirrors of EvidenceGate events for vm.expectEmit.
    event ExecutionAuthorized(
        bytes32 indexed authorizationId,
        bytes32 indexed intentId,
        bytes32 indexed evidenceRoot,
        bytes32 contractHash,
        bytes32 actionHash,
        address executor,
        uint64 expiresAt
    );
    event AuthorizationConsumed(bytes32 indexed authorizationId, bytes32 indexed actionHash, address indexed executor);
    event ReceiptCommitted(bytes32 indexed authorizationId, bytes32 indexed receiptHash);

    struct Auth {
        bytes32 id;
        bytes32 actionHash;
        uint64 issuedAt;
        uint64 expiresAt;
        bytes32 nonce;
    }

    function setUp() public {
        vm.warp(1_750_000_000);

        usdc = new DemoERC20("Demo USDC", "dUSDC", 6, address(this));
        tokenx = new DemoERC20("Demo Token X", "TOKENX", 18, address(this));
        router = new DemoSwapRouter(address(this));
        router.setRate(address(usdc), address(tokenx), RATE);
        tokenx.mint(address(router), 1_000_000 * TOKENX);

        address[] memory targets = new address[](1);
        targets[0] = address(router);
        gate = new EvidenceGate(admin, authorizer, pauser, targets);
        usdc.mint(address(gate), GATE_FUNDING);

        vm.prank(authorizer);
        gate.registerIntent(INTENT_ID, CONTRACT_HASH);
    }

    // ------------------------------------------------------------------
    // Helpers
    // ------------------------------------------------------------------

    function _swapData(uint256 amountIn, uint256 minOut, address to) internal view returns (bytes memory) {
        return abi.encodeWithSelector(
            DemoSwapRouter.swapExactIn.selector, address(usdc), address(tokenx), amountIn, minOut, to
        );
    }

    function _swapAction(bytes32 nonce) internal view returns (EvidenceGate.Action memory a) {
        a.target = address(router);
        a.value = 0;
        a.data = _swapData(AMOUNT_IN, EXPECTED_OUT, recipient);
        a.assetIn = address(usdc);
        a.maxAmountIn = AMOUNT_IN;
        a.assetOut = address(tokenx);
        a.minAmountOut = EXPECTED_OUT;
        a.recipient = recipient;
        a.deadline = uint64(block.timestamp + 1 hours);
        a.nonce = nonce;
    }

    function _callAction(address target, bytes memory data, bytes32 nonce)
        internal
        view
        returns (EvidenceGate.Action memory a)
    {
        a.target = target;
        a.data = data;
        a.recipient = recipient;
        a.deadline = uint64(block.timestamp + 1 hours);
        a.nonce = nonce;
    }

    /// @dev Independent re-derivation of the action hash (13 static words, abi.encode).
    function _expectedActionHash(EvidenceGate.Action memory a) internal view returns (bytes32) {
        bytes memory head = abi.encode(
            keccak256("EVIDENCE_GATE_ACTION_V1"), block.chainid, address(gate), a.target, a.value, keccak256(a.data)
        );
        bytes memory tail = abi.encode(
            a.assetIn, a.maxAmountIn, a.assetOut, a.minAmountOut, a.recipient, a.deadline, a.nonce
        );
        // All values are static, so abi.encode(v1..v13) == head ++ tail.
        return keccak256(bytes.concat(head, tail));
    }

    function _buildAuth(EvidenceGate.Action memory a, address exec, uint64 ttl) internal view returns (Auth memory x) {
        x.actionHash = gate.computeActionHash(a);
        x.nonce = a.nonce;
        x.issuedAt = uint64(block.timestamp);
        x.expiresAt = uint64(block.timestamp) + ttl;
        x.id = gate.computeAuthorizationId(CONTRACT_HASH, EVIDENCE_ROOT, x.actionHash, exec, x.nonce);
    }

    function _authorizeCall(Auth memory x, address exec) internal {
        gate.authorize(
            x.id, INTENT_ID, CONTRACT_HASH, EVIDENCE_ROOT, x.actionHash, exec, x.issuedAt, x.expiresAt, x.nonce
        );
    }

    function _authorize(EvidenceGate.Action memory a, address exec, uint64 ttl) internal returns (Auth memory x) {
        x = _buildAuth(a, exec, ttl);
        vm.prank(authorizer);
        _authorizeCall(x, exec);
    }

    function _authorize(EvidenceGate.Action memory a) internal returns (Auth memory) {
        return _authorize(a, executor, 300);
    }

    function _execute(Auth memory x, EvidenceGate.Action memory a) internal returns (bytes memory) {
        vm.prank(executor);
        return gate.executeAuthorized(x.id, a);
    }

    function _allow(address target) internal {
        vm.prank(admin);
        gate.setTargetAllowed(target, true);
    }

    function _consumed(bytes32 id) internal view returns (bool) {
        return gate.getAuthorization(id).consumed;
    }

    // ------------------------------------------------------------------
    // Hashing
    // ------------------------------------------------------------------

    function test_ActionHash_MatchesIndependentEncoding() public {
        EvidenceGate.Action memory a = _swapAction(keccak256("n"));
        assertEq(gate.computeActionHash(a), _expectedActionHash(a));
    }

    function test_AuthorizationId_MatchesIndependentEncoding() public {
        bytes32 ah = keccak256("action");
        bytes32 expected =
            keccak256(abi.encode(keccak256("EVIDENCE_GATE_AUTH_V1"), CONTRACT_HASH, EVIDENCE_ROOT, ah, executor, bytes32("n")));
        assertEq(gate.computeAuthorizationId(CONTRACT_HASH, EVIDENCE_ROOT, ah, executor, bytes32("n")), expected);
    }

    // ------------------------------------------------------------------
    // authorize
    // ------------------------------------------------------------------

    function test_Authorize_HappyPath() public {
        EvidenceGate.Action memory a = _swapAction(keccak256("n1"));
        Auth memory x = _buildAuth(a, executor, 300);

        vm.expectEmit(true, true, true, true);
        emit ExecutionAuthorized(x.id, INTENT_ID, EVIDENCE_ROOT, CONTRACT_HASH, x.actionHash, executor, x.expiresAt);
        vm.prank(authorizer);
        _authorizeCall(x, executor);

        EvidenceGate.Authorization memory s = gate.getAuthorization(x.id);
        assertEq(s.intentId, INTENT_ID);
        assertEq(s.contractHash, CONTRACT_HASH);
        assertEq(s.evidenceRoot, EVIDENCE_ROOT);
        assertEq(s.actionHash, x.actionHash);
        assertEq(s.executor, executor);
        assertEq(uint256(s.expiresAt), uint256(x.expiresAt));
        assertTrue(!s.consumed);
        assertTrue(gate.isAuthorizationActive(x.id));
    }

    function test_Authorize_RevertsForNonAuthorizer() public {
        Auth memory x = _buildAuth(_swapAction(keccak256("n")), executor, 300);
        vm.expectRevert(
            abi.encodeWithSelector(
                IAccessControl.AccessControlUnauthorizedAccount.selector, attacker, gate.AUTHORIZER_ROLE()
            )
        );
        vm.prank(attacker);
        _authorizeCall(x, executor);
    }

    function test_Authorize_RevertsOnDuplicate() public {
        EvidenceGate.Action memory a = _swapAction(keccak256("n"));
        Auth memory x = _authorize(a);
        vm.expectRevert(EvidenceGate.AuthorizationExists.selector);
        vm.prank(authorizer);
        _authorizeCall(x, executor);
    }

    function test_Authorize_RevertsOnIdMismatch() public {
        Auth memory x = _buildAuth(_swapAction(keccak256("n")), executor, 300);
        x.id = keccak256(abi.encode(x.id));
        vm.expectRevert(EvidenceGate.AuthorizationIdMismatch.selector);
        vm.prank(authorizer);
        _authorizeCall(x, executor);
    }

    function test_Authorize_RevertsOnUnregisteredIntent() public {
        Auth memory x = _buildAuth(_swapAction(keccak256("n")), executor, 300);
        vm.expectRevert(EvidenceGate.IntentNotRegistered.selector);
        vm.prank(authorizer);
        gate.authorize(
            x.id, keccak256("unknown"), CONTRACT_HASH, EVIDENCE_ROOT, x.actionHash, executor, x.issuedAt, x.expiresAt, x.nonce
        );
    }

    function test_Authorize_RevertsOnContractHashMismatch() public {
        Auth memory x = _buildAuth(_swapAction(keccak256("n")), executor, 300);
        vm.expectRevert(EvidenceGate.ContractHashMismatch.selector);
        vm.prank(authorizer);
        gate.authorize(
            x.id, INTENT_ID, keccak256("other"), EVIDENCE_ROOT, x.actionHash, executor, x.issuedAt, x.expiresAt, x.nonce
        );
    }

    function test_Authorize_RevertsWhenAlreadyExpired() public {
        Auth memory x = _buildAuth(_swapAction(keccak256("n")), executor, 300);
        x.issuedAt = uint64(block.timestamp - 700);
        x.expiresAt = uint64(block.timestamp - 100);
        vm.expectRevert(EvidenceGate.AuthorizationExpired.selector);
        vm.prank(authorizer);
        _authorizeCall(x, executor);
    }

    function test_Authorize_RevertsWhenTtlTooLong() public {
        Auth memory x = _buildAuth(_swapAction(keccak256("n")), executor, 601);
        vm.expectRevert(EvidenceGate.TtlTooLong.selector);
        vm.prank(authorizer);
        _authorizeCall(x, executor);

        // Exactly MAX_AUTHORIZATION_TTL is accepted.
        x.expiresAt = uint64(block.timestamp + 600);
        vm.prank(authorizer);
        _authorizeCall(x, executor);
        assertTrue(gate.isAuthorizationActive(x.id));
    }

    function test_Authorize_RevertsOnInvalidFields() public {
        Auth memory x = _buildAuth(_swapAction(keccak256("n")), executor, 300);
        x.expiresAt = x.issuedAt; // empty window
        vm.expectRevert(EvidenceGate.InvalidAuthorization.selector);
        vm.prank(authorizer);
        _authorizeCall(x, executor);
    }

    // ------------------------------------------------------------------
    // executeAuthorized
    // ------------------------------------------------------------------

    function test_Execute_ValidSwap() public {
        EvidenceGate.Action memory a = _swapAction(keccak256("n"));
        Auth memory x = _authorize(a);

        vm.expectEmit(true, true, true, true);
        emit AuthorizationConsumed(x.id, x.actionHash, executor);
        bytes memory result = _execute(x, a);

        assertEq(abi.decode(result, (uint256)), EXPECTED_OUT);
        assertEq(usdc.balanceOf(address(gate)), GATE_FUNDING - AMOUNT_IN);
        assertEq(usdc.balanceOf(address(router)), AMOUNT_IN);
        assertEq(tokenx.balanceOf(recipient), EXPECTED_OUT);
        assertEq(usdc.allowance(address(gate), address(router)), 0);
        assertTrue(_consumed(x.id));
        assertTrue(!gate.isAuthorizationActive(x.id));
    }

    function test_Execute_RevertsWithoutAuthorization() public {
        EvidenceGate.Action memory a = _swapAction(keccak256("n"));
        vm.expectRevert(EvidenceGate.AuthorizationNotFound.selector);
        vm.prank(executor);
        gate.executeAuthorized(keccak256("nothing"), a);
    }

    function test_Execute_RevertsWhenExpired() public {
        EvidenceGate.Action memory a = _swapAction(keccak256("n"));
        Auth memory x = _authorize(a, executor, 60);
        vm.warp(uint256(x.expiresAt) + 1);
        vm.expectRevert(EvidenceGate.AuthorizationExpired.selector);
        _execute(x, a);
        assertTrue(!_consumed(x.id));
    }

    function test_Execute_RevertsForWrongExecutor() public {
        EvidenceGate.Action memory a = _swapAction(keccak256("n"));
        Auth memory x = _authorize(a);
        vm.expectRevert(EvidenceGate.UnauthorizedExecutor.selector);
        vm.prank(attacker);
        gate.executeAuthorized(x.id, a);
    }

    function test_Execute_RevertsOnChangedAmount() public {
        EvidenceGate.Action memory a = _swapAction(keccak256("n"));
        Auth memory x = _authorize(a);
        a.maxAmountIn = AMOUNT_IN * 2;
        a.data = _swapData(AMOUNT_IN * 2, EXPECTED_OUT, recipient);
        vm.expectRevert(EvidenceGate.ActionHashMismatch.selector);
        _execute(x, a);
    }

    function test_Execute_RevertsOnChangedTarget() public {
        EvidenceGate.Action memory a = _swapAction(keccak256("n"));
        Auth memory x = _authorize(a);
        a.target = attacker;
        vm.expectRevert(EvidenceGate.ActionHashMismatch.selector);
        _execute(x, a);
    }

    function test_Execute_RevertsOnChangedRecipient() public {
        EvidenceGate.Action memory a = _swapAction(keccak256("n"));
        Auth memory x = _authorize(a);
        a.recipient = attacker;
        a.data = _swapData(AMOUNT_IN, EXPECTED_OUT, attacker);
        vm.expectRevert(EvidenceGate.ActionHashMismatch.selector);
        _execute(x, a);
    }

    function test_Execute_RevertsOnChangedCalldata() public {
        EvidenceGate.Action memory a = _swapAction(keccak256("n"));
        Auth memory x = _authorize(a);
        a.data = _swapData(AMOUNT_IN, 0, recipient); // drop the router-side min output
        vm.expectRevert(EvidenceGate.ActionHashMismatch.selector);
        _execute(x, a);
    }

    function test_Execute_RevertsOnDoubleExecution() public {
        EvidenceGate.Action memory a = _swapAction(keccak256("n"));
        Auth memory x = _authorize(a);
        _execute(x, a);
        vm.expectRevert(EvidenceGate.AuthorizationAlreadyConsumed.selector);
        _execute(x, a);
        assertEq(tokenx.balanceOf(recipient), EXPECTED_OUT);
    }

    function test_Execute_TargetRevertLeavesAuthorizationUnconsumed() public {
        RevertingTarget t = new RevertingTarget();
        _allow(address(t));
        EvidenceGate.Action memory a =
            _callAction(address(t), abi.encodeWithSelector(RevertingTarget.fail.selector), keccak256("n"));
        Auth memory x = _authorize(a);

        bytes memory inner = abi.encodeWithSelector(RevertingTarget.TargetAlwaysReverts.selector, uint256(42));
        vm.expectRevert(abi.encodeWithSelector(EvidenceGate.ExecutionFailed.selector, inner));
        _execute(x, a);

        assertTrue(!_consumed(x.id));
        assertTrue(gate.isAuthorizationActive(x.id));
    }

    function test_Execute_RevertsWhenPaused_AndUnpauseRestores() public {
        EvidenceGate.Action memory a = _swapAction(keccak256("n"));
        Auth memory x = _authorize(a);

        vm.prank(pauser);
        gate.pause();
        vm.expectRevert(Pausable.EnforcedPause.selector);
        _execute(x, a);

        vm.prank(pauser);
        gate.unpause();
        _execute(x, a);
        assertEq(tokenx.balanceOf(recipient), EXPECTED_OUT);
    }

    function test_Pause_OnlyPauser() public {
        vm.expectRevert(
            abi.encodeWithSelector(IAccessControl.AccessControlUnauthorizedAccount.selector, attacker, gate.PAUSER_ROLE())
        );
        vm.prank(attacker);
        gate.pause();
    }

    function test_Execute_RevertsWhenTargetNotAllowed() public {
        RevertingTarget t = new RevertingTarget();
        EvidenceGate.Action memory a =
            _callAction(address(t), abi.encodeWithSelector(RevertingTarget.ok.selector), keccak256("n"));
        Auth memory x = _authorize(a);
        vm.expectRevert(EvidenceGate.TargetNotAllowed.selector);
        _execute(x, a);

        _allow(address(t));
        _execute(x, a);
        assertEq(t.calls(), 1);
    }

    function test_Execute_ReentrancyBlocked() public {
        ReentrantTarget r = new ReentrantTarget(gate);
        RevertingTarget victim = new RevertingTarget();
        _allow(address(r));
        _allow(address(victim));

        EvidenceGate.Action memory innerA =
            _callAction(address(victim), abi.encodeWithSelector(RevertingTarget.ok.selector), keccak256("inner"));
        Auth memory inner = _authorize(innerA, address(r), 300);

        EvidenceGate.Action memory outerA = _callAction(
            address(r), abi.encodeWithSelector(ReentrantTarget.attack.selector, inner.id, innerA), keccak256("outer")
        );
        Auth memory outer = _authorize(outerA);

        bytes memory guardErr = abi.encodeWithSelector(ReentrancyGuard.ReentrancyGuardReentrantCall.selector);
        vm.expectRevert(abi.encodeWithSelector(EvidenceGate.ExecutionFailed.selector, guardErr));
        _execute(outer, outerA);

        assertEq(victim.calls(), 0);
        assertTrue(!_consumed(inner.id));
        assertTrue(!_consumed(outer.id));
    }

    function test_Execute_SlippageCheck() public {
        EvidenceGate.Action memory a = _swapAction(keccak256("n"));
        a.data = _swapData(AMOUNT_IN, 0, recipient); // router accepts anything; the gate must not
        Auth memory x = _authorize(a);
        router.setRate(address(usdc), address(tokenx), 2e18);

        vm.expectRevert(abi.encodeWithSelector(EvidenceGate.SlippageExceeded.selector, uint256(200e18), EXPECTED_OUT));
        _execute(x, a);
        assertTrue(!_consumed(x.id));
    }

    // ------------------------------------------------------------------
    // recordReceipt
    // ------------------------------------------------------------------

    function test_RecordReceipt_Rules() public {
        bytes32 receipt = keccak256("receipt");
        vm.expectRevert(EvidenceGate.AuthorizationNotFound.selector);
        vm.prank(authorizer);
        gate.recordReceipt(keccak256("unknown"), receipt);

        EvidenceGate.Action memory a = _swapAction(keccak256("n"));
        Auth memory x = _authorize(a);
        vm.expectRevert(EvidenceGate.AuthorizationNotConsumed.selector);
        vm.prank(authorizer);
        gate.recordReceipt(x.id, receipt);

        _execute(x, a);

        vm.expectRevert(EvidenceGate.InvalidReceipt.selector);
        vm.prank(authorizer);
        gate.recordReceipt(x.id, bytes32(0));

        vm.expectEmit(true, true, false, true);
        emit ReceiptCommitted(x.id, receipt);
        vm.prank(authorizer);
        gate.recordReceipt(x.id, receipt);
        assertEq(gate.receiptHashes(x.id), receipt);

        vm.expectRevert(EvidenceGate.ReceiptAlreadyRecorded.selector);
        vm.prank(authorizer);
        gate.recordReceipt(x.id, keccak256("other"));
    }

    // ------------------------------------------------------------------
    // Fuzz
    // ------------------------------------------------------------------

    function testFuzz_DifferentCalldataDifferentActionHash(bytes memory d1, bytes memory d2) public {
        vm.assume(keccak256(d1) != keccak256(d2));
        EvidenceGate.Action memory a = _swapAction(keccak256("n"));
        a.data = d1;
        bytes32 h1 = gate.computeActionHash(a);
        a.data = d2;
        bytes32 h2 = gate.computeActionHash(a);
        assertTrue(h1 != h2);
    }

    function testFuzz_DifferentAmountDifferentActionHash(uint256 amount1, uint256 amount2) public {
        vm.assume(amount1 != amount2);
        EvidenceGate.Action memory a = _swapAction(keccak256("n"));
        a.maxAmountIn = amount1;
        a.data = _swapData(amount1, EXPECTED_OUT, recipient);
        bytes32 h1 = gate.computeActionHash(a);
        a.maxAmountIn = amount2;
        a.data = _swapData(amount2, EXPECTED_OUT, recipient);
        bytes32 h2 = gate.computeActionHash(a);
        assertTrue(h1 != h2);
    }

    function testFuzz_ActionHashParity(address target, uint256 value, bytes memory data, uint64 deadline, bytes32 nonce)
        public
    {
        EvidenceGate.Action memory a = _swapAction(nonce);
        a.target = target;
        a.value = value;
        a.data = data;
        a.deadline = deadline;
        assertEq(gate.computeActionHash(a), _expectedActionHash(a));
    }
}
