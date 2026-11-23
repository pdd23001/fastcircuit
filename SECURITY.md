# Security policy

## Supported versions

fastcircuit is in alpha. Security fixes go into the latest release only.

## Reporting a vulnerability

Please **do not** open a public issue. Report it privately through GitHub:
**Security → Report a vulnerability** on
[the repository](https://github.com/pdd23001/fastcircuit/security/advisories/new).

Include what you found, how to reproduce it, and the output of
`python -m fastcircuit doctor`. You will get an acknowledgement within a week.
Once a fix is released, the issue is disclosed in a GitHub security advisory,
crediting you unless you prefer otherwise.

## Scope

fastcircuit compiles functions to native code and runs that code in your Python
process. Examples of what we treat as vulnerabilities:

- generated code that reads or writes memory out of bounds for any accepted
  input: argument values, array contents or sizes, qubit indices;
- a way to make fastcircuit load or run cached code that it did not compile for
  that function, for example through the cache directory;
- accepted source that compiles to behavior different from its Python meaning in
  a way that bypasses fastcircuit's checks.

Not in scope: the consequences of compiling code you do not trust. A compiled
function has the same rights as the Python function it replaces. fastcircuit's
checks make results match Python; they are not a sandbox.

fastcircuit makes no network connections and never sends your source code or
compiled code anywhere.
