# Fetch the signed Windows virtual-display driver payload at package time.
#
# Zenith bundles the ZakoVDD indirect-display driver (Sunshine-Foundation
# lineage, GPL-3.0) plus nefarius' nefcon driver installer so "Headless" and
# "Dual Display" work on a fresh Windows machine with zero manual steps.
# Binaries are attestation-signed release artifacts; we download, we never
# rebuild them here.
#
# Outputs:
#   VDD_DRIVER_AVAILABLE / VDD_WIN10_DRIVER_AVAILABLE / NEFCON_AVAILABLE
#   VDD_DRIVER_DIR / VDD_WIN10_DRIVER_DIR / NEFCON_DRIVER_DIR

include_guard(GLOBAL)

if(NOT WIN32)
    return()
endif()

option(FETCH_DRIVER_DEPS "Download the VDD driver payload from GitHub releases" ON)

set(VDD_DRIVER_VERSION "v0.16.3" CACHE STRING "ZakoVDD release tag (Win11+ payload)")
set(VDD_DRIVER_ASSET_NAME "zakovdd.zip" CACHE STRING "ZakoVDD release asset name")
set(VDD_WIN10_DRIVER_VERSION "v0.14.3-rc1-edid13-test" CACHE STRING "ZakoVDD release tag (Win10 payload)")
set(VDD_WIN10_DRIVER_ASSET_NAME "ZakoVDD-edid13-issue612.zip" CACHE STRING "Win10 ZakoVDD asset name")
set(NEFCON_VERSION "v1.17.40" CACHE STRING "nefcon release tag")

set(_VDD_REPO "qiin2333/zako-vdd")
set(_NEFCON_REPO "nefarius/nefcon")

set(DRIVER_DEPS_CACHE "${CMAKE_BINARY_DIR}/_driver_deps" CACHE PATH "Driver dependency cache")
set(VDD_DRIVER_DIR "${DRIVER_DEPS_CACHE}/vdd")
set(VDD_WIN10_DRIVER_DIR "${DRIVER_DEPS_CACHE}/vdd-win10")
set(NEFCON_DRIVER_DIR "${DRIVER_DEPS_CACHE}/nefcon")

set(VDD_DRIVER_AVAILABLE FALSE)
set(VDD_WIN10_DRIVER_AVAILABLE FALSE)
set(NEFCON_AVAILABLE FALSE)

if(NOT FETCH_DRIVER_DEPS)
    message(STATUS "VDD driver downloads disabled (FETCH_DRIVER_DEPS=OFF)")
    return()
endif()

function(_zenith_fetch_zip url dest_dir marker_glob available_var)
    file(GLOB _existing "${dest_dir}/${marker_glob}")
    if(_existing)
        set(${available_var} TRUE PARENT_SCOPE)
        return()
    endif()

    set(_zip "${dest_dir}.zip")
    file(MAKE_DIRECTORY "${dest_dir}")
    message(STATUS "Downloading ${url}")
    file(DOWNLOAD "${url}" "${_zip}" STATUS _status TIMEOUT 300)
    list(GET _status 0 _code)
    if(NOT _code EQUAL 0)
        list(GET _status 1 _msg)
        message(WARNING "VDD dependency download failed (${_msg}); the installer will ship without this payload")
        return()
    endif()
    file(ARCHIVE_EXTRACT INPUT "${_zip}" DESTINATION "${dest_dir}")

    # Flatten one nesting level if the zip wraps everything in a folder.
    file(GLOB _marker "${dest_dir}/${marker_glob}")
    if(NOT _marker)
        file(GLOB _subdirs LIST_DIRECTORIES true "${dest_dir}/*")
        foreach(_sub IN LISTS _subdirs)
            file(GLOB _nested "${_sub}/${marker_glob}")
            if(_nested)
                file(GLOB _all "${_sub}/*")
                foreach(_f IN LISTS _all)
                    file(COPY "${_f}" DESTINATION "${dest_dir}")
                endforeach()
                break()
            endif()
        endforeach()
    endif()

    file(GLOB _marker "${dest_dir}/${marker_glob}")
    if(_marker)
        set(${available_var} TRUE PARENT_SCOPE)
    else()
        message(WARNING "Downloaded ${url} but found no ${marker_glob} inside")
    endif()
endfunction()

_zenith_fetch_zip(
    "https://github.com/${_VDD_REPO}/releases/download/${VDD_DRIVER_VERSION}/${VDD_DRIVER_ASSET_NAME}"
    "${VDD_DRIVER_DIR}" "ZakoVDD.inf" VDD_DRIVER_AVAILABLE)
_zenith_fetch_zip(
    "https://github.com/${_VDD_REPO}/releases/download/${VDD_WIN10_DRIVER_VERSION}/${VDD_WIN10_DRIVER_ASSET_NAME}"
    "${VDD_WIN10_DRIVER_DIR}" "ZakoVDD.inf" VDD_WIN10_DRIVER_AVAILABLE)
# nefcon ships per-arch subdirectories (arm64/, x64/) — pick x64 explicitly
# so alphabetical globbing can't hand us the ARM binary.
if(NOT EXISTS "${NEFCON_DRIVER_DIR}/nefconw.exe")
    set(_nefcon_zip "${NEFCON_DRIVER_DIR}.zip")
    file(MAKE_DIRECTORY "${NEFCON_DRIVER_DIR}")
    file(DOWNLOAD
        "https://github.com/${_NEFCON_REPO}/releases/download/${NEFCON_VERSION}/nefcon_${NEFCON_VERSION}.zip"
        "${_nefcon_zip}" STATUS _nefcon_status TIMEOUT 300)
    list(GET _nefcon_status 0 _nefcon_code)
    if(_nefcon_code EQUAL 0)
        set(_nefcon_tmp "${NEFCON_DRIVER_DIR}-extract")
        file(ARCHIVE_EXTRACT INPUT "${_nefcon_zip}" DESTINATION "${_nefcon_tmp}")
        file(GLOB_RECURSE _nefcon_exe "${_nefcon_tmp}/*x64/nefconw.exe")
        if(_nefcon_exe)
            list(GET _nefcon_exe 0 _nefcon_exe)
            file(COPY_FILE "${_nefcon_exe}" "${NEFCON_DRIVER_DIR}/nefconw.exe")
        endif()
        file(REMOVE_RECURSE "${_nefcon_tmp}")
    else()
        message(WARNING "nefcon download failed; the installer will ship without one-click VDD install")
    endif()
endif()
if(EXISTS "${NEFCON_DRIVER_DIR}/nefconw.exe")
    set(NEFCON_AVAILABLE TRUE)
endif()

message(STATUS "VDD payloads: latest=${VDD_DRIVER_AVAILABLE} win10=${VDD_WIN10_DRIVER_AVAILABLE} nefcon=${NEFCON_AVAILABLE}")
