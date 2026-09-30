// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {AccessControl} from "@openzeppelin/contracts/access/AccessControl.sol";
import {Pausable} from "@openzeppelin/contracts/utils/Pausable.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Address} from "@openzeppelin/contracts/utils/Address.sol";

/// @title EvidenceGate
/// @author SUFFICIA
/// @notice Evidence-bound execution for autonomous agents.
///
///         An offchain authorizer (the SUFFICIA backend) decides, from evidence, whether an agent's
///         economic action is AUTHORIZED. It then commits onchain an authorization that binds:
///           - the intent and its contract hash (what the principal asked for),
///           - the evidence root (why it is justified),
///           - the exact action hash (what will be executed, byte for byte),
///           - the executor (who may execute it),
///           - a short expiry (at most MAX_AUTHORIZATION_TTL seconds).
///
///         This contract only lets that exact action execute, only by that executor, only before expiry,
///         and only once. Any deviation (amount, target, recipient, calldata, value, deadline, nonce)
///         changes the action hash and is rejected.
///
/// @dev Immutable (no proxy). All failures use custom errors.
contract EvidenceGate is AccessControl, Pausable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    // ---------------------------------------------------------------------
    // Constants
    // ---------------------------------------------------------------------

    bytes32 public constant AUTHORIZER_ROLE = keccak256("AUTHORIZER_ROLE");
    bytes32 public constant PAUSER_ROLE = keccak256("PAUSER_ROLE");

    /// @notice Domain separator for action hashes. Reproduced offchain by the backend.
    bytes32 public constant ACTION_DOMAIN = keccak256("EVIDENCE_GATE_ACTION_V1");
    /// @notice Domain separator for authorization ids. Reproduced offchain by the backend.
    bytes32 public constant AUTH_DOMAIN = keccak256("EVIDENCE_GATE_AUTH_V1");

    /// @notice Maximum lifetime of an authorization, counted from the block that stores it.
    uint256 public constant MAX_AUTHORIZATION_TTL = 600;

    // ---------------------------------------------------------------------
    // Types
    // ---------------------------------------------------------------------

    struct Action {
        address target; // contract called
        uint256 value; // native value forwarded (must equal msg.value)
        bytes data; // calldata for target
        address assetIn; // ERC20 spent (address(0) = none)
        uint256 maxAmountIn; // exact approval given to target for the duration of the call
        address assetOut; // ERC20 expected (address(0) = no output check)
        uint256 minAmountOut; // minimum balance increase of assetOut at recipient
        address recipient; // receiver of assetOut
        uint64 deadline; // unix seconds
        bytes32 nonce; // uniqueness
    }

    struct Authorization {
        bytes32 intentId;
        bytes32 contractHash;
        bytes32 evidenceRoot;
        bytes32 actionHash;
        address executor;
        uint64 issuedAt;
        uint64 expiresAt;
        bool consumed;
    }

    // ---------------------------------------------------------------------
    // Storage
    // ---------------------------------------------------------------------

    /// @notice intentId => contractHash registered for that intent.
    mapping(bytes32 => bytes32) public intentContracts;
    /// @dev authorizationId => authorization.
    mapping(bytes32 => Authorization) internal _authorizations;
    /// @notice authorizationId => receipt hash committed after execution.
    mapping(bytes32 => bytes32) public receiptHashes;
    /// @notice Contracts that actions may call.
    mapping(address => bool) public allowedTargets;

    // ---------------------------------------------------------------------
    // Events
    // ---------------------------------------------------------------------

    event IntentRegistered(bytes32 indexed intentId, bytes32 indexed contractHash, uint256 registeredAt);
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
    event ProtectedExecution(
        bytes32 indexed authorizationId, address indexed target, bytes32 indexed actionHash, bytes32 resultHash
    );
    event ReceiptCommitted(bytes32 indexed authorizationId, bytes32 indexed receiptHash);
    event TargetAllowed(address indexed target, bool allowed);
    event Swept(address indexed token, address indexed to, uint256 amount);

    // ---------------------------------------------------------------------
    // Errors
    // ---------------------------------------------------------------------

    error InvalidAddress();
    error InvalidTarget();
    error InvalidIntent();
    error IntentAlreadyRegistered();
    error IntentNotRegistered();
    error ContractHashMismatch();
    error InvalidAuthorization();
    error AuthorizationIdMismatch();
    error AuthorizationExists();
    error AuthorizationExpired();
    error TtlTooLong();
    error AuthorizationNotFound();
    error AuthorizationAlreadyConsumed();
    error AuthorizationNotConsumed();
    error UnauthorizedExecutor();
    error ActionHashMismatch();
    error TargetNotAllowed();
    error ActionDeadlinePassed();
    error ValueMismatch();
    error ExecutionFailed(bytes returndata);
    error SlippageExceeded(uint256 received, uint256 minAmountOut);
    error ReceiptAlreadyRecorded();
    error InvalidReceipt();

    // ---------------------------------------------------------------------
    // Construction
    // ---------------------------------------------------------------------

    constructor(address admin, address authorizer, address pauser, address[] memory initialTargets) {
        if (admin == address(0) || authorizer == address(0) || pauser == address(0)) revert InvalidAddress();
        _grantRole(DEFAULT_ADMIN_ROLE, admin);
        _grantRole(AUTHORIZER_ROLE, authorizer);
        _grantRole(PAUSER_ROLE, pauser);
        for (uint256 i = 0; i < initialTargets.length; ++i) {
            _setTargetAllowed(initialTargets[i], true);
        }
    }

    /// @notice Accepts native value (demo funding). Actions forward exactly msg.value, never the balance.
    receive() external payable {}

    // ---------------------------------------------------------------------
    // Hashing (reproduced offchain, must stay byte-exact)
    // ---------------------------------------------------------------------

    /// @notice keccak256(abi.encode(ACTION_DOMAIN, chainid, this, target, value, keccak256(data), assetIn,
    ///         maxAmountIn, assetOut, minAmountOut, recipient, deadline, nonce)).
    /// @dev The gate address is included to prevent replay across deployments; chainid across chains.
    function computeActionHash(Action calldata a) public view returns (bytes32) {
        return keccak256(
            abi.encode(
                ACTION_DOMAIN,
                block.chainid,
                address(this),
                a.target,
                a.value,
                keccak256(a.data),
                a.assetIn,
                a.maxAmountIn,
                a.assetOut,
                a.minAmountOut,
                a.recipient,
                a.deadline,
                a.nonce
            )
        );
    }

    /// @notice keccak256(abi.encode(AUTH_DOMAIN, contractHash, evidenceRoot, actionHash, executor, nonce)).
    function computeAuthorizationId(
        bytes32 contractHash,
        bytes32 evidenceRoot,
        bytes32 actionHash,
        address executor,
        bytes32 nonce
    ) public pure returns (bytes32) {
        return keccak256(abi.encode(AUTH_DOMAIN, contractHash, evidenceRoot, actionHash, executor, nonce));
    }

    // ---------------------------------------------------------------------
    // Authorizer flow
    // ---------------------------------------------------------------------

    /// @notice Binds an intent to its contract hash. Idempotent (no-op, no event) for the same pair.
    function registerIntent(bytes32 intentId, bytes32 contractHash) external onlyRole(AUTHORIZER_ROLE) {
        if (intentId == bytes32(0) || contractHash == bytes32(0)) revert InvalidIntent();
        bytes32 existing = intentContracts[intentId];
        if (existing == contractHash) return;
        if (existing != bytes32(0)) revert IntentAlreadyRegistered();
        intentContracts[intentId] = contractHash;
        emit IntentRegistered(intentId, contractHash, block.timestamp);
    }

    /// @notice Commits an authorization for one exact action, one executor, a short window, single use.
    function authorize(
        bytes32 authorizationId,
        bytes32 intentId,
        bytes32 contractHash,
        bytes32 evidenceRoot,
        bytes32 actionHash,
        address executor,
        uint64 issuedAt,
        uint64 expiresAt,
        bytes32 nonce
    ) external onlyRole(AUTHORIZER_ROLE) whenNotPaused {
        bytes32 registered = intentContracts[intentId];
        if (registered == bytes32(0)) revert IntentNotRegistered();
        if (registered != contractHash) revert ContractHashMismatch();
        if (evidenceRoot == bytes32(0) || actionHash == bytes32(0) || executor == address(0) || expiresAt <= issuedAt)
        {
            revert InvalidAuthorization();
        }
        if (computeAuthorizationId(contractHash, evidenceRoot, actionHash, executor, nonce) != authorizationId) {
            revert AuthorizationIdMismatch();
        }
        Authorization storage auth = _authorizations[authorizationId];
        if (auth.executor != address(0)) revert AuthorizationExists();
        if (expiresAt <= block.timestamp) revert AuthorizationExpired();
        if (expiresAt > block.timestamp + MAX_AUTHORIZATION_TTL) revert TtlTooLong();

        auth.intentId = intentId;
        auth.contractHash = contractHash;
        auth.evidenceRoot = evidenceRoot;
        auth.actionHash = actionHash;
        auth.executor = executor;
        auth.issuedAt = issuedAt;
        auth.expiresAt = expiresAt;

        emit ExecutionAuthorized(authorizationId, intentId, evidenceRoot, contractHash, actionHash, executor, expiresAt);
    }

    /// @notice Commits the hash of the offchain execution receipt for a consumed authorization.
    function recordReceipt(bytes32 authorizationId, bytes32 receiptHash) external onlyRole(AUTHORIZER_ROLE) {
        Authorization storage auth = _authorizations[authorizationId];
        if (auth.executor == address(0)) revert AuthorizationNotFound();
        if (!auth.consumed) revert AuthorizationNotConsumed();
        if (receiptHashes[authorizationId] != bytes32(0)) revert ReceiptAlreadyRecorded();
        if (receiptHash == bytes32(0)) revert InvalidReceipt();
        receiptHashes[authorizationId] = receiptHash;
        emit ReceiptCommitted(authorizationId, receiptHash);
    }

    // ---------------------------------------------------------------------
    // Executor flow
    // ---------------------------------------------------------------------

    /// @notice Executes the exact authorized action. Consumes the authorization only if the whole call
    ///         (target call, approval reset, output check) succeeds; any failure reverts everything.
    function executeAuthorized(bytes32 authorizationId, Action calldata action)
        external
        payable
        nonReentrant
        whenNotPaused
        returns (bytes memory result)
    {
        bytes32 actionHash = _checkExecution(authorizationId, action);

        // Effects
        _authorizations[authorizationId].consumed = true;
        emit AuthorizationConsumed(authorizationId, actionHash, msg.sender);

        // Interactions
        result = _execute(action);

        emit ProtectedExecution(authorizationId, action.target, actionHash, keccak256(result));
    }

    function _checkExecution(bytes32 authorizationId, Action calldata action)
        private
        view
        returns (bytes32 actionHash)
    {
        Authorization storage auth = _authorizations[authorizationId];
        address executor = auth.executor;
        if (executor == address(0)) revert AuthorizationNotFound();
        if (auth.consumed) revert AuthorizationAlreadyConsumed();
        if (block.timestamp > auth.expiresAt) revert AuthorizationExpired();
        if (msg.sender != executor) revert UnauthorizedExecutor();
        actionHash = computeActionHash(action);
        if (actionHash != auth.actionHash) revert ActionHashMismatch();
        if (!allowedTargets[action.target]) revert TargetNotAllowed();
        if (block.timestamp > action.deadline) revert ActionDeadlinePassed();
        if (msg.value != action.value) revert ValueMismatch();
    }

    function _execute(Action calldata action) private returns (bytes memory result) {
        address target = action.target;
        IERC20 assetIn = IERC20(action.assetIn);
        IERC20 assetOut = IERC20(action.assetOut);
        bool approved = address(assetIn) != address(0) && action.maxAmountIn > 0;

        if (approved) assetIn.forceApprove(target, action.maxAmountIn);

        uint256 balanceBefore;
        if (address(assetOut) != address(0)) balanceBefore = assetOut.balanceOf(action.recipient);

        bool success;
        (success, result) = target.call{value: action.value}(action.data);
        if (!success) revert ExecutionFailed(result);

        if (approved) assetIn.forceApprove(target, 0);

        if (address(assetOut) != address(0)) {
            uint256 balanceAfter = assetOut.balanceOf(action.recipient);
            uint256 received = balanceAfter > balanceBefore ? balanceAfter - balanceBefore : 0;
            if (received < action.minAmountOut) revert SlippageExceeded(received, action.minAmountOut);
        }
    }

    // ---------------------------------------------------------------------
    // Views
    // ---------------------------------------------------------------------

    function getAuthorization(bytes32 authorizationId) external view returns (Authorization memory) {
        return _authorizations[authorizationId];
    }

    /// @notice True if the authorization exists, is not consumed, and is not expired.
    function isAuthorizationActive(bytes32 authorizationId) external view returns (bool) {
        Authorization storage auth = _authorizations[authorizationId];
        return auth.executor != address(0) && !auth.consumed && block.timestamp <= auth.expiresAt;
    }

    // ---------------------------------------------------------------------
    // Admin
    // ---------------------------------------------------------------------

    function setTargetAllowed(address target, bool allowed) external onlyRole(DEFAULT_ADMIN_ROLE) {
        _setTargetAllowed(target, allowed);
    }

    function pause() external onlyRole(PAUSER_ROLE) {
        _pause();
    }

    function unpause() external onlyRole(PAUSER_ROLE) {
        _unpause();
    }

    /// @notice Recovers demo funds. token == address(0) means native currency.
    function sweep(address token, address to, uint256 amount) external onlyRole(DEFAULT_ADMIN_ROLE) nonReentrant {
        if (to == address(0)) revert InvalidAddress();
        if (token == address(0)) {
            Address.sendValue(payable(to), amount);
        } else {
            IERC20(token).safeTransfer(to, amount);
        }
        emit Swept(token, to, amount);
    }

    function _setTargetAllowed(address target, bool allowed) private {
        if (target == address(0) || target == address(this)) revert InvalidTarget();
        allowedTargets[target] = allowed;
        emit TargetAllowed(target, allowed);
    }
}
