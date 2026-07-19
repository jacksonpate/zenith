#
# evdi.spec -- userspace half of EVDI (Extensible Virtual Display Interface).
#
# This spec builds ONLY the userspace shared library (evdi/library/).
# The kernel module (evdi/module/) is built by the companion spec
# evdi-kmod.spec, which produces akmod-evdi.
#
# The two MUST stay separate source packages.  akmods rebuilds the kmod SRPM on
# every kernel update and then does:
#     dnf -y install --disablerepo='*' <every non-debuginfo rpm it just built>
# so any userspace subpackage living in the kmod SRPM would be rebuilt and
# reinstalled on every kernel bump.  Keep userspace out of the kmod SRPM.
#
# Fedora ships no evdi package at all (not in Fedora, not in RPM Fusion), so
# these two specs are the whole stack.
#

Name:           evdi
Version:        1.15.0
Release:        1%{?dist}
Summary:        Userspace library for EVDI (Extensible Virtual Display Interface)

# Upstream is split-licensed and the top-level LICENSE (MIT) is NOT the whole
# story.  Everything shipped by *this* package comes from library/, whose
# sources all carry "SPDX-License-Identifier: LGPL-2.1-or-later" and which
# ships library/LICENSE = LGPL v2.1.  The kernel module is GPL-2.0-only and is
# packaged by evdi-kmod.spec, which sets its License tag accordingly.
License:        LGPL-2.1-or-later
URL:            https://github.com/DisplayLink/evdi
Source0:        %{url}/archive/refs/tags/v%{version}/evdi-%{version}.tar.gz

BuildRequires:  gcc
BuildRequires:  make
# library/Makefile calls `pkg-config --cflags-only-I libdrm`
BuildRequires:  pkgconfig(libdrm)

%description
EVDI (Extensible Virtual Display Interface) is a Linux kernel module and
userspace library that let an application create a virtual display: a DRM
connector the compositor treats as a real monitor, whose framebuffer is handed
back to userspace instead of being scanned out to physical hardware.

This source package builds the userspace library.  The kernel module is
provided by akmod-evdi (source package evdi-kmod).

%package -n libevdi
Summary:        EVDI userspace shared library
License:        LGPL-2.1-or-later
# The library is useless without the kernel module: evdi_add_device() writes to
# /sys/devices/platform/evdi/add, which only exists once evdi.ko is loaded.
# Both akmod-evdi and kmod-evdi-<kver> Provide: evdi-kmod, so this is satisfied
# by whichever flavour the user has.
Requires:       evdi-kmod >= %{version}

%description -n libevdi
The EVDI userspace shared library (libevdi.so.1).  It talks to the evdi kernel
module to create and drive virtual displays.

Installed into %{_libdir} so that it is picked up by the normal linker cache;
consumers that dlopen it (e.g. ctypes.CDLL("libevdi.so.1")) therefore just work.

%package -n libevdi-devel
Summary:        Development files for libevdi
License:        LGPL-2.1-or-later
Requires:       libevdi%{?_isa} = %{version}-%{release}

%description -n libevdi-devel
Header and development symlink needed to build applications against libevdi.

%prep
%autosetup -n evdi-%{version}

%build
# Build only library/; module/ belongs to evdi-kmod.spec.
#
# Do NOT pass CFLAGS/LDFLAGS on the make command line: library/Makefile uses
#     CFLAGS := -I../module -std=gnu99 -fPIC ... $(CFLAGS) $(pkg-config ...)
# and a command-line assignment would override that whole line (dropping -fPIC
# and the libdrm includes).  RPM exports the flags in the environment, where
# make picks them up as $(CFLAGS) and the Makefile appends them.  %%set_build_flags
# is what puts them there.
%set_build_flags
%make_build -C library

%install
# LIBDIR is the load-bearing bit: upstream defaults to $(PREFIX)/lib, which on
# x86_64 Fedora is NOT a linker-cache directory.  Force the real multilib path
# so ldconfig indexes it and dlopen("libevdi.so.1") resolves.
%make_install -C library LIBDIR=%{_libdir}

# Upstream ships no header/pkgconfig install target; do it by hand.
install -D -p -m 0644 library/evdi_lib.h %{buildroot}%{_includedir}/evdi_lib.h

mkdir -p %{buildroot}%{_libdir}/pkgconfig
cat > %{buildroot}%{_libdir}/pkgconfig/evdi.pc <<EOF
prefix=%{_prefix}
libdir=%{_libdir}
includedir=%{_includedir}

Name: evdi
Description: Extensible Virtual Display Interface library
Version: %{version}
Libs: -L\${libdir} -levdi
Cflags: -I\${includedir}
EOF

%files -n libevdi
%license library/LICENSE
%doc README.md
%{_libdir}/libevdi.so.1
%{_libdir}/libevdi.so.%{version}

%files -n libevdi-devel
%{_includedir}/evdi_lib.h
%{_libdir}/libevdi.so
%{_libdir}/pkgconfig/evdi.pc

%changelog
* Sun Jul 12 2026 Zenith <zenith@localhost> - 1.15.0-1
- Initial package of the EVDI userspace library (upstream v1.15.0).
