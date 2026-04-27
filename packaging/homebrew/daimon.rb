# Homebrew formula for DAIMON. Lives in the
# `aurorasuperbot/homebrew-daimon` tap repo; users install via:
#
#   brew tap aurorasuperbot/daimon
#   brew install daimon
#
# After a `daimon-vX.Y.Z` tag fires release-binaries.yml, run
# `brew bump-formula-pr` (or edit by hand) to bump `version` + the
# matching SHA256s. Both arches share the same version string.
class Daimon < Formula
  desc "Terminal-first agentic-first TCG / autobattler"
  homepage "https://github.com/aurorasuperbot/daimon"
  version "0.0.0"  # BUMP ME

  on_macos do
    on_arm do
      url "https://github.com/aurorasuperbot/daimon/releases/download/daimon-v#{version}/daimon-macos-aarch64.tar.gz"
      sha256 "0000000000000000000000000000000000000000000000000000000000000000"  # BUMP ME
    end
    on_intel do
      url "https://github.com/aurorasuperbot/daimon/releases/download/daimon-v#{version}/daimon-macos-x86_64.tar.gz"
      sha256 "0000000000000000000000000000000000000000000000000000000000000000"  # BUMP ME
    end
  end

  on_linux do
    url "https://github.com/aurorasuperbot/daimon/releases/download/daimon-v#{version}/daimon-linux-x86_64.tar.gz"
    sha256 "0000000000000000000000000000000000000000000000000000000000000000"  # BUMP ME
  end

  def install
    # The archive extracts into a `daimon-{os}-{arch}/` directory; we
    # `cp_r` the contents of that dir into libexec rather than mirror
    # Homebrew's expected `bin/` layout.
    libexec.install Dir["*"]
    bin.install_symlink libexec/"daimon"
    bin.install_symlink libexec/"dmn-mcp"
  end

  def caveats
    <<~EOS
      DAIMON is installed. Run:

        daimon onboard

      to generate your identity, save the recovery file, and wire the
      Claude Code MCP integration. The bundled WezTerm renders card
      art via the Kitty Graphics Protocol; if you launch DAIMON from
      a different terminal it will auto-spawn a window in our terminal.
    EOS
  end

  test do
    system "#{bin}/daimon", "--version"
    system "#{bin}/daimon", "doctor"
  end
end
