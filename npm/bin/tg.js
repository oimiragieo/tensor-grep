#!/usr/bin/env node

const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");

const exeExt = process.platform === "win32" ? ".exe" : "";
const binaryPath = path.join(__dirname, `tg${exeExt}`);

if (!fs.existsSync(binaryPath)) {
  console.error(
    "tensor-grep native binary is missing. Reinstall the package or run `node install.js` in the package directory."
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
