"""Build and package an independent FFmpeg decoder from its unmodified source."""

import argparse
import hashlib
import os
import platform
import shlex
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path


VERSION = "8.1"
SOURCE_URL = f"https://ffmpeg.org/releases/ffmpeg-{VERSION}.tar.xz"
SOURCE_SHA256 = "b072aed6871998cce9b36e7774033105ca29e33632be5b6347f3206898e0756a"
CONFIGURE_ARGS = [
    "--disable-everything",
    "--disable-autodetect",
    "--disable-network",
    "--disable-doc",
    "--disable-debug",
    "--disable-shared",
    "--enable-static",
    "--enable-pthreads",
    "--enable-ffmpeg",
    "--disable-ffplay",
    "--disable-ffprobe",
    "--enable-avcodec",
    "--enable-avformat",
    "--enable-avfilter",
    "--enable-swscale",
    "--enable-protocol=file,pipe",
    "--enable-demuxer=mov,matroska,avi,ogg,mpegts,mpegvideo,h264,hevc",
    "--enable-decoder=h264,hevc,vp8,vp9,av1,mpeg4,mpeg2video,mjpeg,prores,dnxhd",
    "--enable-parser=h264,hevc,vp8,vp9,av1,mpeg4video,mpegvideo,mjpeg",
    "--enable-filter=scale,format,showinfo",
    "--enable-encoder=rawvideo",
    "--enable-muxer=rawvideo",
    "--enable-bsf=vp9_superframe_split,av1_frame_split",
    "--enable-zlib",
    "--cc=clang",
    "--extra-cflags=-mmacosx-version-min=11.0",
    "--extra-ldflags=-mmacosx-version-min=11.0",
]


def run(command, **kwargs):
    return subprocess.run(command, check=True, **kwargs)


def validate_dependencies(binary):
    output = subprocess.check_output(["/usr/bin/otool", "-L", str(binary)], text=True)
    for line in output.splitlines()[1:]:
        dependency = line.strip().split(" (", 1)[0]
        if not dependency.startswith(("/usr/lib/", "/System/Library/")):
            raise RuntimeError(f"Bundled FFmpeg has an external dependency: {dependency}")


def bundle(app, cache, identity):
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / f"ffmpeg-{VERSION}.tar.xz"
    if not archive.exists():
        temporary = archive.with_suffix(".download")
        try:
            urllib.request.urlretrieve(SOURCE_URL, temporary)
            temporary.replace(archive)
        finally:
            temporary.unlink(missing_ok=True)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise RuntimeError(f"FFmpeg source checksum mismatch: {archive}")

    source = cache / f"ffmpeg-{VERSION}"
    if not (source / "configure").is_file():
        with tarfile.open(archive) as package:
            package.extractall(cache, filter="data")
    build = cache / f"build-{platform.machine()}"
    build.mkdir(exist_ok=True)
    recipe = "\n".join(CONFIGURE_ARGS)
    stamp = build / "video-player-build-options.txt"
    binary = build / "ffmpeg"
    if not binary.is_file() or not stamp.is_file() or stamp.read_text() != recipe:
        if (build / "ffbuild/config.mak").exists():
            run(["make", "distclean"], cwd=build)
        run([str(source.resolve() / "configure"), *CONFIGURE_ARGS], cwd=build)
        run(["make", f"-j{min(8, os.cpu_count() or 1)}", "ffmpeg"], cwd=build)
        stamp.write_text(recipe)

    validate_dependencies(binary)
    destination = app / "Contents/Resources/ffmpeg"
    (destination / "bin").mkdir(parents=True, exist_ok=True)
    packaged = destination / "bin/ffmpeg"
    shutil.copy2(binary, packaged)
    run(["/usr/bin/codesign", "--force", "--sign", identity, str(packaged)])
    run(["/usr/bin/codesign", "--verify", "--strict", str(packaged)])
    run([str(packaged), "-version"], stdout=subprocess.DEVNULL)
    shutil.copy2(archive, destination / archive.name)
    shutil.copy2(source / "COPYING.LGPLv2.1", destination / "COPYING.LGPLv2.1")
    shutil.copy2(source / "LICENSE.md", destination / "LICENSE.md")
    (destination / "BUILD.txt").write_text(
        f"FFmpeg {VERSION}\n"
        "Copyright (c) 2000-2026 the FFmpeg developers.\n"
        "This standalone decoder is distributed under GNU LGPL version 2.1 or later.\n"
        "Video Player invokes it as a separate process.\n"
        "The complete unmodified corresponding source archive and license are included here.\n"
        f"Source URL: {SOURCE_URL}\n"
        f"Source SHA-256: {SOURCE_SHA256}\n\n"
        "To rebuild on macOS with Xcode Command Line Tools installed, extract the source,\n"
        "create a separate build directory and run the following from that directory.\n"
        f"../ffmpeg-{VERSION}/configure {shlex.join(CONFIGURE_ARGS)}\n"
        "make -j8 ffmpeg\n\n"
        "The decoder links only against macOS system libraries. No third-party libraries\n"
        "are compiled into it. GPL and nonfree FFmpeg components are disabled.\n"
    )
    print(f"Bundled standalone FFmpeg {VERSION} in {destination}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("app", type=Path)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--identity", default="-")
    args = parser.parse_args()
    bundle(args.app, args.cache, args.identity)
