// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;

/// @notice Immutable SHA-256 digests. Does not certify investment truth.
contract AetherResearchAnchor {
    struct Anchor { bytes32 reportHash; bytes32 manifestHash; bytes32 configHash; uint64 timestamp; }
    mapping(address => mapping(bytes32 => Anchor)) public anchors;
    event Anchored(address indexed reporter, bytes32 indexed reportId, bytes32 reportHash, bytes32 manifestHash, bytes32 configHash, uint64 timestamp);
    error AlreadyAnchored();
    error EmptyDigest();
    function anchor(bytes32 reportId, bytes32 reportHash, bytes32 manifestHash, bytes32 configHash) external {
        if (anchors[msg.sender][reportId].timestamp != 0) revert AlreadyAnchored();
        if (reportId == bytes32(0) || reportHash == bytes32(0) || manifestHash == bytes32(0) || configHash == bytes32(0)) revert EmptyDigest();
        uint64 t = uint64(block.timestamp);
        anchors[msg.sender][reportId] = Anchor(reportHash, manifestHash, configHash, t);
        emit Anchored(msg.sender, reportId, reportHash, manifestHash, configHash, t);
    }
}
