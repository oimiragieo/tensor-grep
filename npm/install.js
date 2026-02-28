const fs = require('fs');
const path = require('path');
const https = require('https');
const { execSync } = require('child_process');

const VERSION = 'v0.2.0';
const GITHUB_REPO = 'tensor-grep/tensor-grep';

const PLATFORM_MAP = {
  win32: 'windows',
  darwin: 'macos',
  linux: 'linux'
};

const ARCH_MAP = {
  x64: 'amd64',
  arm64: 'arm64'
};

const platform = PLATFORM_MAP[process.platform];
const arch = ARCH_MAP[process.arch];

if (!platform || !arch) {
  console.error(`Unsupported platform/architecture: ${process.platform}/${process.arch}`);
  process.exit(1);
}

const exeExt = platform === 'windows' ? '.exe' : '';
const binName = `tg-${platform}-${arch}${exeExt}`;
const downloadUrl = `https://github.com/${GITHUB_REPO}/releases/download/${VERSION}/${binName}`;

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
        if (platform !== 'windows') {
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
      if (platform !== 'windows') {
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
