# CSV Duplicate Counter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone Python CLI that reads a CSV using the Windows ANSI code page, lets the user pick columns in order, and writes a full CSV with a `Dup_Count` column holding the number of extra copies (group size − 1) of each row's key combination.

**Architecture:** One application file, `csv_dupcounter.py`, built from small single-purpose functions: encoding resolution, dialect detection, header reading, selection parsing, group counting, and export. The export is a two-pass streaming operation — pass 1 builds a `{key: group_size}` dictionary, pass 2 re-reads the source and streams rows to the output — so memory scales with distinct key combinations rather than row count. A second file, `test_csv_dupcounter.py`, holds a plain `unittest` suite that drives every function directly.

**Tech Stack:** Python 3.8+ standard library only (`argparse`, `codecs`, `csv`, `ctypes`, `locale`, `os`, `re`, `sys`, `collections.Counter`, `unittest`). No third-party packages. No `pip install`.

## Global Constraints

- Standard library only. The application must never import a third-party module.
- Must run on Windows with Python 3.8+ and on Linux/macOS for testing.
- Read and write the CSV using the **same** codec, resolved as: `--encoding` override → UTF-8 BOM → Windows ANSI code page (`GetACP`) → `locale.getpreferredencoding()` → `cp1252`.
- Use `errors="replace"` on every open, so an undefined byte becomes `?` instead of raising.
- The count column is always named `Dup_Count` and always the **first** column of the output.
- `Dup_Count` holds **extra copies beyond the first** (`group_size - 1`), identical for every row in a group.
- Output column order: `Dup_Count`, selected columns in the user's chosen order, then remaining columns in original file order.
- Output rows appear in input row order. The output uses the same delimiter as the input.
- The input file is never modified.
- Version control: the workspace has no git repo and the user has not requested one. The "Commit" steps below
  are listed for reference only and are **skipped** during execution. If the user asks for version control,
  run `git init` first, then the commit steps apply as written.

---

## File Structure

| Path | Responsibility |
|---|---|
| `csv_dupcounter.py` | The application. Encoding resolution, dialect detection, header reading, selection parsing, counting, export, CLI. |
| `test_csv_dupcounter.py` | `unittest` suite covering all 13 behaviours from the spec §9. |
| `README.md` | Usage notes: how to run, how to select columns, what the output means. |

---

## Task 1: Encoding, dialect detection, and header reading

**Files:**
- Create: `csv_dupcounter.py`
- Create: `test_csv_dupcounter.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces:
  - `AppError(Exception)` — user-facing error; `main()` prints `Error: <message>` and returns 1.
  - `resolve_encoding(path: str, override: Optional[str] = None) -> str` — codec name.
  - `detect_dialect(path: str, encoding: str) -> str` — single-character delimiter.
  - `read_header(path: str, encoding: str, delimiter: str) -> List[str]` — column names.
  - `windows_ansi_codepage() -> Optional[str]` — codec name or `None`.
  - `has_utf8_bom(path: str) -> bool`.

- [ ] **Step 1: Write the failing test file**

Create `test_csv_dupcounter.py` with this exact content:

```python
#!/usr/bin/env python3
"""Tests for csv_dupcounter. Run with: python test_csv_dupcounter.py"""

import codecs
import contextlib
import csv
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import csv_dupcounter as app


ORDERS = [
    ["OrderID", "Customer", "City", "Item", "Qty"],
    ["1001", "Acme", "Lisbon", "Widget", "2"],
    ["1002", "Globex", "Lisbon", "Widget", "5"],
    ["1003", "Acme", "Lisbon", "Gadget", "1"],
    ["1004", "Initech", "Lisbon", "Widget", "3"],
    ["1005", "Acme", "Lisbon", "Widget", "7"],
]


class BaseTestCase(unittest.TestCase):
    """Shared helpers: temp dir, CSV writer, CSV reader."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def path(self, name):
        return os.path.join(self.tmp.name, name)

    def write_csv(self, name, rows, encoding="cp1252", delimiter=",", newline=""):
        target = self.path(name)
        with open(target, "w", encoding=encoding, errors="replace", newline="") as handle:
            writer = csv.writer(handle, delimiter=delimiter, lineterminator="\r\n")
            writer.writerows(rows)
        return target

    def read_csv(self, path, encoding="cp1252", delimiter=","):
        with open(path, "r", encoding=encoding, errors="replace", newline="") as handle:
            return list(csv.reader(handle, delimiter=delimiter))


class EncodingTests(BaseTestCase):
    def test_resolve_encoding_honours_explicit_override(self):
        source = self.write_csv("a.csv", ORDERS)
        self.assertEqual(app.resolve_encoding(source, "utf-8"), "utf-8")

    def test_resolve_encoding_rejects_unknown_override(self):
        source = self.write_csv("a.csv", ORDERS)
        with self.assertRaises(app.AppError):
            app.resolve_encoding(source, "not-a-real-codec")

    def test_resolve_encoding_detects_utf8_bom(self):
        target = self.path("bom.csv")
        with open(target, "w", encoding="utf-8-sig", newline="") as handle:
            handle.write("Name,City\nAna,Køln\n")
        self.assertEqual(app.resolve_encoding(target), "utf-8-sig")

    def test_resolve_encoding_falls_back_to_a_known_codec(self):
        source = self.write_csv("plain.csv", ORDERS)
        encoding = app.resolve_encoding(source)
        self.assertTrue(codecs.lookup(encoding))
        self.assertNotEqual(encoding, "utf-8-sig")

    def test_undecodable_byte_does_not_raise(self):
        target = self.path("broken.csv")
        with open(target, "wb") as handle:
            handle.write(b"Name,City\n\x81Ana,Køln\n")
        encoding = app.resolve_encoding(target, "cp1252")
        header = app.read_header(target, encoding, ",")
        self.assertEqual(header, ["Name", "City"])


class DialectTests(BaseTestCase):
    def test_detects_comma_semicolon_tab_and_pipe(self):
        for delimiter in (",", ";", "\t", "|"):
            source = self.write_csv("delim%s.csv" % delimiter, ORDERS, delimiter=delimiter)
            encoding = app.resolve_encoding(source)
            self.assertEqual(app.detect_dialect(source, encoding), delimiter)

    def test_wider_consistent_split_wins(self):
        # Every row contains one comma too, but the real delimiter is the semicolon.
        rows = [["a,b", "c", "d"], ["1,2", "3", "4"], ["5,6", "7", "8"]]
        source = self.write_csv("mixed.csv", rows, delimiter=";")
        encoding = app.resolve_encoding(source)
        self.assertEqual(app.detect_dialect(source, encoding), ";")

    def test_single_column_file_uses_comma(self):
        source = self.write_csv("one.csv", [["Name"], ["Ana"], ["Bo"]])
        encoding = app.resolve_encoding(source)
        self.assertEqual(app.detect_dialect(source, encoding), ",")


class HeaderTests(BaseTestCase):
    def test_read_header_returns_column_names(self):
        source = self.write_csv("orders.csv", ORDERS)
        header = app.read_header(source, "cp1252", ",")
        self.assertEqual(header, ["OrderID", "Customer", "City", "Item", "Qty"])

    def test_read_header_strips_bom(self):
        target = self.path("bom.csv")
        with open(target, "w", encoding="utf-8-sig", newline="") as handle:
            handle.write("Name,City\nAna,Køln\n")
        header = app.read_header(target, "utf-8-sig", ",")
        self.assertEqual(header[0], "Name")

    def test_read_header_skips_blank_leading_lines(self):
        target = self.path("blank.csv")
        with open(target, "w", encoding="cp1252", newline="") as handle:
            handle.write("\n\nName,City\nAna,Køln\n")
        header = app.read_header(target, "cp1252", ",")
        self.assertEqual(header, ["Name", "City"])

    def test_read_header_raises_on_empty_file(self):
        target = self.path("empty.csv")
        open(target, "wb").close()
        with self.assertRaises(app.AppError):
            app.read_header(target, "cp1252", ",")


if __name__ == "__main__":
    unittest.main(verbosity=2)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m unittest test_csv_dupcounter -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'csv_dupcounter'`.

- [ ] **Step 3: Create the application skeleton with encoding, dialect, and header functions**

Create `csv_dupcounter.py` with this exact content:

```python
#!/usr/bin/env python3
"""CSV Duplicate Counter.

Examines a CSV file, lets you choose columns in the order you want, and writes
a full CSV with an extra ``Dup_Count`` column holding the number of *extra*
copies (group size - 1) of that row's key combination.

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
    """Return the delimiter character used by the file."""
    sample = _read_sample(path, encoding)
    lines = [line for line in sample.splitlines() if line.strip()]
    if not lines:
        return ","
    scores = _score_delimiters(lines)
    if scores:
        widest = max(scores.values())
        for candidate in CANDIDATE_DELIMITERS:
            if scores.get(candidate) == widest:
                return candidate
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters="".join(CANDIDATE_DELIMITERS))
        if dialect.delimiter:
            return dialect.delimiter
    except csv.Error:
        pass
    return ","


def read_header(path, encoding, delimiter):
    """Return the column names exactly as stored, minus a leading BOM."""
    with open(path, "r", encoding=encoding, errors=ENCODING_ERRORS, newline="") as handle:
        for row in csv.reader(handle, delimiter=delimiter):
            if not row:
                continue
            header = list(row)
            header[0] = header[0].lstrip("\ufeff")
            return header
    raise AppError("no header row found in %s" % path)


if __name__ == "__main__":
    sys.exit("csv_dupcounter: not complete yet")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m unittest test_csv_dupcounter -v`

Expected: all Task 1 tests PASS (the `test_wider_consistent_split_wins` test builds its own fixture at runtime — if it fails, check that the fixture actually contains a comma in every row).

- [ ] **Step 5: Commit (skipped unless the user asks for version control)**

```bash
git add csv_dupcounter.py test_csv_dupcounter.py
git commit -m "feat: add encoding resolution, dialect detection, header reading"
```

---

## Task 2: Column selection parsing

**Files:**
- Modify: `csv_dupcounter.py`
- Modify: `test_csv_dupcounter.py`

**Interfaces:**
- Consumes: `AppError`, `SelectionError` (Task 1).
- Produces:
  - `display_name(columns: List[str], index: int) -> str` — user-facing label; blank headers become `(column N)`.
  - `print_columns(columns: List[str], out=print) -> List[str]`.
  - `parse_selection(text: str, columns: List[str]) -> List[int]` — ordered 0-based indices; raises `SelectionError`.
  - `prompt_for_selection(columns, input_func=input, out=print) -> Optional[List[int]]` — `None` means cancelled.

- [ ] **Step 1: Write the failing tests**

Append to `test_csv_dupcounter.py`, above the `if __name__ == "__main__":` block:

```python
class SelectionTests(BaseTestCase):
    COLUMNS = ["OrderID", "Customer", "City", "Item", "Qty"]

    def test_parse_selection_accepts_indices(self):
        self.assertEqual(app.parse_selection("3,1,5", self.COLUMNS), [2, 0, 4])

    def test_parse_selection_accepts_spaces_and_semicolons(self):
        self.assertEqual(app.parse_selection("3 1 5", self.COLUMNS), [2, 0, 4])
        self.assertEqual(app.parse_selection("3;1;5", self.COLUMNS), [2, 0, 4])

    def test_parse_selection_accepts_names(self):
        self.assertEqual(app.parse_selection("city,item", self.COLUMNS), [2, 3])

    def test_parse_selection_is_case_insensitive_for_names(self):
        self.assertEqual(app.parse_selection("CITY,Item", self.COLUMNS), [2, 3])

    def test_parse_selection_mixes_indices_and_names(self):
        self.assertEqual(app.parse_selection("1,item", self.COLUMNS), [0, 3])

    def test_parse_selection_all_selects_every_column(self):
        self.assertEqual(app.parse_selection("all", self.COLUMNS), [0, 1, 2, 3, 4])

    def test_parse_selection_preserves_the_chosen_order(self):
        self.assertEqual(app.parse_selection("item,city", self.COLUMNS), [3, 2])

    def test_parse_selection_rejects_unknown_name(self):
        with self.assertRaises(app.SelectionError):
            app.parse_selection("nope", self.COLUMNS)

    def test_parse_selection_rejects_out_of_range_index(self):
        with self.assertRaises(app.SelectionError):
            app.parse_selection("99", self.COLUMNS)

    def test_parse_selection_rejects_zero_index(self):
        with self.assertRaises(app.SelectionError):
            app.parse_selection("0", self.COLUMNS)

    def test_parse_selection_rejects_duplicates(self):
        with self.assertRaises(app.SelectionError):
            app.parse_selection("3,city", self.COLUMNS)

    def test_parse_selection_rejects_blank(self):
        with self.assertRaises(app.SelectionError):
            app.parse_selection("   ", self.COLUMNS)

    def test_display_name_labels_blank_headers(self):
        self.assertEqual(app.display_name(["", "Name"], 0), "(column 1)")
        self.assertEqual(app.display_name(["", "Name"], 1), "Name")

    def test_prompt_for_selection_loops_on_bad_input(self):
        answers = iter(["bogus", "2,3"])
        captured = []
        result = app.prompt_for_selection(
            self.COLUMNS, input_func=lambda prompt: next(answers), out=captured.append)
        self.assertEqual(result, [1, 2])
        self.assertTrue(any("Error" in line for line in captured))

    def test_prompt_for_selection_reprints_on_list(self):
        answers = iter(["list", "2"])
        captured = []
        result = app.prompt_for_selection(
            self.COLUMNS, input_func=lambda prompt: next(answers), out=captured.append)
        self.assertEqual(result, [1])
        self.assertTrue(any("OrderID" in line for line in captured))

    def test_prompt_for_selection_returns_none_on_blank(self):
        result = app.prompt_for_selection(
            self.COLUMNS, input_func=lambda prompt: "", out=lambda line: None)
        self.assertIsNone(result)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m unittest test_csv_dupcounter.SelectionTests -v`

Expected: FAIL — `AttributeError: module 'csv_dupcounter' has no attribute 'parse_selection'`.

- [ ] **Step 3: Implement selection parsing**

Insert into `csv_dupcounter.py`, immediately after the `read_header` function and before the `if __name__ == "__main__":` guard:

```python
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


def parse_selection(text, columns):
    """Turn selection text into an ordered list of 0-based column indices."""
    tokens = [token for token in re.split(r"[,;]|\s+", text.strip()) if token]
    if not tokens:
        raise SelectionError("no columns selected")
    if len(tokens) == 1 and tokens[0].lower() == "all":
        return list(range(len(columns)))
    indices = []
    seen = set()
    for token in tokens:
        index = _resolve_token(token, columns)
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
```

The `if __name__ == "__main__":` guard stays as-is for now — Task 4 replaces it with the real entry point.

- [ ] **Step 4: Run the full test suite to verify it passes**

Run: `python -m unittest test_csv_dupcounter -v`

Expected: all Task 1 and Task 2 tests PASS.

- [ ] **Step 5: Commit (skipped unless the user asks for version control)**

```bash
git add csv_dupcounter.py test_csv_dupcounter.py
git commit -m "feat: add ordered column selection parsing"
```

---

## Task 3: Counting and export

**Files:**
- Modify: `csv_dupcounter.py`
- Modify: `test_csv_dupcounter.py`

**Interfaces:**
- Consumes: `ENCODING_ERRORS`, `COUNT_COLUMN` (Task 1).
- Produces:
  - `iter_data_rows(path: str, encoding: str, delimiter: str) -> Iterator[List[str]]` — data rows only.
  - `count_groups(path: str, encoding: str, delimiter: str, indices: List[int]) -> Dict[Tuple[str, ...], int]` — group sizes.
  - `build_output_order(header: List[str], indices: List[int]) -> Tuple[List[str], List[int]]` — `(output_header, remaining_indices)`.
  - `write_output(input_path, output_path, header, encoding, delimiter, indices, counts) -> int` — rows written.

- [ ] **Step 1: Write the failing tests**

Append to `test_csv_dupcounter.py`, above the `if __name__ == "__main__":` block:

```python
class ExportTests(BaseTestCase):
    def export(self, source, indices, encoding="cp1252", delimiter=","):
        """Run the same two-pass export that main() runs."""
        output = self.path("out.csv")
        header = app.read_header(source, encoding, delimiter)
        counts = app.count_groups(source, encoding, delimiter, indices)
        written = app.write_output(
            source, output, header, encoding, delimiter, indices, counts)
        return output, written

    def test_count_is_extra_copies_beyond_the_first(self):
        source = self.write_csv("orders.csv", ORDERS)
        output, written = self.export(source, [2, 3])
        rows = self.read_csv(output)
        self.assertEqual(written, 5)
        self.assertEqual([row[0] for row in rows[1:]], ["3", "3", "0", "3", "3"])

    def test_output_column_order(self):
        source = self.write_csv("orders.csv", ORDERS)
        output, _ = self.export(source, [2, 3])
        rows = self.read_csv(output)
        self.assertEqual(
            rows[0], ["Dup_Count", "City", "Item", "OrderID", "Customer", "Qty"])

    def test_selected_order_is_honored(self):
        source = self.write_csv("orders.csv", ORDERS)
        output, _ = self.export(source, [3, 2])
        rows = self.read_csv(output)
        self.assertEqual(
            rows[0], ["Dup_Count", "Item", "City", "OrderID", "Customer", "Qty"])

    def test_row_order_is_preserved(self):
        source = self.write_csv("orders.csv", ORDERS)
        output, _ = self.export(source, [2, 3])
        rows = self.read_csv(output)
        self.assertEqual(
            [row[3] for row in rows[1:]], ["1001", "1002", "1003", "1004", "1005"])

    def test_cp1252_round_trip(self):
        rows = [["Name", "City"], ["Müller", "Köln"], ["García", "Sevilla"]]
        source = self.write_csv("latin.csv", rows)
        output, _ = self.export(source, [0])
        self.assertEqual(
            self.read_csv(output),
            [["Dup_Count", "Name", "City"],
             ["0", "Müller", "Köln"],
             ["0", "García", "Sevilla"]])
        with open(output, "rb") as handle:
            raw = handle.read()
        self.assertIn("Müller".encode("cp1252"), raw)
        self.assertNotIn("Müller".encode("utf-8"), raw)

    def test_all_columns_selected_leaves_nothing_remaining(self):
        source = self.write_csv("orders.csv", ORDERS)
        output, _ = self.export(source, [0, 1, 2, 3, 4])
        rows = self.read_csv(output)
        self.assertEqual(
            rows[0], ["Dup_Count", "OrderID", "Customer", "City", "Item", "Qty"])

    def test_blank_header_is_preserved(self):
        rows = [["Name", "", "City"], ["Ana", "x", "Køln"]]
        source = self.write_csv("blankhdr.csv", rows)
        output, _ = self.export(source, [0])
        rows_out = self.read_csv(output)
        self.assertEqual(rows_out[0], ["Dup_Count", "Name", "", "City"])

    def test_ragged_rows_are_padded_and_extra_fields_kept(self):
        rows = [["A", "B", "C"], ["1", "2"], ["3", "4", "5", "6"]]
        source = self.write_csv("ragged.csv", rows)
        output, _ = self.export(source, [0])
        rows_out = self.read_csv(output)
        # Row "1,2" is missing the trailing column C, so it is padded at the end.
        self.assertEqual(rows_out[1], ["0", "1", "2", ""])
        # Row "3,4,5,6" has one field beyond the header, which is kept at the end.
        self.assertEqual(rows_out[2], ["0", "3", "4", "5", "6"])

    def test_build_output_order_reports_remaining_indices(self):
        header = ["OrderID", "Customer", "City", "Item", "Qty"]
        output_header, remaining = app.build_output_order(header, [2, 3])
        self.assertEqual(output_header,
                         ["Dup_Count", "City", "Item", "OrderID", "Customer", "Qty"])
        self.assertEqual(remaining, [0, 1, 4])

    def test_count_groups_returns_group_sizes(self):
        source = self.write_csv("orders.csv", ORDERS)
        counts = app.count_groups(source, "cp1252", ",", [2, 3])
        self.assertEqual(counts[("Lisbon", "Widget")], 4)
        self.assertEqual(counts[("Lisbon", "Gadget")], 1)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m unittest test_csv_dupcounter.ExportTests -v`

Expected: FAIL — `AttributeError: module 'csv_dupcounter' has no attribute 'count_groups'`.

- [ ] **Step 3: Implement counting and export**

Insert into `csv_dupcounter.py`, immediately after the `prompt_for_selection` function and before the `if __name__ == "__main__":` guard:

```python
# ---------------------------------------------------------------------------
# Counting and export
# ---------------------------------------------------------------------------

def iter_data_rows(path, encoding, delimiter):
    """Yield the data rows, skipping the header and any blank leading rows."""
    with open(path, "r", encoding=encoding, errors=ENCODING_ERRORS, newline="") as handle:
        reader = csv.reader(handle, delimiter=delimiter)
        seen_header = False
        for row in reader:
            if not seen_header:
                if not row:
                    continue
                seen_header = True
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
```

- [ ] **Step 4: Run the full test suite to verify it passes**

Run: `python -m unittest test_csv_dupcounter -v`

Expected: all Task 1, 2, and 3 tests PASS.

- [ ] **Step 5: Commit (skipped unless the user asks for version control)**

```bash
git add csv_dupcounter.py test_csv_dupcounter.py
git commit -m "feat: add duplicate counting and full-csv export"
```

---

## Task 4: CLI wiring

**Files:**
- Modify: `csv_dupcounter.py`
- Modify: `test_csv_dupcounter.py`

**Interfaces:**
- Consumes: every function from Tasks 1–3.
- Produces:
  - `default_output_path(input_path: str) -> str`
  - `confirm(question="Proceed? [Y/n] ", input_func=input, out=print) -> bool`
  - `build_parser() -> argparse.ArgumentParser`
  - `main(argv=None) -> int` — 0 on success or cancel, 1 on a user-facing error.

- [ ] **Step 1: Write the failing tests**

Append to `test_csv_dupcounter.py`, above the `if __name__ == "__main__":` block:

```python
class CliTests(BaseTestCase):
    def test_default_output_path(self):
        self.assertEqual(
            app.default_output_path(os.path.join("data", "orders.csv")),
            os.path.join(os.path.abspath("data"), "orders_dupcount.csv"))

    def test_default_output_path_without_extension(self):
        self.assertTrue(
            app.default_output_path(os.path.join("data", "orders")).endswith("orders_dupcount.csv"))

    def test_confirm_accepts_blank_as_yes(self):
        self.assertTrue(app.confirm(input_func=lambda prompt: ""))

    def test_confirm_accepts_yes_and_no(self):
        self.assertTrue(app.confirm(input_func=lambda prompt: "y"))
        self.assertFalse(app.confirm(input_func=lambda prompt: "n"))

    def test_confirm_reasks_on_garbage(self):
        answers = iter(["maybe", "y"])
        self.assertTrue(app.confirm(input_func=lambda prompt: next(answers),
                                    out=lambda line: None))

    def test_main_reports_missing_file(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = app.main([self.path("nope.csv")])
        self.assertEqual(code, 1)
        self.assertIn("no such file", buffer.getvalue())

    def test_main_reports_empty_file(self):
        target = self.path("empty.csv")
        open(target, "wb").close()
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = app.main([target])
        self.assertEqual(code, 1)
        self.assertIn("is empty", buffer.getvalue())

    def test_main_rejects_a_directory(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = app.main([self.tmp.name])
        self.assertEqual(code, 1)
        self.assertIn("is a directory", buffer.getvalue())

    def test_main_end_to_end(self):
        source = self.write_csv("orders.csv", ORDERS)
        output = self.path("result.csv")
        answers = iter(["city,item", "y"])
        buffer = io.StringIO()
        with mock.patch("builtins.input", side_effect=answers):
            with contextlib.redirect_stdout(buffer):
                code = app.main([source, output, "--encoding", "cp1252"])
        self.assertEqual(code, 0)
        rows = self.read_csv(output)
        self.assertEqual(
            rows[0], ["Dup_Count", "City", "Item", "OrderID", "Customer", "Qty"])
        self.assertEqual([row[0] for row in rows[1:]], ["3", "3", "0", "3", "3"])
        self.assertIn("2 distinct key combinations", buffer.getvalue())

    def test_main_cancels_on_blank_selection(self):
        source = self.write_csv("orders.csv", ORDERS)
        output = self.path("result.csv")
        buffer = io.StringIO()
        with mock.patch("builtins.input", side_effect=[""]):
            with contextlib.redirect_stdout(buffer):
                code = app.main([source, output, "--encoding", "cp1252"])
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(output))
        self.assertIn("Cancelled", buffer.getvalue())
```

The test file's import block must also gain `contextlib`, `io`, and `from unittest import mock`. Add those now:

```python
import codecs
import contextlib
import csv
import io
import os
import sys
import tempfile
import unittest
from unittest import mock
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m unittest test_csv_dupcounter.CliTests -v`

Expected: FAIL — `AttributeError: module 'csv_dupcounter' has no attribute 'default_output_path'`.

- [ ] **Step 3: Implement the CLI**

Insert into `csv_dupcounter.py`, immediately after the `write_output` function and before the `if __name__ == "__main__":` guard:

```python
# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def default_output_path(input_path):
    """<input>_dupcount.csv next to the input file."""
    directory = os.path.dirname(os.path.abspath(input_path))
    stem, extension = os.path.splitext(os.path.basename(input_path))
    if not extension:
        extension = ".csv"
    return os.path.join(directory, stem + "_dupcount" + extension)


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
        encoding = resolve_encoding(input_path, args.encoding)
        delimiter = detect_dialect(input_path, encoding)
        header = read_header(input_path, encoding, delimiter)
        output_path = args.output or default_output_path(input_path)
    except AppError as exc:
        print("Error: %s" % exc)
        return 1

    print("Opened %s" % input_path)
    print("  encoding : %s" % encoding)
    print("  delimiter: %s" % delimiter)
    print("  columns  : %d" % len(header))
    print()
    print_columns(header)
    print()
    print("Select columns to group by, in order.")
    print("  - numbers:  3,1,5")
    print("  - names:    city,item")
    print("  - commands: list (show columns again), all (every column), blank (cancel)")
    print()

    try:
        # `input` is resolved here, at call time, so tests can patch builtins.input.
        indices = prompt_for_selection(header, input_func=input, out=print)
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

    try:
        if not confirm(input_func=input, out=print):
            print("Cancelled.")
            return 0
    except (EOFError, KeyboardInterrupt):
        print()
        print("Cancelled.")
        return 0

    print()
    try:
        counts = count_groups(input_path, encoding, delimiter, indices)
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
```

Replace the trailing guard:

```python
if __name__ == "__main__":
    sys.exit("csv_dupcounter: not complete yet")
```

with:

```python
if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the full test suite to verify it passes**

Run: `python -m unittest test_csv_dupcounter -v`

Expected: all tests PASS.

- [ ] **Step 5: Commit (skipped unless the user asks for version control)**

```bash
git add csv_dupcounter.py test_csv_dupcounter.py
git commit -m "feat: wire up the interactive command line"
```

---

## Task 5: README and end-to-end verification

**Files:**
- Create: `README.md`

**Interfaces:**
- Consumes: `csv_dupcounter.py` (all tasks).
- Produces: `README.md` — usage documentation.

- [ ] **Step 1: Write a real CSV fixture on disk with cp1252 bytes**

Run:

```bash
python - <<'PY'
import csv
rows = [
    ["OrderID", "Customer", "City", "Item", "Qty"],
    ["1001", "Acme", "Lisbon", "Widget", "2"],
    ["1002", "Globex", "Lisbon", "Widget", "5"],
    ["1003", "Acme", "Lisbon", "Gadget", "1"],
    ["1004", "Initech", "Lisbon", "Widget", "3"],
    ["1005", "Acme", "Lisbon", "Widget", "7"],
]
with open("/workspace/demo_orders.csv", "w", encoding="cp1252", newline="") as fh:
    csv.writer(fh).writerows(rows)
print("wrote /workspace/demo_orders.csv")
PY
```

Expected: `wrote /workspace/demo_orders.csv`.

- [ ] **Step 2: Run the application end to end with piped input**

Run:

```bash
cd /workspace && printf 'city,item\ny\n' | python csv_dupcounter.py demo_orders.csv demo_out.csv --encoding cp1252
```

Expected output (the `> ` prompt is written without a trailing newline, so it runs into the next line):

```
Opened demo_orders.csv
  encoding : cp1252
  delimiter: ,
  columns  : 5

Columns:
  1. OrderID
  2. Customer
  3. City
  4. Item
  5. Qty

Select columns to group by, in order.
  - numbers:  3,1,5
  - names:    city,item
  - commands: list (show columns again), all (every column), blank (cancel)

> 
Grouping by 2 columns, in this order: City, Item
Output layout: Dup_Count, City, Item, OrderID, Customer, Qty
Proceed? [Y/n] 
Read 5 data rows from demo_orders.csv
2 distinct key combinations; 1 group contains duplicates
Wrote 5 rows to demo_out.csv
Done.
```

- [ ] **Step 3: Verify the exported file**

Run: `cat /workspace/demo_out.csv`

Expected:

```
Dup_Count,City,Item,OrderID,Customer,Qty
3,Lisbon,Widget,1001,Acme,2
3,Lisbon,Widget,1002,Globex,5
0,Lisbon,Gadget,1003,Acme,1
3,Lisbon,Widget,1004,Initech,3
3,Lisbon,Widget,1005,Acme,7
```

- [ ] **Step 4: Verify the output bytes are cp1252-encoded with CRLF line endings**

Run:

```bash
cd /workspace && python -c "
import codecs
raw = open('demo_out.csv','rb').read()
print('CRLF line endings:', b'\r\n' in raw)
print('decodes as cp1252:', bool(raw.decode('cp1252')))
print('first line:', raw.split(b'\r\n')[0].decode('cp1252'))
"
```

Expected:

```
CRLF line endings: True
decodes as cp1252: True
first line: Dup_Count,City,Item,OrderID,Customer,Qty
```

(The fixture is pure ASCII, so cp1252 and UTF-8 coincide byte-for-byte here. The dedicated
`test_cp1252_round_trip` test covers the non-ASCII case by asserting the output bytes decode as
cp1252 and do *not* match the UTF-8 encoding.)

- [ ] **Step 5: Write the README**

Create `README.md` with this exact content:

```markdown
# CSV Duplicate Counter

Looks at a CSV file, shows you the column names, lets you pick the columns you
care about **in the order you want them**, and writes a new CSV with a
`Dup_Count` column telling you how many duplicates each row has.

## Requirements

Python 3.8 or newer. Nothing to install — standard library only.

## Running it

```
python csv_dupcounter.py <input.csv> [output.csv] [--encoding CODEPAGE]
```

| Argument | Meaning |
|---|---|
| `<input.csv>` | The file to examine. Required. |
| `[output.csv]` | Where to write the result. Defaults to `<input>_dupcount.csv` beside the input. |
| `--encoding` | Override the encoding, e.g. `cp1252` or `utf-8`. Usually unnecessary. |

Example:

```
python csv_dupcounter.py orders.csv
```

## Choosing columns

You get a numbered list of the columns. Type the ones you want, in the order
you want them:

```
> 3,1,5        by number
> city,item    by name (case-insensitive)
> 1,item       numbers and names can be mixed
> all          every column, in file order
> list         show the column list again
> <Enter>      cancel without writing anything
```

Names and numbers can be separated with commas, spaces, or semicolons.

The order you type is the order the columns appear in the output.

## What the output looks like

The output contains every original column plus one new column, `Dup_Count`:

- `Dup_Count` is the number of **extra copies** of that row — the group size
  minus one. A row with `Dup_Count = 0` is unique. A row with `Dup_Count = 3`
  has three other rows sharing its key combination.
- Every row in the same group carries the same count.
- Column order: `Dup_Count`, then the columns you chose in your order, then
  all remaining columns in the order they appeared in the original file.
- Rows stay in their original order.

Example — source:

```
OrderID,Customer,City,Item,Qty
1001,Acme,Lisbon,Widget,2
1002,Globex,Lisbon,Widget,5
1003,Acme,Lisbon,Gadget,1
```

Choosing `City` and `Item` gives:

```
Dup_Count,City,Item,OrderID,Customer,Qty
1,Lisbon,Widget,1001,Acme,2
1,Lisbon,Widget,1002,Globex,5
0,Lisbon,Gadget,1003,Acme,1
```

In Excel: sort by `Dup_Count` descending to bring the duplicates to the top,
then filter on `Dup_Count > 0` to see only the rows you might want to review.

## Encoding

The file is read and written with the **same** encoding, so accented characters
survive the round trip:

1. `--encoding` if you pass it.
2. Otherwise, if the file starts with a UTF-8 BOM, it is treated as UTF-8.
3. Otherwise the Windows ANSI code page is used — `cp1252` on most machines,
   `cp932` on Japanese ones, and so on.
4. Otherwise the system's preferred encoding.

The output file uses the same encoding as the input and the same delimiter, so
it opens in Excel exactly like the original.

Bytes that cannot be represented in the chosen encoding become `?` instead of
causing an error. Your original file is never modified.

## Tests

```
python test_csv_dupcounter.py
```
```

- [ ] **Step 6: Run the full test suite one final time**

Run: `cd /workspace && python -m unittest test_csv_dupcounter -v`

Expected: every test PASS, no errors.

- [ ] **Step 7: Clean up the demo fixture**

Run: `rm -f /workspace/demo_orders.csv /workspace/demo_out.csv`

Expected: no output. Keep `demo_orders.csv` and `demo_out.csv` if the user wants to try it immediately — in that case skip this step.

- [ ] **Step 8: Commit (skipped unless the user asks for version control)**

```bash
git add README.md
git commit -m "docs: add usage readme"
```

---

## Verification Summary

After Task 5, all of the following must be true:

| Check | Command | Expected |
|---|---|---|
| Full suite passes | `python -m unittest test_csv_dupcounter -v` | all PASS |
| App runs end to end | `printf 'city,item\ny\n' \| python csv_dupcounter.py demo_orders.csv demo_out.csv --encoding cp1252` | summary printed, file written |
| Output layout correct | `cat demo_out.csv` | header `Dup_Count,City,Item,OrderID,Customer,Qty` |
| Count math correct | same | `3,3,0,3,3` in the first column |
| No third-party imports | `grep -E "^import |^from " csv_dupcounter.py` | stdlib only |
| Input file untouched | compare file hash before/after | unchanged |
