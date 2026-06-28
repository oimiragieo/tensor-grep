const fs = require('fs');
const path = require('path');
const https = require('https');
const crypto = require('crypto');
const { URL } = require('url');
const packageJson = require('./package.json');

const VERSION = `v${packageJson.version}`;
const GITHUB_REPO = 'oimiragieo/tensor-grep';

const RELEASE_ASSET_MAP = {
  'win32:x64': 'tg-windows-amd64-cpu.exe',
  'linux:x64': 'tg-linux-amd64-cpu',
  'darwin:x64': 'tg-macos-amd64-cpu'
};

// Only follow redirects that stay on GitHub / its asset CDN. A downloaded binary is
// made executable, so an attacker-influenced redirect must not be able to point us at
// an arbitrary host (audit S4).
function isAllowedHost(hostname) {
  return (
    hostname === 'github.com' ||
    hostname === 'githubusercontent.com' ||
    hostname.endsWith('.githubusercontent.com')
  );
}

// Time-bound each request and cap the response size so a stalled or oversized release
// response can't hang the install or exhaust memory before the checksum comparison runs.
const DOWNLOAD_TIMEOUT_MS = 60000;
const MAX_DOWNLOAD_BYTES = 256 * 1024 * 1024; // generous for a release binary; rejects abuse

// Download a URL to a Buffer, following a bounded number of redirects, enforcing HTTPS,
// a trusted host, and a final 200 status before returning any bytes.
function download(url, redirectsLeft = 5) {
  return new Promise((resolve, reject) => {
    let parsed;
    try {
      parsed = new URL(url);
    } catch (err) {
      reject(new Error(`Invalid URL: ${url}`));
      return;
    }
    if (parsed.protocol !== 'https:') {
      reject(new Error(`Refusing non-HTTPS URL: ${url}`));
      return;
    }
    if (!isAllowedHost(parsed.hostname)) {
      reject(new Error(`Refusing download from untrusted host: ${parsed.hostname}`));
      return;
    }
    const req = https.get(url, { timeout: DOWNLOAD_TIMEOUT_MS }, (response) => {
      const { statusCode } = response;
      if ([301, 302, 303, 307, 308].includes(statusCode)) {
        response.resume();
        if (redirectsLeft <= 0) {
          reject(new Error('Too many redirects'));
          return;
        }
        const location = response.headers.location;
        if (!location) {
          reject(new Error('Redirect response without a location header'));
          return;
        }
        resolve(download(new URL(location, url).toString(), redirectsLeft - 1));
        return;
      }
      if (statusCode !== 200) {
        response.resume();
        reject(new Error(`Download failed with status code ${statusCode}`));
        return;
      }
      const chunks = [];
      let total = 0;
      response.on('data', (chunk) => {
        total += chunk.length;
        if (total > MAX_DOWNLOAD_BYTES) {
          response.destroy();
          req.destroy();
          reject(new Error(`Download exceeded ${MAX_DOWNLOAD_BYTES} bytes`));
          return;
        }
        chunks.push(chunk);
      });
      response.on('end', () => resolve(Buffer.concat(chunks)));
      response.on('error', reject);
    });
    req.on('timeout', () => {
      req.destroy(new Error(`Download timed out after ${DOWNLOAD_TIMEOUT_MS}ms`));
    });
    req.on('error', reject);
  });
}

// Find the published sha256 for an asset in a CHECKSUMS.txt ("<sha256>  <asset>").
function expectedChecksum(checksumsText, assetName) {
  for (const rawLine of checksumsText.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line) continue;
    const parts = line.split(/\s+/);
    if (parts.length >= 2 && parts[parts.length - 1] === assetName) {
      return parts[0].toLowerCase();
    }
  }
  return null;
}

async function main() {
  const assetName = RELEASE_ASSET_MAP[`${process.platform}:${process.arch}`];
  if (!assetName) {
    console.error(`Unsupported platform/architecture: ${process.platform}/${process.arch}`);
    process.exit(1);
  }

  const exeExt = process.platform === 'win32' ? '.exe' : '';
  const releaseBase = `https://github.com/${GITHUB_REPO}/releases/download/${VERSION}`;
  const binDir = path.join(__dirname, 'bin');
  const binPath = path.join(binDir, `tg${exeExt}`);
  if (!fs.existsSync(binDir)) {
    fs.mkdirSync(binDir, { recursive: true });
  }

  console.log(`Downloading tensor-grep binary from ${releaseBase}/${assetName}`);

  let binary;
  let checksumsText;
  try {
    binary = await download(`${releaseBase}/${assetName}`);
    checksumsText = (await download(`${releaseBase}/CHECKSUMS.txt`)).toString('utf8');
  } catch (err) {
    console.error(`Download failed: ${err.message}`);
    process.exit(1);
  }

  // Verify the downloaded bytes against the signed manifest BEFORE writing or making the
  // binary executable. Fail closed: never install an unverified or mismatched binary.
  const expected = expectedChecksum(checksumsText, assetName);
  const actual = crypto.createHash('sha256').update(binary).digest('hex');
  if (!expected) {
    console.error(`No published checksum for ${assetName}; refusing to install an unverified binary.`);
    process.exit(1);
  }
  if (expected !== actual) {
    console.error(`Checksum MISMATCH for ${assetName} (expected ${expected}, got ${actual}); refusing to install.`);
    process.exit(1);
  }

  fs.writeFileSync(binPath, binary);
  if (process.platform !== 'win32') {
    fs.chmodSync(binPath, 0o755);
  }
  console.log('Download verified (sha256) and complete!');
}

// Only run the download when executed directly as the npm postinstall step, so the
// module can be required (e.g. by tests) without triggering a network fetch.
if (require.main === module) {
  main();
}

module.exports = { expectedChecksum, isAllowedHost, download };
