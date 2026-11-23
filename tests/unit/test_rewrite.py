"""Import-time rewriting of 'with fastcircuit:' blocks (syntax only, nothing compiled)."""

from __future__ import annotations

import ast
import textwrap

import pytest

from fastcircuit import UnsupportedSyntax
from fastcircuit._rewrite import RUNTIME, BlockSite, rewrite


def transform(source: str) -> tuple[str, list[BlockSite]]:
    sites: list[BlockSite] = []

    def register(site: BlockSite) -> int:
        sites.append(site)
        return len(sites) - 1

    tree = ast.parse(textwrap.dedent(source))
    result = rewrite(tree, "mod.py", register)
    return ast.unparse(result.tree), sites


def test_block_becomes_runtime_call_with_free_names():
    code, [site] = transform(
        """
        import fastcircuit

        def build(n, theta):
            qc = QuantumCircuit(n)
            with fastcircuit:
                for i in range(n):
                    total = theta * i
                    qc.rx(total, i)
            return qc
        """
    )
    assert f"{RUNTIME}.run(0, {{'n': n, 'qc': qc, 'range': range, 'theta': theta}})" in code
    assert "with" not in code
    assert site.names == ("n", "qc", "range", "theta")  # 'i' and 'total' are block-local
    assert site.receivers == frozenset({"qc"})
    assert site.scope == "build"
    assert site.lineno == 6
    assert not site.debug


@pytest.mark.parametrize(
    ("imports", "marker"),
    [
        ("import fastcircuit as fc", "fc"),
        ("import fastcircuit as fc", "fc.block(debug=True)"),
        ("from fastcircuit import block", "block()"),
        ("from fastcircuit import block as compiled", "compiled(debug=False)"),
    ],
)
def test_aliases(imports, marker):
    code, sites = transform(
        f"""
        {imports}

        def build(qc):
            with {marker}:
                qc.h(0)
        """
    )
    assert len(sites) == 1, code
    assert sites[0].debug == ("debug=True" in marker)


def test_other_with_statements_untouched():
    source = """
        import fastcircuit
        import contextlib

        def build(qc, block):
            with contextlib.suppress(ValueError):
                qc.h(0)
            with block:
                qc.h(0)
            with fastcircuit.other():
                qc.h(0)
        """
    code, sites = transform(source)
    assert sites == []
    assert code == ast.unparse(ast.parse(textwrap.dedent(source)))


def test_module_without_import_is_untouched():
    _, sites = transform("def build(qc):\n    with fastcircuit:\n        qc.h(0)\n")
    assert sites == []


def test_nested_function_and_method_scopes():
    _, sites = transform(
        """
        import fastcircuit

        class Builder:
            def build(self, qc):
                def inner(n):
                    with fastcircuit:
                        qc.h(n)
                inner(0)
        """
    )
    assert sites[0].scope == "Builder.build.<locals>.inner"
    assert sites[0].qualname == "Builder.build.<locals>.inner.<block line 7>"


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ("n = 3\nwith fastcircuit:\n    n = 4\n    qc.h(n)\nqc.x(n)", "'n' is assigned inside"),
        (
            "with fastcircuit:\n    for i in range(3):\n        qc.h(i)\n"
            "for i in range(2):\n    pass",
            "'i' is assigned inside",
        ),
        ("with fastcircuit as fc:\n    qc.h(0)", "'with fastcircuit as ...'"),
        ("with fastcircuit, open('f'):\n    qc.h(0)", "combined with other contexts"),
        ("with fastcircuit.block(True):\n    qc.h(0)", "keyword arguments only"),
        ("with fastcircuit.block(speed=1):\n    qc.h(0)", "unknown option 'speed'"),
        ("with fastcircuit.block(debug=flag):\n    qc.h(0)", "literal True or False"),
        ("with fastcircuit:\n    return qc", "cannot return, yield or await"),
        ("with fastcircuit:\n    global g\n    qc.h(0)", "'global' and 'nonlocal'"),
    ],
)
def test_rejected_at_import(body, fragment):
    source = "import fastcircuit\n\ndef build(qc, flag):\n" + textwrap.indent(body, "    ")
    with pytest.raises(UnsupportedSyntax) as info:
        transform(source)
    assert fragment in str(info.value)
    assert info.value.filename == "mod.py"
    assert info.value.lineno is not None


def test_block_in_class_body_rejected():
    with pytest.raises(UnsupportedSyntax, match="class body"):
        transform("import fastcircuit\n\nclass C:\n    with fastcircuit:\n        pass\n")


def test_nested_block_left_for_the_compiler_to_reject():
    _, sites = transform(
        """
        import fastcircuit

        def build(qc):
            with fastcircuit:
                with fastcircuit:
                    qc.h(0)
        """
    )
    assert len(sites) == 1
    assert isinstance(sites[0].body[0], ast.With)
