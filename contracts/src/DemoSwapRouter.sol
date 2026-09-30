// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {IERC20Metadata} from "@openzeppelin/contracts/token/ERC20/extensions/IERC20Metadata.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";

/// @title DemoSwapRouter
/// @notice Fixed-rate swap venue for demos. Holds reserves of output tokens; the owner sets rates.
/// @dev amountOut = amountIn * rateE18 * 10**decOut / (1e18 * 10**decIn), rounded down.
///      rateE18 is the price of 1 whole tokenIn expressed in whole tokenOut, scaled by 1e18.
///      Testnet / local EVM only.
contract DemoSwapRouter is Ownable {
    using SafeERC20 for IERC20;

    /// @notice tokenIn => tokenOut => rate (1e18 = 1 whole tokenOut per whole tokenIn). 0 = not set.
    mapping(address => mapping(address => uint256)) public rates;

    event RateSet(address indexed tokenIn, address indexed tokenOut, uint256 rateE18);
    event Swapped(
        address indexed sender,
        address indexed tokenIn,
        address indexed tokenOut,
        uint256 amountIn,
        uint256 amountOut,
        address recipient
    );
    event ReserveWithdrawn(address indexed token, address indexed to, uint256 amount);

    error InvalidPair();
    error RateNotSet(address tokenIn, address tokenOut);
    error InsufficientOutput(uint256 amountOut, uint256 minAmountOut);
    error InsufficientLiquidity(uint256 available, uint256 required);

    constructor(address initialOwner) Ownable(initialOwner) {}

    function setRate(address tokenIn, address tokenOut, uint256 rateE18) external onlyOwner {
        if (tokenIn == address(0) || tokenOut == address(0) || tokenIn == tokenOut) revert InvalidPair();
        rates[tokenIn][tokenOut] = rateE18;
        emit RateSet(tokenIn, tokenOut, rateE18);
    }

    function quote(address tokenIn, address tokenOut, uint256 amountIn) public view returns (uint256 amountOut) {
        uint256 rateE18 = rates[tokenIn][tokenOut];
        if (rateE18 == 0) revert RateNotSet(tokenIn, tokenOut);
        uint256 decIn = IERC20Metadata(tokenIn).decimals();
        uint256 decOut = IERC20Metadata(tokenOut).decimals();
        // amountIn * rateE18 * 10**decOut / (1e18 * 10**decIn), with a 512-bit intermediate product.
        if (decOut >= decIn) {
            uint256 up = decOut - decIn;
            if (up <= 18) {
                // 1e18 / 10**up is an exact integer: no precision loss.
                amountOut = Math.mulDiv(amountIn, rateE18, 10 ** (18 - up));
            } else {
                // Exotic decimals gap (> 18): checked arithmetic reverts on overflow instead of wrapping.
                amountOut = Math.mulDiv(amountIn, rateE18, 1) * 10 ** (up - 18);
            }
        } else {
            amountOut = Math.mulDiv(amountIn, rateE18, 1e18 * 10 ** (decIn - decOut));
        }
    }

    function swapExactIn(address tokenIn, address tokenOut, uint256 amountIn, uint256 minAmountOut, address recipient)
        external
        returns (uint256 amountOut)
    {
        if (rates[tokenIn][tokenOut] == 0) revert RateNotSet(tokenIn, tokenOut);
        IERC20(tokenIn).safeTransferFrom(msg.sender, address(this), amountIn);
        amountOut = quote(tokenIn, tokenOut, amountIn);
        if (amountOut < minAmountOut) revert InsufficientOutput(amountOut, minAmountOut);
        uint256 available = IERC20(tokenOut).balanceOf(address(this));
        if (available < amountOut) revert InsufficientLiquidity(available, amountOut);
        IERC20(tokenOut).safeTransfer(recipient, amountOut);
        emit Swapped(msg.sender, tokenIn, tokenOut, amountIn, amountOut, recipient);
    }

    /// @notice Recovers reserves (demo housekeeping).
    function withdrawReserve(address token, address to, uint256 amount) external onlyOwner {
        IERC20(token).safeTransfer(to, amount);
        emit ReserveWithdrawn(token, to, amount);
    }
}
