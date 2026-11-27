# Homebrew Formula for tensor-grep
# Save this in a homebrew-tap repository (e.g. oimiragieo/homebrew-tap/tensor-grep.rb)

class TensorGrep < Formula
  desc "Fast local code search, symbol navigation, and AI context via Rust & MCP"
  homepage "https://github.com/oimiragieo/tensor-grep"
  license "Apache-2.0"
  TENSOR_GREP_VERSION = "1.142.0"
  version TENSOR_GREP_VERSION

  # NOTE: a `sha256 "<digest>"` line is stamped after each url into the PUBLISHED formula at
  # release-bundle time from CHECKSUMS.txt (scripts/prepare_package_manager_release.py). This
  # source template carries none on purpose: the binary digests only exist after the release
  # builds them (the same post-build constraint as the winget InstallerSha256).
  on_macos do
    on_arm do
      url "https://github.com/oimiragieo/tensor-grep/releases/download/v#{version}/tg-macos-arm64-cpu"
    end
    on_intel do
      url "https://github.com/oimiragieo/tensor-grep/releases/download/v#{version}/tg-macos-amd64-cpu"
    end
  end

  on_linux do
    on_arm do
      url "https://github.com/oimiragieo/tensor-grep/releases/download/v#{version}/tg-linux-arm64-cpu"
    end
    on_intel do
      url "https://github.com/oimiragieo/tensor-grep/releases/download/v#{version}/tg-linux-amd64-cpu"
    end
  end

  def install
    if OS.mac?
      if Hardware::CPU.arm?
        bin.install "tg-macos-arm64-cpu" => "tg"
      else
        bin.install "tg-macos-amd64-cpu" => "tg"
      end
    elsif OS.linux?
      if Hardware::CPU.arm?
        bin.install "tg-linux-arm64-cpu" => "tg"
      else
        bin.install "tg-linux-amd64-cpu" => "tg"
      end
    end
  end

  test do
    system "#{bin}/tg", "--help"
  end
end
