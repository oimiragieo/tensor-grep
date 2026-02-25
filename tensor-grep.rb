# Homebrew Formula for tensor-grep
# Save this in a homebrew-tap repository (e.g. oimiragieo/homebrew-tap/tensor-grep.rb)

class TensorGrep < Formula
  desc "GPU-Accelerated Semantic Log Parsing CLI combining raw regex speed with cyBERT"
  homepage "https://github.com/oimiragieo/tensor-grep"
  version "1.0.0-rc1"
  
  if OS.mac?
    url "https://github.com/oimiragieo/tensor-grep/releases/download/v1.0.0-rc1/tg-macos"
    # Note: Replace this with the actual sha256 of the macOS binary once GitHub Actions finishes
    # sha256 "PLACEHOLDER_MAC_SHA256" 
  elsif OS.linux?
    url "https://github.com/oimiragieo/tensor-grep/releases/download/v1.0.0-rc1/tg-linux"
    # sha256 "PLACEHOLDER_LINUX_SHA256"
  end

  def install
    if OS.mac?
      bin.install "tg-macos" => "tg"
    elsif OS.linux?
      bin.install "tg-linux" => "tg"
    end
  end

  test do
    system "#{bin}/tg", "--help"
  end
end
