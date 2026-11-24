#!/usr/bin/env python3
"""CSV Duplicate Counter.

Examines a CSV file, lets you choose columns in the order you want, and writes
a full CSV with an extra ``Dup_Count`` column holding the number of *extra*
copies (group size - 1) of that row's key combination.

On a terminal, columns are chosen with an arrow-key picker: up/down to move,
space to toggle, enter to confirm. When the input is piped instead, you type
your selection.

Standard library only. Reads and writes using the Windows ANSI code page
(e.g. cp1252) unless the file carries a UTF-8 BOM or --encoding is given.

Usage:
    python csv_dupcounter.py <input.csv> [output.csv] [--encoding CODEPAGE]
"""

import argparse
import codecs
import csv
import locale
import os
import re
import sys
from collections import Counter

COUNT_COLUMN = "Dup_Count"
ENCODING_ERRORS = "replace"
SAMPLE_CHARS = 64 * 1024
CANDIDATE_DELIMITERS = (",", ";", "\t", "|")
MAX_SNIFF_LINES = 50


class AppError(Exception):
    """A problem the user can act on. main() prints it and exits 1."""


class SelectionError(Exception):
    """A bad column selection. The prompt loop catches it and asks again."""


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------

def windows_ansi_codepage():
    """Return the Windows ANSI code page as a Python codec name.

    Returns None when not on Windows, when GetACP fails, or when Python has
    no codec for the reported code page.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        acp = ctypes.windll.kernel32.GetACP()
    except Exception:
        return None
    if not acp:
        return None
    name = "cp%d" % acp
    try:
        codecs.lookup(name)
    except LookupError:
        return None
    return name


def has_utf8_bom(path):
    """True when the file starts with a UTF-8 byte order mark."""
    with open(path, "rb") as handle:
        return handle.read(3) == codecs.BOM_UTF8


def resolve_encoding(path, override=None):
    """Pick the codec used for both reading and writing.

    Order: explicit override, UTF-8 BOM, Windows ANSI code page, then the
    locale's preferred encoding.
    """
    if override:
        try:
            codecs.lookup(override)
        except LookupError:
            raise AppError("unknown encoding: %s" % override)
        return override
    if has_utf8_bom(path):
        return "utf-8-sig"
    codepage = windows_ansi_codepage()
    if codepage:
        return codepage
    try:
        preferred = locale.getpreferredencoding(False)
    except TypeError:
        preferred = locale.getpreferredencoding()
    except Exception:
        preferred = None
    if preferred:
        try:
            codecs.lookup(preferred)
            return preferred
        except LookupError:
            pass
    return "cp1252"


# ---------------------------------------------------------------------------
# CSV shape
# ---------------------------------------------------------------------------

def _read_sample(path, encoding):
    with open(path, "r", encoding=encoding, errors=ENCODING_ERRORS, newline="") as handle:
        return handle.read(SAMPLE_CHARS)


def _score_delimiters(lines):
    """Map candidate delimiter -> field count, for candidates that fit the sample.

    A candidate qualifies when the most common field count covers at least
    80% of the sampled lines and that width is greater than 1. The widest
    qualifying candidate wins, which is what identifies the real delimiter
    when another character also appears consistently in every row.
    """
    sample = lines[:MAX_SNIFF_LINES]
    scores = {}
    for candidate in CANDIDATE_DELIMITERS:
        counts = Counter()
        for line in sample:
            row = next(csv.reader([line], delimiter=candidate), [])
            counts[len(row)] += 1
        if not counts:
            continue
        width, hits = counts.most_common(1)[0]
        if width > 1 and hits * 5 >= len(sample) * 4:
            scores[candidate] = width
    return scores


def detect_dialect(path, encoding):
    """Return the delimiter character used by the file.

    The stdlib Sniffer is tried first because it weighs how consistent each
    candidate is against how plausible the resulting fields look - it gets
    files whose values contain the other candidate delimiter right, where a
    pure frequency count would not. It only detects the delimiters it is
    handed, so '|' is included explicitly.
    """
    sample = _read_sample(path, encoding)
    lines = [line for line in sample.splitlines() if line.strip()]
    if not lines:
        return ","
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters="".join(CANDIDATE_DELIMITERS))
        if dialect.delimiter and dialect.delimiter in lines[0]:
            return dialect.delimiter
    except csv.Error:
        pass
    scores = _score_delimiters(lines)
    if scores:
        widest = max(scores.values())
        for candidate in CANDIDATE_DELIMITERS:
            if scores.get(candidate) == widest:
                return candidate
    return ","


def consistent_delimiters(path, encoding):
    """Every candidate delimiter that would split this file consistently.

    More than one result means the file is genuinely ambiguous - a value
    contains the other delimiter often enough to look like a real one. The
    caller warns, and --delimiter is the way to settle it.
    """
    sample = _read_sample(path, encoding)
    lines = [line for line in sample.splitlines() if line.strip()]
    scores = _score_delimiters(lines)
    return [candidate for candidate in CANDIDATE_DELIMITERS if candidate in scores]


def _is_blank_row(row):
    """True for an empty row, or one whose cells are all whitespace."""
    return not row or all(not cell.strip() for cell in row)


def read_header(path, encoding, delimiter):
    """Return the column names exactly as stored, minus a leading BOM."""
    with open(path, "r", encoding=encoding, errors=ENCODING_ERRORS, newline="") as handle:
        for row in csv.reader(handle, delimiter=delimiter):
            if _is_blank_row(row):
                continue
            header = list(row)
            header[0] = header[0].lstrip("\ufeff")
            return header
    raise AppError("no header row found in %s" % path)


# ---------------------------------------------------------------------------
# Column selection
# ---------------------------------------------------------------------------

def display_name(columns, index):
    """How a column is shown to the user. Blank headers get a positional label."""
    name = columns[index].strip()
    if not name:
        return "(column %d)" % (index + 1)
    return name


def print_columns(columns, out=print):
    """Print the numbered column list."""
    out("Columns:")
    for index in range(len(columns)):
        out("  %d. %s" % (index + 1, display_name(columns, index)))
    return columns


def _resolve_token(token, columns):
    """Turn one selection token into a 0-based column index."""
    if token.isdigit():
        number = int(token)
        if number < 1 or number > len(columns):
            raise SelectionError(
                "column %d does not exist (file has %d columns)" % (number, len(columns)))
        return number - 1
    wanted = token.strip().lower()
    matches = [index for index, name in enumerate(columns)
               if name.strip().lower() == wanted]
    if not matches:
        raise SelectionError("unknown column '%s'" % token)
    if len(matches) > 1:
        raise SelectionError(
            "column '%s' is ambiguous (%d matches) - use its number instead"
            % (token, len(matches)))
    return matches[0]


_COLUMN_LABEL = re.compile(r"\(\s*column\s+(\d+)\s*\)", re.IGNORECASE)


def _normalize_labels(text, columns):
    """Rewrite '(column 3)' labels into plain indices so they survive splitting."""

    def replace(match):
        number = int(match.group(1))
        if 1 <= number <= len(columns):
            return " %d " % number
        return match.group(0)

    return _COLUMN_LABEL.sub(replace, text)


def parse_selection(text, columns):
    """Turn selection text into an ordered list of 0-based column indices.

    Commas and semicolons separate the columns. Within a segment, a name
    that matches a column exactly wins, so a column whose name contains a
    space ("Order Date") can still be picked. A segment that is not a name
    is split on whitespace, so "3 1 5" works too.
    """
    text = text.strip()
    if not text:
        raise SelectionError("no columns selected")
    if text.isdigit():
        number = int(text)
        if number < 1 or number > len(columns):
            raise SelectionError(
                "column %d does not exist (file has %d columns)" % (number, len(columns)))
        return [number - 1]

    segments = [part.strip() for part in
                re.split(r"[,;]", _normalize_labels(text, columns)) if part.strip()]
    if not segments:
        raise SelectionError("no columns selected")
    if len(segments) == 1 and segments[0].lower() == "all":
        return list(range(len(columns)))

    indices = []
    seen = set()
    for segment in segments:
        matches = [index for index, name in enumerate(columns)
                   if name.strip().lower() == segment.lower()]
        if len(matches) > 1:
            raise SelectionError(
                "column '%s' is ambiguous (%d matches) - use its number instead"
                % (segment, len(matches)))
        if matches:
            candidates = matches
        else:
            tokens = segment.split()
            if not tokens:
                continue
            if len(tokens) == 1 and tokens[0].lower() == "all":
                candidates = list(range(len(columns)))
            else:
                candidates = [_resolve_token(token, columns) for token in tokens]
        for index in candidates:
            if index in seen:
                raise SelectionError(
                    "'%s' selected more than once" % display_name(columns, index))
            seen.add(index)
            indices.append(index)
    return indices


def prompt_for_selection(columns, input_func=input, out=print):
    """Ask which columns to group by until a valid selection is given.

    Returns the ordered indices, or None when the user cancels.
    """
    while True:
        text = input_func("> ")
        stripped = text.strip()
        if stripped.lower() == "list":
            print_columns(columns, out)
            continue
        if not stripped:
            return None
        try:
            return parse_selection(stripped, columns)
        except SelectionError as exc:
            out("Error: %s" % exc)


# ---------------------------------------------------------------------------
# Counting and export
# ---------------------------------------------------------------------------

def iter_data_rows(path, encoding, delimiter):
    """Yield the data rows, skipping the header and any blank rows.

    A blank line before the header is skipped the same way read_header skips
    it, so both agree on which row is the header. Blank lines *after* the
    header are dropped too - they are a common artefact of Excel exports and
    would otherwise become empty data rows with a count of their own.
    """
    with open(path, "r", encoding=encoding, errors=ENCODING_ERRORS, newline="") as handle:
        reader = csv.reader(handle, delimiter=delimiter)
        seen_header = False
        for row in reader:
            if not seen_header:
                if _is_blank_row(row):
                    continue
                seen_header = True
                continue
            if not row:
                continue
            yield row


def _cell(row, index):
    """Value at `index` in `row`, or '' when the row is shorter than the header."""
    return row[index] if index < len(row) else ""


def count_groups(path, encoding, delimiter, indices):
    """Pass 1: how many rows share each key combination."""
    counts = {}
    for row in iter_data_rows(path, encoding, delimiter):
        key = tuple(_cell(row, index) for index in indices)
        counts[key] = counts.get(key, 0) + 1
    return counts


def build_output_order(header, indices):
    """Return (output header, remaining column indices)."""
    chosen = set(indices)
    remaining = [index for index in range(len(header)) if index not in chosen]
    output_header = [COUNT_COLUMN]
    output_header += [header[index] for index in indices]
    output_header += [header[index] for index in remaining]
    return output_header, remaining


def write_output(input_path, output_path, header, encoding, delimiter, indices, counts):
    """Pass 2: write the full CSV plus the duplicate count. Returns the row count."""
    output_header, remaining = build_output_order(header, indices)
    written = 0
    with open(output_path, "w", encoding=encoding, errors=ENCODING_ERRORS, newline="") as out:
        writer = csv.writer(out, delimiter=delimiter, lineterminator="\r\n")
        writer.writerow(output_header)
        for row in iter_data_rows(input_path, encoding, delimiter):
            key = tuple(_cell(row, index) for index in indices)
            extras = counts.get(key, 0) - 1
            out_row = [str(extras)]
            out_row += [_cell(row, index) for index in indices]
            out_row += [_cell(row, index) for index in remaining]
            out_row += list(row[len(header):])
            writer.writerow(out_row)
            written += 1
    return written


# ---------------------------------------------------------------------------
# Interactive column picker (arrow keys)
# ---------------------------------------------------------------------------

_ANSI_CLEAR = "\x1b[H\x1b[J"
_ANSI_HIDE_CURSOR = "\x1b[?25l"
_ANSI_SHOW_CURSOR = "\x1b[?25h"

# Scan codes Windows sends after a leading NUL or 0xE0.
_WIN_KEYS = {
    "H": "up", "P": "down", "K": "left", "M": "right",
    "G": "home", "O": "end", "I": "pageup", "Q": "pagedown",
    "R": "insert", "S": "delete",
}

# Escape sequences a POSIX terminal sends for the same keys.
_CSI_KEYS = {
    "\x1b[A": "up", "\x1b[B": "down", "\x1b[C": "right", "\x1b[D": "left",
    "\x1b[H": "home", "\x1b[F": "end", "\x1bOH": "home", "\x1bOF": "end",
    "\x1b[1~": "home", "\x1b[4~": "end", "\x1b[7~": "home", "\x1b[8~": "end",
    "\x1b[5~": "pageup", "\x1b[6~": "pagedown", "\x1b[3~": "delete",
}


def _enable_windows_vt():
    """Turn on ANSI escape sequence support in the Windows console."""
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        enable_vt = 0x0004
        if mode.value & enable_vt:
            return True
        return bool(kernel32.SetConsoleMode(handle, mode.value | enable_vt))
    except Exception:
        return False


class KeyReader:
    """Reads single keypresses, including the arrow keys, on Windows and POSIX.

    Used as a context manager: entering puts the terminal into a mode where
    each key arrives immediately and untyped, exiting restores the original
    mode.
    """

    def __init__(self):
        self._windows = sys.platform == "win32"
        self._fd = None
        self._saved = None

    def __enter__(self):
        if not self._windows:
            try:
                import termios
                import tty
                self._fd = sys.stdin.fileno()
                self._saved = termios.tcgetattr(self._fd)
                # cbreak, not raw: Ctrl+C still raises KeyboardInterrupt.
                tty.setcbreak(self._fd)
            except Exception:
                self._fd = None
                self._saved = None
        return self

    def __exit__(self, *exc_info):
        if self._saved is not None:
            try:
                import termios
                termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)
            except Exception:
                pass
            self._saved = None
            self._fd = None

    def read_key(self):
        """Return a key name, or None if the input ended."""
        if self._windows:
            return self._read_key_windows()
        if self._fd is None:
            return None
        return self._read_key_posix()

    @staticmethod
    def _read_key_windows():
        import msvcrt
        char = msvcrt.getwch()
        if char in ("\x00", "\xe0"):
            return _WIN_KEYS.get(msvcrt.getwch(), "unknown")
        if char in ("\r", "\n"):
            return "enter"
        if char == " ":
            return "space"
        if char == "\x1b":
            return "esc"
        if char == "\x03":
            return "ctrl-c"
        return char

    def _read_key_posix(self):
        char = self._read_byte()
        if char is None:
            return None
        if char == "\x1b":
            return self._read_escape()
        if char in ("\r", "\n"):
            return "enter"
        if char == " ":
            return "space"
        if char == "\x03":
            return "ctrl-c"
        if char == "\x04":
            return "ctrl-d"
        return char

    def _read_byte(self):
        """Read one character, assembling multi-byte UTF-8 sequences."""
        try:
            data = os.read(self._fd, 1)
        except OSError:
            return None
        if not data:
            return None
        first = data[0]
        if first < 0x80:
            return chr(first)
        if first >= 0xF0:
            extra = 3
        elif first >= 0xE0:
            extra = 2
        else:
            extra = 1
        raw = bytearray(data)
        for _ in range(extra):
            try:
                more = os.read(self._fd, 1)
            except OSError:
                break
            if not more:
                break
            raw += more
        return raw.decode("utf-8", "replace")

    def _read_escape(self):
        """Read the rest of an escape sequence, if one follows."""
        import select
        sequence = "\x1b"
        for _ in range(8):
            ready, _, _ = select.select([self._fd], [], [], 0.05)
            if not ready:
                break
            char = self._read_byte()
            if char is None:
                break
            sequence += char
            if char.isalpha() or char == "~":
                break
        return _CSI_KEYS.get(sequence, "esc")


class ColumnPicker:
    """Arrow-key column chooser.

    Up/down move the cursor, space toggles the current column in or out of
    the selection, enter confirms and returns the selection in the order the
    columns were picked, escape cancels.
    """

    def __init__(self, columns, context=None):
        self.columns = columns
        self.context = list(context or [])
        self.cursor = 0
        self.chosen = []
        self.offset = 0

    def run(self, reader=None):
        """Return the ordered column indices, or None if the user cancelled."""
        stdout = sys.stdout
        try:
            stdout.write(_ANSI_HIDE_CURSOR)
            stdout.flush()
        except Exception:
            pass
        try:
            if reader is None:
                with KeyReader() as active:
                    return self._loop(active)
            return self._loop(reader)
        finally:
            try:
                stdout.write(_ANSI_SHOW_CURSOR)
                stdout.flush()
            except Exception:
                pass

    def _loop(self, reader):
        while True:
            self._render()
            key = reader.read_key()
            if key is None or key in ("esc", "ctrl-c", "ctrl-d"):
                return None
            if key == "up":
                self.cursor = max(0, self.cursor - 1)
            elif key == "down":
                self.cursor = min(len(self.columns) - 1, self.cursor + 1)
            elif key == "home":
                self.cursor = 0
            elif key == "end":
                self.cursor = len(self.columns) - 1
            elif key == "pageup":
                self.cursor = max(0, self.cursor - 10)
            elif key == "pagedown":
                self.cursor = min(len(self.columns) - 1, self.cursor + 10)
            elif key == "j":
                self.cursor = min(len(self.columns) - 1, self.cursor + 1)
            elif key == "k":
                self.cursor = max(0, self.cursor - 1)
            elif key == "space":
                if self.cursor in self.chosen:
                    self.chosen.remove(self.cursor)
                else:
                    self.chosen.append(self.cursor)
            elif key == "enter":
                if not self.chosen:
                    continue
                return list(self.chosen)

    def _render(self):
        import shutil
        size = shutil.get_terminal_size(fallback=(80, 24))
        width = max(40, size.columns - 2)
        limit = max(3, size.lines - 9)

        if self.cursor < self.offset:
            self.offset = self.cursor
        elif self.cursor >= self.offset + limit:
            self.offset = self.cursor - limit + 1
        self.offset = max(0, min(self.offset, max(0, len(self.columns) - limit)))

        out = [_ANSI_CLEAR]
        for line in self.context:
            out.append(line + "\n")
        out.append("Select columns to group by, in order.\n")
        out.append("  up/down move   space toggle   enter confirm   esc cancel\n")
        out.append("\n")
        for index in range(self.offset, min(self.offset + limit, len(self.columns))):
            marker = ">" if index == self.cursor else " "
            name = display_name(self.columns, index)
            tag = "   [%d]" % (self.chosen.index(index) + 1) if index in self.chosen else ""
            if len(name) + len(tag) > width - 6:
                name = name[:max(1, width - 9 - len(tag))] + "..."
            out.append(" %s %3d. %s%s\n" % (marker, index + 1, name, tag))
        out.append("\n")
        if self.chosen:
            order = ", ".join(display_name(self.columns, index) for index in self.chosen)
            if len(order) > width:
                order = order[:max(1, width - 3)] + "..."
            out.append("Order: %s\n" % order)
        else:
            out.append("Order: (nothing selected yet)\n")
        out.append("Space toggles a column in or out; the number in brackets is its "
                   "place in the order.\n")
        sys.stdout.write("".join(out))
        sys.stdout.flush()


def _interactive_terminal_available():
    """True when we can drive the terminal with the arrow-key picker."""
    try:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return False
    except Exception:
        return False
    if sys.platform == "win32":
        return _enable_windows_vt()
    return True


def pick_columns(columns, use_arrow_keys=None, context=None):
    """Choose columns to group by. Returns ordered indices, or None to cancel."""
    if use_arrow_keys is None:
        use_arrow_keys = _interactive_terminal_available()
    if use_arrow_keys:
        try:
            return ColumnPicker(columns, context=context).run()
        except Exception:
            # A terminal that will not cooperate falls back to typing.
            return prompt_for_selection(columns, input_func=input, out=print)
    # `input` is resolved here, at call time, so tests can patch builtins.input.
    return prompt_for_selection(columns, input_func=input, out=print)


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def default_output_path(input_path):
    """dupes-<input file name> next to the input file."""
    directory = os.path.dirname(os.path.abspath(input_path))
    stem, extension = os.path.splitext(os.path.basename(input_path))
    if not extension:
        extension = ".csv"
    return os.path.join(directory, "dupes-" + stem + extension)


def _same_file(first, second):
    """True when both paths point at the same file on disk."""
    try:
        return os.path.samefile(first, second)
    except OSError:
        return os.path.realpath(first) == os.path.realpath(second)


def confirm(question="Proceed? [Y/n] ", input_func=input, out=print):
    """Ask a yes/no question. Blank means yes."""
    while True:
        answer = input_func(question).strip().lower()
        if answer in ("", "y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        out("Please answer y or n.")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="csv_dupcounter.py",
        description="Add a duplicate-count column to a CSV file, based on the "
                    "columns you choose. Reads and writes using the Windows "
                    "ANSI code page.")
    parser.add_argument("input", help="path to the input CSV file")
    parser.add_argument("output", nargs="?",
                        help="path for the output CSV "
                             "(default: <input>_dupcount.csv next to the input)")
    parser.add_argument("--encoding", metavar="CODEPAGE",
                        help="override the encoding used for reading and writing, "
                             "e.g. cp1252 or utf-8")
    parser.add_argument("--delimiter", metavar="CHAR",
                        help="override the detected delimiter, e.g. ';' or '\\t'")
    return parser


def _plural(count, singular, plural):
    """'1 row' / '3 rows' - keeps the summary free of grammar bugs."""
    return "%d %s" % (count, singular if count == 1 else plural)


def main(argv=None):
    args = build_parser().parse_args(argv)
    input_path = args.input

    try:
        if not os.path.exists(input_path):
            raise AppError("no such file: %s" % input_path)
        if os.path.isdir(input_path):
            raise AppError("%s is a directory, not a CSV file" % input_path)
        if os.path.getsize(input_path) == 0:
            raise AppError("%s is empty" % input_path)
        if args.delimiter and len(args.delimiter) != 1:
            raise AppError("--delimiter must be a single character")
        encoding = resolve_encoding(input_path, args.encoding)
        delimiter = args.delimiter or detect_dialect(input_path, encoding)
        header = read_header(input_path, encoding, delimiter)
        output_path = args.output or default_output_path(input_path)
    except AppError as exc:
        print("Error: %s" % exc)
        return 1

    # Writing over the input would truncate it before the second pass reads
    # it, so refuse that outright rather than destroy the user's data.
    if _same_file(input_path, output_path):
        print("Error: the output file must be different from the input file")
        return 1

    info_lines = [
        "Opened %s" % input_path,
        "  encoding : %s" % encoding,
        "  delimiter: %s" % delimiter,
        "  columns  : %d" % len(header),
    ]
    if not args.delimiter:
        candidates = consistent_delimiters(input_path, encoding)
        if len(candidates) > 1:
            info_lines.append(
                "  note     : %s would all fit this file; if the columns look "
                "wrong, re-run with --delimiter"
                % ", ".join(repr(c) for c in candidates))

    arrow_mode = _interactive_terminal_available()
    if arrow_mode:
        print()
    else:
        for line in info_lines:
            print(line)
        print()
        print_columns(header)
        print()
        print("Select columns to group by, in order.")
        print("  - numbers:  3,1,5")
        print("  - names:    city,item")
        print("  - commands: list (show columns again), all (every column), blank (cancel)")
        print()

    try:
        indices = pick_columns(header, use_arrow_keys=arrow_mode, context=info_lines)
    except (EOFError, KeyboardInterrupt):
        print()
        print("Cancelled.")
        return 0

    if not indices:
        print("Cancelled.")
        return 0

    output_header, _ = build_output_order(header, indices)
    print()
    print("Grouping by %s, in this order: %s"
          % (_plural(len(indices), "column", "columns"),
             ", ".join(display_name(header, index) for index in indices)))
    print("Output layout: %s"
          % ", ".join(name if name.strip() else "(blank)" for name in output_header))
    if COUNT_COLUMN in header:
        print("Warning: the source file already has a column named %s - the output "
              "will contain two columns with that name." % COUNT_COLUMN)

    # The arrow-key picker confirms with enter, so it needs no second prompt.
    if not arrow_mode:
        try:
            if not confirm(input_func=input, out=print):
                print("Cancelled.")
                return 0
        except (EOFError, KeyboardInterrupt):
            print()
            print("Cancelled.")
            return 0

    print()
    if os.path.exists(output_path):
        print("Overwriting existing file: %s" % output_path)
    try:
        counts = count_groups(input_path, encoding, delimiter, indices)
    except OSError as exc:
        print("Error: cannot read input: %s" % exc)
        return 1
    try:
        written = write_output(input_path, output_path, header, encoding,
                               delimiter, indices, counts)
    except OSError as exc:
        print("Error: cannot write output: %s" % exc)
        return 1

    groups = sum(1 for size in counts.values() if size > 1)
    print("Read %s from %s" % (_plural(written, "data row", "data rows"), input_path))
    print("%s; %s duplicates"
          % (_plural(len(counts), "distinct key combination", "distinct key combinations"),
             _plural(groups, "group contains", "groups contain")))
    print("Wrote %s to %s" % (_plural(written, "row", "rows"), output_path))
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
