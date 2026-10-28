from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path

SAFE_BASH_MODES = frozenset({"off", "readonly", "dev"})

_GLOB_CHARS = frozenset("*?[")

_VERSION_COMMANDS: dict[str, frozenset[str]] = {
    "python": frozenset({"--version", "-V"}),
    "python3": frozenset({"--version", "-V"}),
    "node": frozenset({"--version", "-v"}),
    "npm": frozenset({"--version", "-v"}),
    "pip": frozenset({"--version", "-V"}),
    "pip3": frozenset({"--version", "-V"}),
    "git": frozenset({"--version"}),
    "go": frozenset({"version"}),
    "cargo": frozenset({"--version", "-V"}),
    "rustc": frozenset({"--version", "-V"}),
    "java": frozenset({"-version", "--version"}),
    "javac": frozenset({"-version", "--version"}),
    "gcc": frozenset({"--version"}),
    "g++": frozenset({"--version"}),
    "clang": frozenset({"--version"}),
    "make": frozenset({"--version", "-v"}),
    "cmake": frozenset({"--version"}),
}


@dataclass(frozen=True)
class _Spec:
    flags: frozenset[str] = frozenset()
    value_flags: frozenset[str] = frozenset()
    value_path_flags: frozenset[str] = frozenset()
    path_args: bool = True
    max_nonflags: int | None = None


def _has_unsafe_shell(command: str) -> bool:
    quote: str | None = None
    escaped = False
    for char in command:
        if escaped:
            escaped = False
            continue
        if quote == "'":
            if char == "'":
                quote = None
            continue
        if quote == '"':
            if char == "\\":
                escaped = True
            elif char == '"':
                quote = None
            elif char in "$`":
                return True
            continue
        if char == "\\":
            escaped = True
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char in ";&|<>`$()\n\r":
            return True
        if char in _GLOB_CHARS:
            return True
    return False


def _split(command: str) -> list[str] | None:
    if not command or len(command) > 4096:
        return None
    if _has_unsafe_shell(command):
        return None
    try:
        parts = shlex.split(command, posix=True)
    except ValueError:
        return None
    return parts or None


def _within(value: str, cwd: Path) -> bool:
    if value == "-":
        return True
    if not value or value.startswith("~"):
        return False
    try:
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = cwd / candidate
        resolved = candidate.resolve()
    except (OSError, RuntimeError):
        return False
    return resolved == cwd or cwd in resolved.parents


def _match_flag(
    arg: str,
    flags: frozenset[str],
    value_flags: frozenset[str],
) -> tuple[str, str | None, str] | None:
    if arg.startswith("--"):
        name, sep, value = arg.partition("=")
        if name in flags:
            return "flag", None, name
        if name in value_flags:
            return "value", value if sep else None, name
        return None
    if arg in flags:
        return "flag", None, arg
    if arg in value_flags:
        return "value", None, arg
    for flag in value_flags:
        if len(flag) == 2 and arg.startswith(flag) and len(arg) > 2:
            return "value", arg[2:], flag
    if len(arg) > 2 and all("-" + char in flags for char in arg[1:]):
        return "flags", None, ""
    return None


def _parse_args(args: list[str], cwd: Path, spec: _Spec) -> list[str] | None:
    non_flags: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            non_flags.extend(args[index + 1 :])
            break
        if arg.startswith("-") and arg != "-":
            matched = _match_flag(arg, spec.flags, spec.value_flags)
            if matched is None:
                return None
            kind, value, flag = matched
            if kind == "value" and value is None:
                if index + 1 >= len(args):
                    return None
                index += 1
                value = args[index]
            if kind == "value" and flag in spec.value_path_flags:
                if not _within(value or "", cwd):
                    return None
            index += 1
            continue
        non_flags.append(arg)
        index += 1

    if spec.path_args:
        for token in non_flags:
            if not _within(token, cwd):
                return None
    if spec.max_nonflags is not None and len(non_flags) > spec.max_nonflags:
        return None
    return non_flags


def _flag_set(*values: str) -> frozenset[str]:
    return frozenset(values)


_READONLY_SPECS: dict[str, _Spec] = {
    "pwd": _Spec(
        flags=_flag_set("-L", "--logical", "-P", "--physical"),
        path_args=False,
        max_nonflags=0,
    ),
    "ls": _Spec(
        flags=_flag_set(
            "-a", "--all", "-A", "--almost-all", "-b", "--escape",
            "-B", "--ignore-backups", "-c", "-C", "-d", "--directory",
            "-D", "--dired", "-f", "-F", "--classify", "-g", "-G",
            "--no-group", "--group-directories-first", "-h",
            "--human-readable", "-i", "--inode", "-k", "--kibibytes",
            "-l", "-m", "-n", "--numeric-uid-gid", "-N", "--literal",
            "-o", "-p", "-q", "--hide-control-chars", "-Q", "--quote-name",
            "-r", "--reverse", "-R", "--recursive", "-s", "--size",
            "-S", "-t", "-u", "-U", "-v", "-w", "-x", "-X", "-1",
            "-Z", "--context", "--color", "--colour", "--author",
            "--show-control-chars", "--tabsize", "--indicator-style",
            "--quoting-style", "--time", "--time-style", "--sort",
            "--block-size", "--format", "--hide", "--ignore",
            "--full-time", "--help", "--version",
        ),
        value_flags=_flag_set("--tabsize", "--indicator-style", "--time-style",
                              "--sort", "--block-size", "--format"),
        value_path_flags=frozenset(),
    ),
    "cat": _Spec(
        flags=_flag_set(
            "-A", "--show-all", "-b", "--number-nonblank", "-e", "-E",
            "--show-ends", "-n", "--number", "-s", "--squeeze-blank",
            "-t", "-T", "--show-tabs", "-u", "-v", "--show-nonprinting",
            "--help", "--version",
        )
    ),
    "head": _Spec(
        flags=_flag_set(
            "-q", "--quiet", "--silent", "-v", "--verbose", "-z",
            "--zero-terminated", "--help", "--version",
        ),
        value_flags=_flag_set("-n", "--lines", "-c", "--bytes"),
    ),
    "tail": _Spec(
        flags=_flag_set(
            "-f", "--follow", "-F", "--retry", "-q", "--quiet", "--silent",
            "-v", "--verbose", "-z", "--zero-terminated", "--help", "--version",
        ),
        value_flags=_flag_set(
            "-n", "--lines", "-c", "--bytes", "-s", "--sleep-interval",
            "--max-unchanged-stats", "--pid",
        ),
    ),
    "wc": _Spec(
        flags=_flag_set(
            "-c", "--bytes", "-m", "--chars", "-l", "--lines", "-w",
            "--words", "-L", "--max-line-length", "--help", "--version",
        ),
        value_flags=_flag_set("--files0-from"),
        value_path_flags=_flag_set("--files0-from"),
    ),
    "grep": _Spec(
        flags=_flag_set(
            "-E", "--extended-regexp", "-F", "--fixed-strings", "-G",
            "--basic-regexp", "-P", "--perl-regexp", "-i", "--ignore-case",
            "-y", "--no-ignore-case", "-v", "--invert-match", "-w",
            "--word-regexp", "-x", "--line-regexp", "-c", "--count", "-l",
            "--files-with-matches", "-L", "--files-without-match", "-o",
            "--only-matching", "-q", "--quiet", "--silent", "-s",
            "--no-messages", "-b", "--byte-offset", "-H", "--with-filename",
            "-h", "--no-filename", "-n", "--line-number", "-T",
            "--initial-tab", "-Z", "--null", "-z", "--null-data", "-a",
            "--text", "-I", "--ignore-binary", "--line-buffered", "-U",
            "--binary", "-u", "--unix-byte-offsets", "-r", "--recursive",
            "--include", "--exclude", "--exclude-dir", "--directories",
            "--devices", "--color", "--colour", "--help", "--version",
        ),
        value_flags=_flag_set(
            "-e", "--regexp", "-f", "--file", "-m", "--max-count",
            "--label", "--binary-files", "--include", "--exclude",
            "--exclude-from", "--exclude-dir", "--directories", "--devices",
        ),
        value_path_flags=_flag_set("-f", "--file", "--exclude-from"),
    ),
    "rg": _Spec(
        flags=_flag_set(
            "-a", "--text", "-c", "--count", "--count-matches", "-F",
            "--fixed-strings", "-i", "--ignore-case", "-S", "--smart-case",
            "-s", "--case-sensitive", "-v", "--invert-match", "-w",
            "--word-regexp", "-x", "--line-regexp", "-l",
            "--files-with-matches", "--files-without-match", "--files",
            "-n", "--line-number", "-N", "--no-line-number", "-o",
            "--only-matching", "-q", "--quiet", "--no-messages",
            "--no-ignore", "-u", "--unrestricted", "--hidden",
            "--no-hidden", "--max-columns-preview", "--mmap", "--no-mmap",
            "--pcre2", "--no-pcre2", "--stats", "--json", "--heading",
            "--no-heading", "--pretty", "--vimgrep", "--trim",
            "--no-require-git", "--require-git", "--glob-case-insensitive",
            "--no-ignore-vcs", "--no-ignore-global", "--no-ignore-parent",
            "--no-ignore-dot", "--no-ignore-exclude", "--no-ignore-files",
            "--no-ignore-messages", "--debug", "--trace", "--help",
            "--version",
        ),
        value_flags=_flag_set(
            "-A", "--after-context", "-B", "--before-context", "-C",
            "--context", "-e", "--regexp", "-f", "--file", "-g", "--glob",
            "--iglob", "--ignore-file", "--max-depth", "--max-filesize",
            "--max-columns", "--engine", "--encoding", "--threads",
            "--sort", "--sortr", "--color", "--colors",
            "--context-separator", "--field-context-separator",
            "--field-match-separator", "--type", "-t", "-T",
            "--type-not", "--type-add", "--type-clear", "-r", "--replace",
        ),
        value_path_flags=_flag_set("-f", "--file", "--ignore-file"),
    ),
    "file": _Spec(
        flags=_flag_set(
            "-b", "--brief", "-i", "--mime", "-N", "--no-pad", "-z",
            "--uncompress", "-Z", "--uncompress-noreport", "-0",
            "--print0", "-r", "--raw", "-s", "--special-files", "-c",
            "--checking-printout", "-k", "--keep-going", "-d", "--debug",
            "-h", "--no-dereference", "--mime-type", "--mime-encoding",
            "--apple", "--version", "--help",
        ),
        value_flags=_flag_set(
            "-m", "--magic-file", "-f", "--files-from", "-e", "--exclude",
            "-F", "--separator", "-P", "--parameter", "-E", "--extension",
        ),
        value_path_flags=_flag_set("-m", "--magic-file", "-f", "--files-from"),
    ),
    "stat": _Spec(
        flags=_flag_set(
            "-f", "--file-system", "-t", "--terse", "--help", "--version",
        ),
        value_flags=_flag_set(
            "-c", "--format", "--printf", "--cached",
        ),
    ),
    "du": _Spec(
        flags=_flag_set(
            "-a", "--all", "-b", "--bytes", "-c", "--total", "-h",
            "--human-readable", "-k", "-m", "-s", "--summarize", "-x",
            "--one-file-system", "-P", "--no-dereference", "-l",
            "--count-links", "--apparent-size", "--si", "-0", "--null",
            "--help", "--version",
        ),
        value_flags=_flag_set(
            "-d", "--max-depth", "-t", "--threshold", "--time-style",
            "--exclude", "-X", "--exclude-from", "--block-size", "-B",
            "--time",
        ),
        value_path_flags=_flag_set("-X", "--exclude-from"),
    ),
    "df": _Spec(
        flags=_flag_set(
            "-a", "--all", "-h", "--human-readable", "-H", "--si", "-i",
            "--inodes", "-k", "-l", "--local", "-P", "--portability", "-T",
            "--print-type", "--total", "--no-sync", "--help", "--version",
        ),
        value_flags=_flag_set(
            "-B", "--block-size", "-t", "--type", "-x", "--exclude-type",
            "--output",
        ),
    ),
    "diff": _Spec(
        flags=_flag_set(
            "-r", "--recursive", "-q", "--quiet", "-s", "--no-patch", "-p",
            "--patch", "-u", "--unified", "-c", "-C", "-N", "--new-file",
            "--unidirectional-new-file", "-w", "--ignore-all-space", "-b",
            "--ignore-space-change", "-B", "--ignore-blank-lines", "-i",
            "--ignore-case", "-E", "--ignore-tab-expansion", "-Z",
            "--ignore-trailing-space", "--ignore-space-at-eol", "-a",
            "--text", "-t", "--expand-tabs", "--initial-tab",
            "--suppress-common-lines", "-y", "--side-by-side",
            "--left-column", "--suppress-blank-empty", "--minimal",
            "--speed-large-files", "--strip-trailing-cr", "--check",
            "--brief", "--report-identical-files", "--ignore-matching-lines",
            "--color", "--no-color", "--palette", "--help", "--version",
        ),
        value_flags=_flag_set(
            "-U", "--unified", "-C", "--context", "-W", "--width",
            "--tabsize", "--label", "-I", "--ignore-matching-lines",
            "--exclude", "-x", "--exclude-from", "-X", "--starting-file",
            "-S", "--horizon-lines", "--from-file", "--to-file",
            "--color", "--palette",
        ),
        value_path_flags=_flag_set(
            "--from-file", "--to-file", "--exclude-from", "-X",
            "--starting-file", "-S",
        ),
    ),
    "cmp": _Spec(
        flags=_flag_set(
            "-l", "--verbose", "-s", "--silent", "--quiet", "-b",
            "--print-bytes", "--help", "--version",
        ),
        value_flags=_flag_set(
            "-n", "--bytes", "-i", "--ignore-initial", "--ignore-initial",
        ),
    ),
    "cut": _Spec(
        flags=_flag_set(
            "-s", "--only-delimited", "-n", "--complement", "-z",
            "--zero-terminated", "--help", "--version",
        ),
        value_flags=_flag_set(
            "-b", "--bytes", "-c", "--characters", "-f", "--fields",
            "-d", "--delimiter", "--output-delimiter",
        ),
    ),
    "sort": _Spec(
        flags=_flag_set(
            "-b", "--ignore-leading-blanks", "-d", "--dictionary-order",
            "-f", "--ignore-case", "-g", "--general-numeric-sort", "-h",
            "--human-numeric-sort", "-i", "--ignore-nonprinting", "-M",
            "--month-sort", "-n", "--numeric-sort", "-r", "--reverse",
            "-R", "--random-sort", "-V", "--version-sort", "-c", "--check",
            "-C", "--check=quiet", "-m", "--merge", "-s", "--stable", "-u",
            "--unique", "-z", "--zero-terminated", "--debug", "--help",
            "--version",
        ),
        value_flags=_flag_set(
            "-k", "--key", "-t", "--field-separator", "-S", "--buffer-size",
            "--files0-from", "--random-source", "--batch-size",
            "--parallel", "--sort", "--temporary-directory", "-T",
        ),
        value_path_flags=_flag_set(
            "--files0-from", "--random-source", "--temporary-directory", "-T",
        ),
    ),
    "uniq": _Spec(
        flags=_flag_set(
            "-c", "--count", "-d", "--repeated", "-D", "--all-repeated",
            "-u", "--unique", "-i", "--ignore-case", "-z",
            "--zero-terminated", "--group", "--help", "--version",
        ),
        value_flags=_flag_set(
            "-f", "--skip-fields", "-s", "--skip-chars", "-w",
            "--check-chars",
        ),
        max_nonflags=1,
    ),
    "echo": _Spec(
        flags=_flag_set("-n", "-e", "-E", "--help", "--version"),
        path_args=False,
    ),
    "printf": _Spec(
        flags=_flag_set("--help", "--version"),
        path_args=False,
    ),
    "date": _Spec(
        flags=_flag_set(
            "-u", "--utc", "--universal", "-R", "--rfc-email",
            "--rfc-2822", "--rfc-3339", "--iso-8601", "--debug",
            "--help", "--version",
        ),
        value_flags=_flag_set(
            "-d", "--date", "-f", "--file", "-r", "--reference",
            "--rfc-3339", "--iso-8601",
        ),
        value_path_flags=_flag_set("-f", "--file", "-r", "--reference"),
    ),
    "uname": _Spec(
        flags=_flag_set(
            "-a", "--all", "-s", "--kernel-name", "-n", "--nodename",
            "-r", "--kernel-release", "-v", "--kernel-version", "-m",
            "--machine", "-p", "--processor", "-i", "--hardware-platform",
            "-o", "--operating-system", "--help", "--version",
        ),
        path_args=False,
        max_nonflags=0,
    ),
    "whoami": _Spec(path_args=False, max_nonflags=0),
    "id": _Spec(
        flags=_flag_set(
            "-u", "--user", "-g", "--group", "-G", "--groups", "-n",
            "--name", "-r", "--real", "-Z", "--context", "-a",
            "--help", "--version",
        ),
        max_nonflags=1,
    ),
    "which": _Spec(
        flags=_flag_set(
            "-a", "--all", "-s", "--skip-dot", "--skip-tilde",
            "--show-dot", "--show-tilde", "--tty-only", "--read-alias",
            "--read-functions", "--show-functions", "--version", "--help",
        ),
        max_nonflags=None,
    ),
    "hostname": _Spec(
        flags=_flag_set(
            "-s", "--short", "-f", "--fqdn", "-d", "--domain", "-i",
            "--ip-address", "-I", "--all-ip-addresses", "-A",
            "--all-fqdns", "--help", "--version",
        ),
        path_args=False,
        max_nonflags=0,
    ),
    "tree": _Spec(
        flags=_flag_set(
            "-a", "-d", "-f", "-h", "-i", "-n", "-p", "-u", "-g",
            "-s", "-D", "-C", "-F", "-r", "-t", "-v", "-x", "-J", "-X",
            "-q", "-N", "-Q", "-A", "-S", "--noreport", "--dirsfirst",
            "--prune", "--ignore-case", "--matchdirs",
            "--gitignore", "--info", "--help", "--version",
        ),
        value_flags=_flag_set("-L", "-P", "-I", "--ignore", "--gitfile"),
        value_path_flags=_flag_set("--gitfile"),
    ),
    "tr": _Spec(
        flags=_flag_set(
            "-c", "-C", "--complement", "-d", "--delete", "-s",
            "--squeeze-repeats", "-t", "--truncate-set1", "--help",
            "--version",
        ),
        path_args=False,
    ),
    "sha256sum": _Spec(
        flags=_flag_set(
            "-b", "-t", "--text", "--binary", "--tag", "--ignore-missing",
            "--quiet", "--status", "--warn", "--strict", "-z", "--zero",
            "--help", "--version",
        ),
        value_flags=_flag_set("-c", "--check"),
        value_path_flags=_flag_set("-c", "--check"),
    ),
    "sha1sum": _Spec(
        flags=_flag_set(
            "-b", "-t", "--text", "--binary", "--tag", "--ignore-missing",
            "--quiet", "--status", "--warn", "--strict", "-z", "--zero",
            "--help", "--version",
        ),
        value_flags=_flag_set("-c", "--check"),
        value_path_flags=_flag_set("-c", "--check"),
    ),
    "md5sum": _Spec(
        flags=_flag_set(
            "-b", "-t", "--text", "--binary", "--tag", "--ignore-missing",
            "--quiet", "--status", "--warn", "--strict", "-z", "--zero",
            "--help", "--version",
        ),
        value_flags=_flag_set("-c", "--check"),
        value_path_flags=_flag_set("-c", "--check"),
    ),
    "b2sum": _Spec(
        flags=_flag_set(
            "-b", "-t", "--text", "--binary", "--tag", "--ignore-missing",
            "--quiet", "--status", "--warn", "--strict", "-z", "--zero",
            "--help", "--version",
        ),
        value_flags=_flag_set("-c", "--check"),
        value_path_flags=_flag_set("-c", "--check"),
    ),
}


def is_safe_command(command: str, cwd: Path, *, allow_dev: bool = False) -> bool:
    parts = _split(command)
    if parts is None:
        return False
    cwd = cwd.resolve()
    name, args = parts[0], parts[1:]

    version_flags = _VERSION_COMMANDS.get(name)
    if version_flags is not None and len(args) == 1 and args[0] in version_flags:
        return True

    if name == "find":
        return _validate_find(args, cwd)
    if name == "git":
        return _validate_git(args, cwd)
    if name in {"grep", "rg"}:
        return _validate_grep_like(
            args,
            cwd,
            _READONLY_SPECS[name],
            files_mode=name == "rg",
        )

    spec = _READONLY_SPECS.get(name)
    if spec is not None:
        return _parse_args(args, cwd, spec) is not None

    if allow_dev and _validate_dev(name, args, cwd):
        return True
    return False


_FIND_DANGEROUS = frozenset(
    {
        "-exec",
        "-execdir",
        "-ok",
        "-okdir",
        "-delete",
        "-fprint",
        "-fprint0",
        "-fprintf",
        "-fls",
        "-L",
        "-H",
        "-follow",
        "--follow",
    }
)
_FIND_PATH_PRIMARIES = frozenset(
    {"-newer", "-anewer", "-cnewer", "-samefile", "-files0-from"}
)


def _validate_find(args: list[str], cwd: Path) -> bool:
    leading: list[str] = []
    index = 0
    while index < len(args):
        token = args[index]
        if token.startswith("-") or token in {"(", "!", ","}:
            break
        leading.append(token)
        index += 1
    for token in leading:
        if not _within(token, cwd):
            return False

    while index < len(args):
        token = args[index]
        if token in _FIND_DANGEROUS:
            return False
        if token.startswith(("-fprint", "-fprintf", "-fls")):
            return False
        if token in _FIND_PATH_PRIMARIES:
            if index + 1 >= len(args) or not _within(args[index + 1], cwd):
                return False
            index += 2
            continue
        index += 1
    return True


def _used_value_flag(args: list[str], names: set[str]) -> bool:
    for arg in args:
        if arg in names:
            return True
        if arg.startswith("--"):
            if arg.partition("=")[0] in names:
                return True
            continue
        for name in names:
            if len(name) == 2 and arg.startswith(name) and len(arg) > 2:
                return True
    return False


def _validate_grep_like(
    args: list[str],
    cwd: Path,
    spec: _Spec,
    *,
    files_mode: bool,
) -> bool:
    no_paths = _Spec(
        flags=spec.flags,
        value_flags=spec.value_flags,
        value_path_flags=spec.value_path_flags,
        path_args=False,
    )
    non_flags = _parse_args(args, cwd, no_paths)
    if non_flags is None:
        return False
    has_pattern_flag = _used_value_flag(args, {"-e", "--regexp", "-f", "--file"})
    if has_pattern_flag or (files_mode and "--files" in args):
        return all(_within(token, cwd) for token in non_flags)
    if not non_flags:
        return True
    return all(_within(token, cwd) for token in non_flags[1:])


_DEV_SCRIPTS = frozenset(
    {"test", "build", "lint", "check", "typecheck", "format:check", "format-check"}
)
_DEV_MAKE_TARGETS = frozenset({"test", "check", "build", "lint"})


def _validate_dev(name: str, args: list[str], cwd: Path) -> bool:
    if name in {"pytest", "mypy"}:
        return _dev_args(args, cwd)
    if name in {"python", "python3"}:
        return (
            len(args) >= 2
            and args[0] == "-m"
            and args[1] in {"pytest", "unittest"}
            and _dev_args(args[2:], cwd)
        )
    if name in {"npm", "yarn", "pnpm"}:
        return _validate_package_manager(name, args, cwd)
    if name == "cargo":
        if not args:
            return False
        if args[0] in {"test", "build", "check", "clippy"}:
            return _dev_args(args[1:], cwd)
        if args[0] == "fmt":
            return "--check" in args[1:] and _dev_args(args[1:], cwd)
        return False
    if name == "go":
        return bool(args) and args[0] in {"test", "build", "vet"} and _dev_args(args[1:], cwd)
    if name == "make":
        return _validate_make(args, cwd)
    if name in {"mvn", "gradle"}:
        phases = {"test", "package", "verify"} if name == "mvn" else {"test", "build", "check"}
        non_flags = [arg for arg in args if not arg.startswith("-")]
        return bool(non_flags) and non_flags[0] in phases and _dev_args(args, cwd)
    if name == "dotnet":
        non_flags = [arg for arg in args if not arg.startswith("-")]
        return bool(non_flags) and non_flags[0] in {"test", "build"} and _dev_args(args, cwd)
    if name == "ruff":
        non_flags = [arg for arg in args if not arg.startswith("-")]
        if not non_flags or non_flags[0] not in {"check", "format"}:
            return False
        if non_flags[0] == "format" and "--check" not in args:
            return False
        return _dev_args(args, cwd)
    if name == "eslint":
        return "--fix" not in args and _dev_args(args, cwd)
    if name == "tsc":
        return "--noEmit" in args and _dev_args(args, cwd)
    return False


def _validate_package_manager(name: str, args: list[str], cwd: Path) -> bool:
    if not args:
        return False
    if args[0] == "test":
        return _dev_args(args[1:], cwd)
    if name in {"yarn", "pnpm"} and args[0] in _DEV_SCRIPTS:
        return _dev_args(args[1:], cwd)
    if args[0] in {"run", "run-script"}:
        if len(args) < 2 or args[1] not in _DEV_SCRIPTS:
            return False
        return _dev_args(args[2:], cwd)
    return False


def _validate_make(args: list[str], cwd: Path) -> bool:
    targets: list[str] = []
    index = 0
    path_value_flags = {
        "-C", "--directory", "-f", "--file", "-I", "--include-dir",
    }
    value_flags = path_value_flags | {
        "-j", "--jobs", "-l", "--load-average", "-O", "--output-sync",
        "-o", "--old-file", "-W", "--what-if", "--new-file",
        "--assume-old", "--assume-new", "--debug", "--shuffle",
    }
    while index < len(args):
        arg = args[index]
        if arg in {"-t", "--touch", "--eval"} or arg.startswith(("-E", "--eval=")):
            return False
        if arg.startswith("--") and "=" in arg:
            flag, value = arg.split("=", 1)
            if flag in path_value_flags and not _within(value, cwd):
                return False
            index += 1
            continue
        if arg in value_flags:
            if index + 1 >= len(args):
                return False
            value = args[index + 1]
            if arg in path_value_flags and not _within(value, cwd):
                return False
            index += 2
            continue
        if len(arg) > 2 and arg[:2] in value_flags:
            if arg[:2] in path_value_flags and not _within(arg[2:], cwd):
                return False
            index += 1
            continue
        if arg.startswith("-"):
            index += 1
            continue
        targets.append(arg)
        index += 1
    return bool(targets) and all(target in _DEV_MAKE_TARGETS for target in targets)


def _dev_args(args: list[str], cwd: Path) -> bool:
    for token in args:
        if token.startswith("-"):
            if "=" in token:
                value = token.split("=", 1)[1]
                if not value or value.startswith(("~", "/")):
                    return False
                if ".." in Path(value).parts:
                    return False
            continue
        if not _within(token, cwd):
            return False
    return True


_GIT_STATUS_FLAGS = _flag_set(
    "-s", "--short", "-b", "--branch", "--porcelain", "-v", "--verbose",
    "--show-stash", "--ahead-behind", "--no-ahead-behind", "--renames",
    "--no-renames", "--find-renames", "--column", "--no-column", "--long",
    "-z", "--null", "--no-optional-locks", "--help",
)
_GIT_STATUS_VALUE_FLAGS = _flag_set(
    "-u", "--untracked-files", "--ignored", "--ignore-submodules",
    "--find-renames", "--column", "--porcelain",
)

_GIT_DIFF_FLAGS = _flag_set(
    "-p", "--patch", "-u", "--no-patch", "-s", "-r", "--recursive",
    "-q", "--quiet", "--stat", "--shortstat", "--numstat", "--dirstat",
    "--cumulative", "--summary", "--name-only", "--name-status",
    "--color", "--no-color", "--word-diff", "--color-words",
    "--no-renames", "--find-renames", "--find-copies",
    "--find-copies-harder", "--pickaxe-all", "--pickaxe-regex", "-w",
    "--ignore-all-space", "-b", "--ignore-space-change",
    "--ignore-blank-lines", "-B", "--ignore-cr-at-eol",
    "--ignore-space-at-eol", "--check", "--full-index", "--binary",
    "--text", "-a", "--no-index", "--no-prefix", "--no-relative",
    "--relative", "--ita-invisible-in-index",
    "--compact-summary", "--no-compact-summary", "--function-context",
    "-W", "--no-textconv", "--no-ext-diff", "--exit-code", "--raw",
    "--patch-with-raw", "--patch-with-stat", "--cc",
    "--combined-all-paths", "--color-moved", "--patience", "--histogram",
    "--minimal", "--indent-heuristic", "--no-indent-heuristic",
    "--ignore-tab-expansion", "--ignore-trailing-space",
    "--ignore-case", "--break-rewrites", "--help",
)
_GIT_DIFF_VALUE_FLAGS = _flag_set(
    "-U", "--unified", "--diff-filter", "-S", "-G",
    "--ignore-matching-lines", "-I", "--ws-error-highlight", "--abbrev",
    "--src-prefix", "--dst-prefix", "--line-prefix",
    "--output-indicator-new", "--output-indicator-old",
    "--output-indicator-context", "--stat-width", "--stat-name-width",
    "--stat-count", "--stat-graph-width", "--stat-density",
    "--inter-hunk-context", "--word-diff-regex", "--color-moved-ws",
    "--diff-algorithm", "--anchored", "--ignore-submodules",
)

_GIT_LOG_FLAGS = _flag_set(
    "--oneline", "--graph", "--decorate", "--no-decorate",
    "--abbrev-commit", "--no-abbrev-commit", "--all-match",
    "--invert-grep", "-i", "--regexp-ignore-case", "-E",
    "--extended-regexp", "-F", "--fixed-strings", "-P",
    "--perl-regexp", "--basic-regexp", "--merges", "--no-merges",
    "--first-parent", "--ancestry-path", "--reverse", "--walk-reflogs",
    "-g", "--all", "--branches", "--tags", "--remotes", "--parents",
    "--children", "--left-right", "--cherry-mark", "--cherry-pick",
    "--cherry", "--boundary", "--simplify-by-decoration",
    "--full-history", "--simplify-merges", "--dense", "--sparse",
    "--topo-order", "--date-order", "--author-date-order",
    "--no-show-signature", "--stat", "--shortstat", "--numstat",
    "--name-only", "--name-status", "--summary", "--patch", "-p",
    "--no-patch", "-s", "--color", "--no-color", "--notes",
    "--no-notes", "--standard-notes", "--no-standard-notes", "--source",
    "--use-mailmap", "--no-use-mailmap", "--cc", "--combined-all-paths",
    "--no-diff-merges", "--remerge-diff", "--no-remerge-diff",
    "--word-diff", "--color-words", "--no-renames", "--find-renames",
    "--find-copies", "--find-copies-harder", "--pickaxe-all",
    "--pickaxe-regex", "-w", "--ignore-all-space", "-b",
    "--ignore-space-change", "--ignore-blank-lines", "-B",
    "--ignore-cr-at-eol", "--ignore-space-at-eol", "--check",
    "--full-index", "--binary", "--text", "-a", "--no-textconv",
    "--no-ext-diff", "--relative", "--no-relative",
    "--ita-invisible-in-index", "--compact-summary",
    "--no-compact-summary", "--function-context", "-W",
    "--no-walk", "--do-walk", "--show-pulls", "--no-show-pulls",
    "--full-diff", "--log-size", "--no-expand-tabs", "--help",
)
_GIT_LOG_VALUE_FLAGS = _flag_set(
    "-n", "--max-count", "--skip", "--since", "--after", "--until",
    "--before", "--author", "--committer", "--grep", "--min-parents",
    "--max-parents", "--glob", "--exclude", "--date", "--pretty",
    "--format", "--diff-merges", "--word-diff-regex", "--diff-filter",
    "-S", "-G", "--ignore-matching-lines", "-I", "--ws-error-highlight",
    "--abbrev", "--src-prefix", "--dst-prefix", "--line-prefix",
    "--inter-hunk-context", "--stat-width", "--stat-name-width",
    "--stat-count", "--stat-graph-width", "--stat-density",
    "--decorate-refs", "--decorate-refs-exclude", "--show-notes",
    "--mailmap-file", "--encoding", "--expand-tabs",
    "--show-linear-break", "--color-moved-ws", "--diff-algorithm",
    "--anchored", "--ignore-submodules",
)
_GIT_LOG_VALUE_PATH_FLAGS = _flag_set("--mailmap-file")

_GIT_REV_PARSE_FLAGS = _flag_set(
    "--verify", "--quiet", "--short", "--abbrev-ref", "--symbolic",
    "--symbolic-full-name", "--all", "--branches", "--tags", "--remotes",
    "--show-toplevel", "--show-prefix", "--show-cdup",
    "--show-superproject-working-tree", "--git-dir", "--absolute-git-dir",
    "--is-inside-work-tree", "--is-inside-git-dir", "--is-bare-repository",
    "--is-shallow-repository", "--show-object-format", "--local-env-vars",
    "--help",
)
_GIT_REV_PARSE_VALUE_FLAGS = _flag_set(
    "--glob", "--exclude", "--path-format", "--short", "--abbrev-ref",
    "--branches", "--tags", "--remotes",
)

_GIT_LS_FILES_FLAGS = _flag_set(
    "-c", "--cached", "-d", "--deleted", "-m", "--modified", "-o",
    "--others", "-i", "--ignored", "-s", "--stage", "-u", "--unmerged",
    "-k", "--killed", "-z", "--null", "--directory",
    "--no-empty-directory", "--error-unmatch", "--recurse-submodules",
    "--full-name", "--exclude-standard", "--eol", "--debug",
    "--deduplicate", "--sparse", "--help",
)
_GIT_LS_FILES_VALUE_FLAGS = _flag_set(
    "--with-tree", "--exclude", "--exclude-from", "--exclude-per-directory",
    "--format", "--abbrev",
)
_GIT_LS_FILES_VALUE_PATH_FLAGS = _flag_set("--exclude-from")

_GIT_BLAME_FLAGS = _flag_set(
    "-l", "-t", "-s", "-e", "-w", "-M", "-C", "-f", "-n", "-b", "-p",
    "--porcelain", "--line-porcelain", "--incremental", "--root",
    "--show-stats", "--show-name", "--show-number", "--show-email",
    "--score-debug", "--help",
)
_GIT_BLAME_VALUE_FLAGS = _flag_set(
    "-L", "--date", "--ignore-rev", "--ignore-revs-file", "--contents",
    "--reverse", "--abbrev", "--encoding",
)
_GIT_BLAME_VALUE_PATH_FLAGS = _flag_set(
    "--ignore-revs-file", "--contents",
)

_GIT_DESCRIBE_FLAGS = _flag_set(
    "--all", "--tags", "--long", "--always", "--contains", "--exact-match",
    "--first-parent", "--debug", "--help",
)
_GIT_DESCRIBE_VALUE_FLAGS = _flag_set(
    "--abbrev", "--match", "--exclude", "--candidates", "--commit",
)

_GIT_CAT_FILE_FLAGS = _flag_set(
    "-t", "-s", "-e", "-p", "-a", "--batch", "--batch-check",
    "--batch-all-objects", "--buffer", "--unordered",
    "--allow-unknown-type", "--help",
)
_GIT_CAT_FILE_VALUE_FLAGS = frozenset()

_GIT_GREP_FLAGS = _flag_set(
    "--cached", "--no-index", "--untracked", "--exclude-standard",
    "--recurse-submodules", "--color", "--no-color", "--break",
    "--heading", "--full-name", "-n", "--line-number", "-l",
    "--files-with-matches", "-L", "--files-without-match", "-c",
    "--count", "-h", "--no-filename", "-H", "--with-filename", "-i",
    "--ignore-case", "-w", "--word-regexp", "-v", "--invert-match",
    "-F", "--fixed-strings", "-E", "--extended-regexp", "-G",
    "--basic-regexp", "-P", "--perl-regexp", "--and", "--or", "--not",
    "--all-match", "-o", "--only-matching", "-q", "--quiet",
    "-W", "--function-context", "-p", "--show-function", "-z", "--null",
    "--column", "-a", "--text", "--no-textconv", "--help",
)
_GIT_GREP_VALUE_FLAGS = _flag_set(
    "-e", "--regexp", "-f", "--file", "--max-count", "-A",
    "--after-context", "-B", "--before-context", "-C", "--context",
    "--max-depth", "--threads", "--exclude", "--exclude-from",
    "--exclude-per-directory",
)
_GIT_GREP_VALUE_PATH_FLAGS = _flag_set("-f", "--file", "--exclude-from")

_GIT_SHORTLOG_FLAGS = _flag_set(
    "-s", "--summary", "-n", "--numbered", "-e", "--email", "-c",
    "--committer", "--no-merges", "--help",
)
_GIT_SHORTLOG_VALUE_FLAGS = _flag_set("--group", "--format", "--author")

_GIT_BRANCH_FLAGS = _flag_set(
    "-a", "--all", "-r", "--remotes", "-v", "--verbose", "--list", "-l",
    "--contains", "--no-contains", "--merged", "--no-merged",
    "--points-at", "--show-current", "--omit-empty", "--ignore-case",
    "--no-abbrev", "--no-column", "--format", "--sort", "--column",
    "--abbrev", "--color", "--no-color", "--help",
)
_GIT_BRANCH_VALUE_FLAGS = _flag_set(
    "--format", "--sort", "--column", "--abbrev", "--contains",
    "--no-contains", "--points-at",
)
_GIT_BRANCH_LISTING_FLAGS = _flag_set(
    "--list", "-l", "--contains", "--no-contains", "--merged",
    "--no-merged", "--points-at",
)

_GIT_SPECS: dict[str, _Spec] = {
    "status": _Spec(
        flags=_GIT_STATUS_FLAGS,
        value_flags=_GIT_STATUS_VALUE_FLAGS,
    ),
    "diff": _Spec(
        flags=_GIT_DIFF_FLAGS,
        value_flags=_GIT_DIFF_VALUE_FLAGS,
    ),
    "log": _Spec(
        flags=_GIT_LOG_FLAGS,
        value_flags=_GIT_LOG_VALUE_FLAGS,
        value_path_flags=_GIT_LOG_VALUE_PATH_FLAGS,
    ),
    "show": _Spec(
        flags=_GIT_LOG_FLAGS,
        value_flags=_GIT_LOG_VALUE_FLAGS,
        value_path_flags=_GIT_LOG_VALUE_PATH_FLAGS,
    ),
    "rev-parse": _Spec(
        flags=_GIT_REV_PARSE_FLAGS,
        value_flags=_GIT_REV_PARSE_VALUE_FLAGS,
    ),
    "ls-files": _Spec(
        flags=_GIT_LS_FILES_FLAGS,
        value_flags=_GIT_LS_FILES_VALUE_FLAGS,
        value_path_flags=_GIT_LS_FILES_VALUE_PATH_FLAGS,
    ),
    "blame": _Spec(
        flags=_GIT_BLAME_FLAGS,
        value_flags=_GIT_BLAME_VALUE_FLAGS,
        value_path_flags=_GIT_BLAME_VALUE_PATH_FLAGS,
    ),
    "describe": _Spec(
        flags=_GIT_DESCRIBE_FLAGS,
        value_flags=_GIT_DESCRIBE_VALUE_FLAGS,
    ),
    "cat-file": _Spec(
        flags=_GIT_CAT_FILE_FLAGS,
        value_flags=_GIT_CAT_FILE_VALUE_FLAGS,
    ),
    "grep": _Spec(
        flags=_GIT_GREP_FLAGS,
        value_flags=_GIT_GREP_VALUE_FLAGS,
        value_path_flags=_GIT_GREP_VALUE_PATH_FLAGS,
    ),
    "shortlog": _Spec(
        flags=_GIT_SHORTLOG_FLAGS,
        value_flags=_GIT_SHORTLOG_VALUE_FLAGS,
    ),
    "version": _Spec(
        flags=_flag_set("--build-options", "--help"),
        path_args=False,
        max_nonflags=0,
    ),
}


def _git_config_unsafe(cwd: Path) -> bool:
    for directory in (cwd, *cwd.parents):
        git_path = directory / ".git"
        if git_path.is_symlink():
            return True
        if git_path.is_file():
            return True
        if not git_path.is_dir():
            continue

        config = git_path / "config"
        if not config.exists():
            return False
        if config.is_symlink() or not config.is_file():
            return True
        try:
            lines = config.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return True

        section = ""
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith(("#", ";")):
                continue
            if line.startswith("["):
                section = line[1:].split("]", 1)[0].strip().lower()
                if section == "include" or section.startswith("includeif"):
                    return True
                continue
            key = line.partition("=")[0].strip().lower()
            if section == "core" and key in {"fsmonitor", "hookspath"}:
                return True
            if section == "diff" and key == "external":
                return True
            if section.startswith('diff "') and key in {"command", "textconv"}:
                return True
            if section.startswith('filter "') and key in {"clean", "smudge", "process"}:
                return True
            if section == "gpg" and key == "program":
                return True
        return False
    return False


def _git_format_unsafe(args: list[str]) -> bool:
    for index, arg in enumerate(args):
        if arg in {"--format", "--pretty"}:
            if index + 1 < len(args) and "%G" in args[index + 1]:
                return True
        elif arg.startswith(("--format=", "--pretty=")) and "%G" in arg:
            return True
    return False


def _validate_git(args: list[str], cwd: Path) -> bool:
    if not args:
        return False
    while args and args[0].startswith("-"):
        if args[0] == "--no-pager":
            args = args[1:]
            continue
        if args[0] == "--version" and len(args) == 1:
            return True
        return False
    if not args:
        return False

    if _git_config_unsafe(cwd):
        return False

    subcommand, rest = args[0], args[1:]
    if subcommand == "branch":
        non_flags = _parse_args(
            rest,
            cwd,
            _Spec(flags=_GIT_BRANCH_FLAGS, value_flags=_GIT_BRANCH_VALUE_FLAGS),
        )
        if non_flags is None:
            return False
        listed = bool(non_flags) and any(
            flag in rest for flag in _GIT_BRANCH_LISTING_FLAGS
        )
        return not non_flags or listed
    if subcommand == "reflog":
        if not rest:
            return True
        if rest[0] in {"expire", "delete", "drop"}:
            return False
        if rest[0] == "show":
            rest = rest[1:]
        elif not rest[0].startswith("-"):
            return False
        if _git_format_unsafe(rest):
            return False
        return _parse_args(rest, cwd, _GIT_SPECS["log"]) is not None

    if subcommand == "grep":
        return _validate_grep_like(rest, cwd, _GIT_SPECS["grep"], files_mode=False)

    spec = _GIT_SPECS.get(subcommand)
    if spec is None:
        return False
    if subcommand in {"log", "show", "shortlog"} and _git_format_unsafe(rest):
        return False
    return _parse_args(rest, cwd, spec) is not None
