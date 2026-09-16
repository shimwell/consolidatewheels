from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from unittest import mock

import pytest

from consolidatewheels import consolidate_linux, wheelsfunc

HERE = os.path.dirname(__file__)
FIXTURE_FILES = {
    "libtwo.whl": os.path.join(
        HERE,
        "files",
        "libtwo-0.0.0-cp310-cp310-manylinux1_x86_64.manylinux_2_5_x86_64.whl",
    )
}


def test_buildlibmap(tmpdir):
    wheeldir = wheelsfunc.unpackwheels([FIXTURE_FILES["libtwo.whl"]], workdir=tmpdir)
    wheeldir = wheeldir[0]

    # Ensure that mapping works in common case
    mapping = consolidate_linux.buildlibmap([wheeldir])
    assert mapping == {
        "libbar.so": "libbar-3fac4b7b.so",
        "libfoo.so": "libfoo-3faccd3s.so",
    }

    # Ensure buildlibmap detects conflicts
    duplicatewheeldir = os.path.join(tmpdir, "anotherwheel")
    shutil.copytree(wheeldir, duplicatewheeldir)
    with pytest.raises(ValueError) as err:
        consolidate_linux.buildlibmap([wheeldir, duplicatewheeldir])
    assert re.search(r"Library lib.+\.so appears multiple times: ", str(err.value))


def test_buildlibmap_versioned_library(tmpdir):
    wheeldir = tmpdir.mkdir("wheel")
    libsdir = wheeldir.mkdir("package.libs")
    libsdir.join("libfoo-3fac4b7b.so.1.2.3").write("")

    mapping = consolidate_linux.buildlibmap([str(wheeldir)])

    assert mapping == {
        "libfoo.so.1.2.3": "libfoo-3fac4b7b.so.1.2.3",
    }


def test_buildlibmap_distinct_versions(tmp_path):
    libsdir = tmp_path / "package.libs"
    libsdir.mkdir()
    for name in ("libfoo-aaaaaaaa.so.0.12", "libfoo-bbbbbbbb.so.0.13"):
        (libsdir / name).touch()

    assert consolidate_linux.buildlibmap([str(tmp_path)]) == {
        "libfoo.so.0.12": "libfoo-aaaaaaaa.so.0.12",
        "libfoo.so.0.13": "libfoo-bbbbbbbb.so.0.13",
    }


def test_buildlibmap_duplicate_version(tmp_path):
    libsdir = tmp_path / "package.libs"
    libsdir.mkdir()
    for name in ("libfoo-aaaaaaaa.so.1.2", "libfoo-bbbbbbbb.so.1.2"):
        (libsdir / name).touch()

    with pytest.raises(ValueError, match=r"Library libfoo\.so\.1\.2 appears"):
        consolidate_linux.buildlibmap([str(tmp_path)])


@pytest.mark.parametrize(
    ("libfilename", "expected"),
    [
        ("libfoo-3fac4b7b.so", "libfoo.so"),
        ("libfoo-3fac4b7b.so.1.2.3", "libfoo.so.1.2.3"),
        ("libfoo.solver-deadbeef.so.1", "libfoo.solver.so.1"),
        ("libfoo.so.helper-deadbeef.so.1", "libfoo.so.helper.so.1"),
        ("libfoo.so.1.2.3", "libfoo.so.1.2.3"),
        ("libopenblas-r0-3fac4b7b.3.29.so", "libopenblas-r0.so"),
    ],
)
def test_demangle_libname(libfilename, expected):
    assert consolidate_linux.demangle_libname(libfilename) == expected


@pytest.mark.parametrize("name", ["libfoo.so.1-gdb.py", "libfoo.so.debug", "libfoo"])
def test_demangle_invalid_libname(name):
    with pytest.raises(ValueError, match="Not a shared library filename"):
        consolidate_linux.demangle_libname(name)


def test_shared_object_discovery(tmp_path):
    names = ["module.cpython-310-x86_64-linux-gnu.so", "libfoo.so.1.2.3"]
    for name in names + ["libfoo.so.1-gdb.py", "libfoo.so.1.debug", "libfoo.source"]:
        (tmp_path / name).touch()
    (tmp_path / "directory.so.1").mkdir()

    assert {
        path.name for path in consolidate_linux._find_shared_objects(str(tmp_path))
    } == set(names)


@pytest.mark.skipif(sys.platform == "win32", reason="Symlinks require privileges")
def test_shared_object_symlinks(tmp_path):
    library = tmp_path / "libfoo-deadbeef.so.1.2"
    library.touch()
    (tmp_path / "libfoo-deadbeef.so.1").symlink_to(library.name)
    (tmp_path / "missing.so.1").symlink_to("missing.so.1.2")

    assert list(consolidate_linux._find_shared_objects(str(tmp_path))) == [library]


def test_patch_wheeldirs(tmpdir):
    wheeldir = wheelsfunc.unpackwheels([FIXTURE_FILES["libtwo.whl"]], workdir=tmpdir)
    wheeldir = wheeldir[0]

    # Create a second wheel without the mangled lib
    duplicatewheeldir = os.path.join(tmpdir, "anotherwheel")
    shutil.copytree(wheeldir, duplicatewheeldir)
    os.rename(
        os.path.join(duplicatewheeldir, "libtwo.libs", "libbar-3fac4b7b.so"),
        os.path.join(duplicatewheeldir, "libtwo.libs", "libotherlib.so"),
    )

    # Ensure that patch_wheels patches all shared objects in provided wheels
    # according to the mangling_map
    with mock.patch("subprocess.call", return_value=0) as mock_call:
        consolidate_linux.patch_wheeldirs(
            [wheeldir, duplicatewheeldir],
            mangling_map={"libbar.so": "libbar-3fac4b7b.so"},
        )
    mock_call.assert_has_calls(
        [
            mock.call(
                [
                    "patchelf",
                    "--replace-needed",
                    "libbar.so",
                    "libbar-3fac4b7b.so",
                    os.path.join(duplicatewheeldir, "libtwo.libs", "libotherlib.so"),
                ]
            ),
            mock.call(
                [
                    "patchelf",
                    "--replace-needed",
                    "libbar.so",
                    "libbar-3fac4b7b.so",
                    os.path.join(
                        duplicatewheeldir,
                        "libtwo",
                        "_libtwo.cpython-310-x86_64-linux-gnu.so",
                    ),
                ]
            ),
            mock.call(
                [
                    "patchelf",
                    "--replace-needed",
                    "libbar.so",
                    "libbar-3fac4b7b.so",
                    os.path.join(
                        wheeldir, "libtwo", "_libtwo.cpython-310-x86_64-linux-gnu.so"
                    ),
                ]
            ),
        ],
        any_order=True,
    )

    # Ensure we trap errors in patching files
    with pytest.raises(RuntimeError) as err:
        with mock.patch("subprocess.call", return_value=1) as mock_call:
            consolidate_linux.patch_wheeldirs(
                [wheeldir, duplicatewheeldir],
                mangling_map={"libbar.so": "libbar-3fac4b7b.so"},
            )
    assert re.compile(
        r"Unable to apply mangling to .+, libbar.so->libbar-3fac4b7b.so"
    ).match(str(err.value))


def test_patch_wheeldirs_versioned_library(tmpdir):
    wheeldir = tmpdir.mkdir("wheel")
    versioned_lib = wheeldir.join("libfoo.so.1.2.3")
    versioned_lib.write("")

    with mock.patch("subprocess.call", return_value=0) as mock_call:
        consolidate_linux.patch_wheeldirs(
            [str(wheeldir)],
            mangling_map={"libfoo.so.1.2.3": "libfoo-3fac4b7b.so.1.2.3"},
        )

    mock_call.assert_called_once_with(
        [
            "patchelf",
            "--replace-needed",
            "libfoo.so.1.2.3",
            "libfoo-3fac4b7b.so.1.2.3",
            str(versioned_lib),
        ]
    )


@pytest.mark.skipif(sys.platform != "linux", reason="Requires Linux ELF tools")
@pytest.mark.parametrize("soname", ["libfoo.so.1.2.3", "libfoo.so.1"])
def test_versioned_elf_dependencies(tmp_path, soname):
    compiler = shutil.which("cc")
    if not compiler or not shutil.which("patchelf"):
        pytest.skip("Requires cc and patchelf")

    libsdir = tmp_path / "provider.libs"
    libsdir.mkdir()
    provider = libsdir / "libfoo-deadbeef.so.1.2.3"
    consumer = tmp_path / "consumer.so.2.0"
    subprocess.run(
        [
            compiler,
            "-shared",
            "-fPIC",
            "-x",
            "c",
            "-",
            "-Wl,-soname," + soname,
            "-o",
            str(provider),
        ],
        input="int foo(void) { return 42; }",
        text=True,
        check=True,
    )
    subprocess.run(
        [
            compiler,
            "-shared",
            "-fPIC",
            "-x",
            "c",
            "-",
            "-x",
            "none",
            str(provider),
            "-o",
            str(consumer),
        ],
        input="extern int foo(void); int use_foo(void) { return foo(); }",
        text=True,
        check=True,
    )
    # Reproduce auditwheel changing the provider SONAME, not the consumer.
    subprocess.run(
        ["patchelf", "--set-soname", provider.name, str(provider)], check=True
    )
    assert (
        soname
        in subprocess.check_output(
            ["patchelf", "--print-needed", str(consumer)], text=True
        ).splitlines()
    )

    mapping = consolidate_linux.buildlibmap([str(tmp_path)])
    consolidate_linux.patch_wheeldirs([str(tmp_path)], mapping)
    needed = subprocess.check_output(
        ["patchelf", "--print-needed", str(consumer)], text=True
    ).splitlines()
    if soname == "libfoo.so.1.2.3":
        assert provider.name in needed
        assert soname not in needed
        # Verify the rewritten dependency can load and execute, not just be inspected.
        subprocess.run(
            [
                sys.executable,
                "-c",
                "import ctypes, sys; ctypes.CDLL(sys.argv[1]); "
                "assert ctypes.CDLL(sys.argv[2]).use_foo() == 42",
                str(provider),
                str(consumer),
            ],
            check=True,
        )
    else:
        assert soname in needed
        assert provider.name not in needed


def test_consolidate(tmpdir):
    # Integration test that actually does the whole workflow.

    with mock.patch(
        "consolidatewheels.consolidate_linux._invoke_patchelf", return_value=0
    ) as mock_call:
        consolidate_linux.consolidate([FIXTURE_FILES["libtwo.whl"]], destdir=tmpdir)
    # Find the workdir directly from the patchelf invokation
    workdir = mock_call.call_args[0][-1].split("libtwo-0.0.0")[0]
    mock_call.assert_has_calls(
        [
            mock.call(
                "libbar.so",
                "libbar-3fac4b7b.so",
                os.path.join(
                    workdir, "libtwo-0.0.0", "libtwo.libs", "libbar-3fac4b7b.so"
                ),
            ),
            mock.call(
                "libbar.so",
                "libbar-3fac4b7b.so",
                os.path.join(
                    workdir,
                    "libtwo-0.0.0",
                    "libtwo",
                    "_libtwo.cpython-310-x86_64-linux-gnu.so",
                ),
            ),
        ],
        any_order=True,
    )
