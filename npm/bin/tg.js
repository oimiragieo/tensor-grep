#!/usr/bin/env node

const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");

const PLATFORM_PACKAGES = {
  "win32:x64": "@tensor-grep/cli-win32-x64",
  "darwin:arm64": "@tensor-grep/cli-darwin-arm64",
  "darwin:x64": "@tensor-grep/cli-darwin-x64",
  "linux:x64": "@tensor-grep/cli-linux-x64",
  "linux:arm64": "@tensor-grep/cli-linux-arm64"
};

const exeExt = process.platform === "win32" ? ".exe" : "";
const platformKey = `${process.platform}:${process.arch}`;
const pkgName = PLATFORM_PACKAGES[platformKey];

function resolveBinaryPath() {
  if (pkgName) {
    try {
      const pkgJsonPath = require.resolve(`${pkgName}/package.json`);
      const candidate = path.join(path.dirname(pkgJsonPath), "bin", `tg${exeExt}`);
      if (fs.existsSync(candidate)) {
        return candidate;
      }
    } catch (_) {
      // Fall through to local binary check.
    }
  }
  const localBinary = path.join(__dirname, `tg${exeExt}`);
  if (fs.existsSync(localBinary)) {
    return localBinary;
  }
  return null;
}

const binaryPath = resolveBinaryPath();

if (!binaryPath) {
  const targetDesc = pkgName ? `${pkgName} (${platformKey})` : platformKey;
  console.error(
    `tensor-grep native binary is missing for ${targetDesc}. Reinstall without --no-optional or install the binary directly from GitHub Releases.`
  );
  process.exit(1);
}

const child = spawn(binaryPath, process.argv.slice(2), { stdio: "inherit" });

child.on("error", (error) => {
  console.error(`Failed to launch tensor-grep native binary: ${error.message}`);
  process.exit(1);
});

child.on("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exit(code ?? 1);
});
