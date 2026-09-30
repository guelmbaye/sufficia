#!/usr/bin/env node
// Builds SUFFICIA contracts with solcjs (npm `solc`), no Foundry required.
//
//   node scripts/build.mjs               compile src/**/*.sol + test/mocks/*.sol, write artifacts
//   node scripts/build.mjs --with-tests  also compile-check test/*.t.sol and script/*.s.sol
//
// Artifacts: contracts/out/<ContractName>.json, and a copy of the backend-relevant ones in
// apps/api/sufficia/blockchain/artifacts/<ContractName>.json.
// Exits non-zero on any compiler error; warnings are printed.

import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const solc = require("solc");

const CONTRACTS_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const REPO_ROOT = path.resolve(CONTRACTS_DIR, "..");
const NODE_MODULES = path.join(CONTRACTS_DIR, "node_modules");
const OUT_DIR = path.join(CONTRACTS_DIR, "out");
const API_ARTIFACTS_DIR = path.join(REPO_ROOT, "apps", "api", "sufficia", "blockchain", "artifacts");

const EXPORTED_TO_API = ["EvidenceGate", "DemoERC20", "DemoSwapRouter", "RevertingTarget", "ReentrantTarget"];

const EVM_VERSION = "cancun";
const OPTIMIZER = { enabled: true, runs: 200 };

// Import remappings, same as foundry.toml.
const REMAPPINGS = [
  ["@openzeppelin/", path.join(NODE_MODULES, "@openzeppelin") + path.sep],
  ["forge-std/", path.join(NODE_MODULES, "forge-std") + path.sep],
  // forge-std 1.1.2 (npm) imports "ds-test/test.sol"; ds-test is shipped separately on npm.
  ["ds-test/", path.join(NODE_MODULES, "@shunkakinoki", "ds-test") + path.sep],
];

const withTests = process.argv.includes("--with-tests");

function listSol(dir, { recursive, suffix = ".sol" }) {
  const abs = path.join(CONTRACTS_DIR, dir);
  if (!fs.existsSync(abs)) return [];
  const out = [];
  for (const entry of fs.readdirSync(abs, { withFileTypes: true })) {
    const rel = path.posix.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (recursive) out.push(...listSol(rel, { recursive, suffix }));
    } else if (entry.name.endsWith(suffix)) {
      out.push(rel);
    }
  }
  return out.sort();
}

const sourceFiles = [
  ...listSol("src", { recursive: true }),
  ...listSol("test/mocks", { recursive: false }),
];
if (withTests) {
  sourceFiles.push(...listSol("test", { recursive: false, suffix: ".t.sol" }));
  sourceFiles.push(...listSol("script", { recursive: false, suffix: ".s.sol" }));
}

const sources = {};
for (const rel of sourceFiles) {
  sources[rel] = { content: fs.readFileSync(path.join(CONTRACTS_DIR, rel), "utf8") };
}

function findImports(importPath) {
  for (const [prefix, target] of REMAPPINGS) {
    if (importPath.startsWith(prefix)) {
      const abs = path.join(target, importPath.slice(prefix.length));
      if (fs.existsSync(abs)) return { contents: fs.readFileSync(abs, "utf8") };
      return { error: `Remapped import not found: ${importPath} -> ${abs}` };
    }
  }
  // Project-relative imports (resolved by solc against the importing source unit's path).
  const local = path.join(CONTRACTS_DIR, importPath);
  if (fs.existsSync(local)) return { contents: fs.readFileSync(local, "utf8") };
  return { error: `Import not found: ${importPath}` };
}

const input = {
  language: "Solidity",
  sources,
  settings: {
    evmVersion: EVM_VERSION,
    optimizer: OPTIMIZER,
    viaIR: false,
    outputSelection: {
      "*": { "*": ["abi", "evm.bytecode.object", "evm.deployedBytecode.object"] },
    },
  },
};

console.log(
  `solc ${solc.version()} | evm ${EVM_VERSION} | optimizer ${OPTIMIZER.enabled} (${OPTIMIZER.runs} runs) | ` +
    `${sourceFiles.length} source files${withTests ? " (with tests/scripts)" : ""}`,
);

const output = JSON.parse(solc.compile(JSON.stringify(input), { import: findImports }));

let errorCount = 0;
let warningCount = 0;
for (const diag of output.errors ?? []) {
  const msg = diag.formattedMessage ?? diag.message;
  if (diag.severity === "error") {
    errorCount += 1;
    console.error(msg);
  } else {
    // Don't drown the output in warnings from vendored forge-std / ds-test.
    const file = diag.sourceLocation?.file ?? "";
    if (file.startsWith("forge-std/") || file.startsWith("ds-test/")) continue;
    // 5574 = contract code size > 24576 bytes: expected for Foundry test contracts, which are never deployed.
    if (diag.errorCode === "5574" && file.endsWith(".t.sol")) continue;
    warningCount += 1;
    console.warn(msg);
  }
}
if (errorCount > 0) {
  console.error(`\nBuild FAILED: ${errorCount} error(s).`);
  process.exit(1);
}

fs.mkdirSync(OUT_DIR, { recursive: true });

const compilerInfo = { version: solc.version(), evmVersion: EVM_VERSION, optimizer: OPTIMIZER };
const artifacts = new Map();
const isProjectSource = (file) => file.startsWith("src/") || file.startsWith("test/mocks/");

for (const [file, contracts] of Object.entries(output.contracts ?? {})) {
  // Only project sources get artifacts; tests/scripts/libraries are compile-checked only.
  if (!isProjectSource(file)) continue;
  for (const [name, c] of Object.entries(contracts)) {
    if (artifacts.has(name)) {
      console.error(`Duplicate contract name ${name} (${file}); artifact names must be unique.`);
      process.exit(1);
    }
    const artifact = {
      contractName: name,
      sourceName: file,
      abi: c.abi,
      bytecode: "0x" + c.evm.bytecode.object,
      deployedBytecode: "0x" + c.evm.deployedBytecode.object,
      compiler: compilerInfo,
    };
    artifacts.set(name, artifact);
    fs.writeFileSync(path.join(OUT_DIR, `${name}.json`), JSON.stringify(artifact, null, 2) + "\n");
  }
}

fs.mkdirSync(API_ARTIFACTS_DIR, { recursive: true });
for (const name of EXPORTED_TO_API) {
  const artifact = artifacts.get(name);
  if (!artifact) {
    console.error(`Expected contract ${name} was not produced by the compiler.`);
    process.exit(1);
  }
  fs.writeFileSync(path.join(API_ARTIFACTS_DIR, `${name}.json`), JSON.stringify(artifact, null, 2) + "\n");
}

for (const [name, a] of [...artifacts.entries()].sort()) {
  const runtimeBytes = (a.deployedBytecode.length - 2) / 2;
  console.log(`  ${name.padEnd(18)} runtime ${String(runtimeBytes).padStart(6)} bytes`);
}
console.log(
  `Build OK: ${artifacts.size} artifacts -> ${path.relative(REPO_ROOT, OUT_DIR)}/, ` +
    `${EXPORTED_TO_API.length} copied -> ${path.relative(REPO_ROOT, API_ARTIFACTS_DIR)}/ ` +
    `(${warningCount} warning(s)).`,
);
