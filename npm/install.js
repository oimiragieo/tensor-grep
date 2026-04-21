const fs = require('fs');
const path = require('path');
const https = require('https');
const packageJson = require('./package.json');

const VERSION = `v${packageJson.version}`;
const GITHUB_REPO = 'oimiragieo/tensor-grep';

const RELEASE_ASSET_MAP = {
  'win32:x64': 'tg-windows-amd64-cpu.exe',
  'linux:x64': 'tg-linux-amd64-cpu',
  'darwin:x64': 'tg-macos-amd64-cpu'
};

const assetName = RELEASE_ASSET_MAP[`${process.platform}:${process.arch}`];

if (!assetName) {
  console.error(`Unsupported platform/architecture: ${process.platform}/${process.arch}`);
  process.exit(1);
}

const exeExt = process.platform === 'win32' ? '.exe' : '';
const downloadUrl = `https://github.com/${GITHUB_REPO}/releases/download/${VERSION}/${assetName}`;

const binDir = path.join(__dirname, 'bin');
const binPath = path.join(binDir, `tg${exeExt}`);

if (!fs.existsSync(binDir)) {
  fs.mkdirSync(binDir, { recursive: true });
}

console.log(`Downloading tensor-grep binary from ${downloadUrl}`);

const file = fs.createWriteStream(binPath);

https.get(downloadUrl, (response) => {
  if (response.statusCode === 302 || response.statusCode === 301) {
    https.get(response.headers.location, (redirectResponse) => {
      redirectResponse.pipe(file);
      file.on('finish', () => {
        file.close();
        if (process.platform !== 'win32') {
            fs.chmodSync(binPath, 0o755);
        }
        console.log('Download complete!');
      });
    });
  } else if (response.statusCode !== 200) {
    fs.unlinkSync(binPath);
    console.error(`Download failed with status code ${response.statusCode}`);
    process.exit(1);
  } else {
    response.pipe(file);
    file.on('finish', () => {
      file.close();
      if (process.platform !== 'win32') {
          fs.chmodSync(binPath, 0o755);
      }
      console.log('Download complete!');
    });
  }
}).on('error', (err) => {
  fs.unlinkSync(binPath);
  console.error(`Download failed: ${err.message}`);
  process.exit(1);
});
