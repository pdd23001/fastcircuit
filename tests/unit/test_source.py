"""Diagnostics for unsupported code in blocks, and how free names resolve."""

from __future__ import annotations

import math

import pytest

from fastcircuit import UnsupportedSyntax
from fastcircuit._source import MISSING, resolve_name

from support.modules import import_module


def test_diagnostic_contents(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    module = import_module(
        tmp_path,
        """
        def build(n):
            qc = QuantumCircuit(n)
            with fastcircuit:
                for i in range(n):
                    print(i)
                    qc.h(i)
            return qc
        """,
    )
    with pytest.raises(UnsupportedSyntax) as info:
        module.build(2)
    err = info.value
    assert err.function == "build.<block line 9>"
    assert err.filename == module.__file__
    assert err.lineno == 11
    assert err.col == 12  # absolute column in the file
    assert err.construct == "print(i)"
    text = str(err)
    assert "build.<block line 9>" in text and ":11:13" in text and "print(i)" in text
    assert "hint:" in text


def test_nested_function_columns_are_absolute(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    module = import_module(
        tmp_path,
        """
        def outer():
            def build(n):
                qc = QuantumCircuit(n)
                with fastcircuit:
                    qc.h(1.5)
                return qc
            return build
        """,
    )
    with pytest.raises(UnsupportedSyntax) as info:
        module.outer()(2)
    assert info.value.lineno == 11
    assert info.value.col == 17  # the argument '1.5'
    assert info.value.function == "outer.<locals>.build.<block line 10>"


def test_resolve_name_follows_python_scoping():
    angle = 0.5

    def fn():
        return angle, math, len

    assert resolve_name(fn, "angle") == 0.5  # closure
    assert resolve_name(fn, "math") is math  # module global
    assert resolve_name(fn, "len") is len  # builtin
    assert resolve_name(fn, "undefined_name") is MISSING
