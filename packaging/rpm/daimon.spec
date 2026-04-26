# RPM spec for DAIMON. Built from the linux-x86_64 release tarball
# produced by release-binaries.yml. Bump `Version` on each release;
# `Release` resets to 1 for a new upstream version.
#
# Build:
#   rpmbuild -bb packaging/rpm/daimon.spec
#
# Source0 expects the tarball at SOURCES/daimon-linux-x86_64.tar.gz —
# the COPR / Fedora pipeline downloads it from the GitHub Release in
# its `_disturl` step before invoking rpmbuild.

Name:           daimon
Version:        0.0.0
Release:        1%{?dist}
Summary:        Terminal-first agentic-first TCG / autobattler

License:        Proprietary
URL:            https://github.com/aurorasuperbot/daimon
Source0:        https://github.com/aurorasuperbot/daimon/releases/download/daimon-v%{version}/daimon-linux-x86_64.tar.gz

BuildArch:      x86_64
Requires:       fontconfig
Requires:       libxkbcommon
Requires:       libxcb

%description
DAIMON is a terminal-first, agentic-first trading-card game and
autobattler. The CLI runs inside a bundled WezTerm so card art
renders pixel-perfect via the Kitty Graphics Protocol, and a
PostToolUse hook in Claude Code mines in-game currency from
productive agent activity. Ships with the dmn-mcp stdio server
so AI agents can play (and lose) autonomously.

%prep
%setup -q -n daimon-linux-x86_64

%build
# No-op; the upstream tarball is already a built standalone tree.

%install
mkdir -p %{buildroot}%{_libdir}/daimon
cp -r * %{buildroot}%{_libdir}/daimon/
mkdir -p %{buildroot}%{_bindir}
ln -sf %{_libdir}/daimon/daimon %{buildroot}%{_bindir}/daimon
ln -sf %{_libdir}/daimon/dmn-mcp %{buildroot}%{_bindir}/dmn-mcp

%files
%{_bindir}/daimon
%{_bindir}/dmn-mcp
%{_libdir}/daimon

%post
echo "DAIMON installed. Run 'daimon onboard' to set up."

%changelog
* Sun Apr 26 2026 aurorasuperbot <aurorasuperbot@users.noreply.github.com> - 0.0.0-1
- Initial RPM release.
