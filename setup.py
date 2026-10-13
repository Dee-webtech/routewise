import os

from setuptools import setup
from setuptools.command.build_ext import build_ext


class OptionalBuildExt(build_ext):
    """A failed C++ build must never fail the install."""

    def run(self):
        try:
            super().run()
        except Exception as exc:
            print(f"WARNING: C++ extension not built ({exc}); using pure Python.")

    def build_extension(self, ext):
        try:
            super().build_extension(ext)
        except Exception as exc:
            print(f"WARNING: failed to build {ext.name} ({exc}); using pure Python.")


def get_extensions():
    if os.environ.get("ROUTEWISE_SKIP_CPP"):
        return []
    try:
        from pybind11.setup_helpers import Pybind11Extension
    except ImportError:
        print("WARNING: pybind11 not available; skipping C++ extension.")
        return []
    return [
        Pybind11Extension(
            "routewise._cpp_two_opt",
            ["cpp/two_opt.cpp"],
            cxx_std=17,
            optional=True,
        )
    ]


setup(ext_modules=get_extensions(), cmdclass={"build_ext": OptionalBuildExt})
