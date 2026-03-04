# Homebrew Formula for tensor-grep
# Save this in a homebrew-tap repository (e.g. oimiragieo/homebrew-tap/tensor-grep.rb)

class TensorGrep < Formula
  desc "GPU-Accelerated Semantic Log Parsing CLI combining raw regex speed with cyBERT"
  homepage "https://github.com/oimiragieo/tensor-grep"
  TENSOR_GREP_VERSION = "0.22.0"
  version TENSOR_GREP_VERSION
  
  if OS.mac?
    url "https://github.com/oimiragieo/tensor-grep/releases/download/v#{version}/tg-macos-amd64-cpu"
  elsif OS.linux?
    url "https://github.com/oimiragieo/tensor-grep/releases/download/v#{version}/tg-linux-amd64-cpu"
  end

  def install
    if OS.mac?
      bin.install "tg-macos-amd64-cpu" => "tg"
    elsif OS.linux?
      bin.install "tg-linux-amd64-cpu" => "tg"
    end
  end

  test do
    system "#{bin}/tg", "--help"
  end
end
