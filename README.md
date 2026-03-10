# Squiby Tooling

This repository is used to store tool sources and binaries used in [squiby](https://github.com/SquarefaceStudios/squiby)

## Contents

- [Hooks](#hooks)
  - [Clang format & pre-commit](#clang-format---pre-commit)
- [Tools](#tools)
  - [JSON Schema bundler](#json-schema-bundler)

## Hooks

### Clang format - pre-commit

How to use:
- Build or install clang-format (release page contains linux binary)
- Copy hooks/pre-commit to /path/to/repo/.git/hooks
- Make it executable (chmod +x .git/hooks/pre-commit)

Building clang-format:
```shell
git clone https://github.com/llvm/llvm-project.git
cd llvm
cmake -S llvm -B build -G Ninja -DLLVM_ENABLE_PROJECTS='clang;clang-tools-extra' -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF
ninja -C build clang-format
```

Install or add build/bin into PATH or move the built binaries in another location in PATH

## Tools

### JSON Schema bundler

Used in the https://github.com/SquarefaceStudios/meta repository to generate bundled schemas.

IDEs provide lackluster support for modular JSON schemas. This script merges modular schemas into bundled ones to be used directly.

How is it used to bundle schemas: https://github.com/SquarefaceStudios/meta/blob/379e9d1087d14fcd9f3f976baad73066b589715f/doc/squiby_json_schema/squiby_json_schema.md#step-by-step-breakdown---normal-changes.
