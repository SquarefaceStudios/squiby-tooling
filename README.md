# Squiby Tooling

This repository is used to store tool sources and binaries used in [squiby](https://github.com/SquarefaceStudios/squiby)

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
