# CSV Duplicate Counter — Design Spec

**Date:** 2026-11-24
**Status:** Approved for implementation
**Deliverable:** A standalone Python CLI application that inspects a CSV file, lets the user pick
columns in a chosen order, and exports a full CSV containing a duplicate-count column computed
from those chosen columns.

---

## 1. Problem

A user has a CSV file and needs to know which rows are duplicates of each other, based on a set of
columns they pick. The output must be a complete CSV — the original rows plus a count — so it can be
opened in Excel and filtered or sorted.

Two constraints shape the solution:

1. The user works on Windows and the source files use the **Windows ANSI code page** (e.g. `cp1252`),
   not UTF-8. Opening and saving with any other encoding corrupts accented characters.
2. The tool runs on a machine where the user cannot or will not install packages. Standard library only.

## 2. Goals

- Display the list of column names from the input CSV.
- Let the user select columns **in a specific order**; that order is preserved in the output.
- Export a **full** CSV (every original column retained) with a duplicate-count column added.
- Use the Windows ANSI code page for both reading and writing.
- Zero third-party dependencies. Single self-contained file the user can copy to a Windows machine.
- Handle real-world CSV quirks without crashing: alternate delimiters, BOMs, ragged rows, stray bytes.

## 3. Non-goals

- No GUI. The interface is an interactive command line.
- No editing, de-duplication, or deletion of rows. The tool *reports* counts; it never rewrites the
  user's data in place.
- No fuzzy or case-insensitive matching. Grouping is exact string equality on the selected values.
- No support for `.xlsx`, `.tsv` inputs, or multi-file batches.

## 4. User-facing behavior

### 4.1 Invocation

```
python csv_dupcounter.py <input.csv> [output.csv] [--encoding CODEPAGE]
                          [--delimiter CHAR] [--exclude COLUMNS] [--combined]
                          [--separator TEXT]
```

- `input.csv` — required.
- `output.csv` — optional. Default: `<input name>-dupes.<ext>` in the input's directory — the
  original name with `-dupes` in front of the extension (`orders.csv` → `orders-dupes.csv`). The
  original extension is kept; a file with no extension gets `.csv`. **An existing output file is
  overwritten**, and the app says so when it happens. The output path must never be the input path.
- `--encoding` — optional override, e.g. `--encoding cp1252`, `--encoding utf-8`. Overrides all
  automatic detection.
- `--delimiter` — optional override for the detected delimiter, e.g. `--delimiter ";"`. Use it when
  the file's own values contain the delimiter often enough that detection is ambiguous.
- `--exclude COLUMNS` — columns to leave out of the output entirely, by name or number, e.g.
  `--exclude "Notes,4"`. This is the script equivalent of the picker's delete key.
- `--combined` — add the `Dup_Key` column. This is the script equivalent of the picker's `c` key.
- `--separator TEXT` — the separator used inside `Dup_Key`. Defaults to `" -:- "`.

### 4.2 Interaction sequence

The primary interface is an **arrow-key column picker**. When the input and output are both real
terminals, the app switches the terminal into a mode where each key arrives immediately, draws the
column list itself, and reacts to the keys directly:

```
Opened orders.csv
  encoding : cp1252
  delimiter: ,
  columns  : 5

Select columns to group by, in order.
  up/down move   space group   del remove   c combined   enter confirm   esc cancel

     1. OrderID
     2. Customer
 >   3. City        [1]
     4. Item        [2]
     5. Qty         [-]

Order: City, Item
Removed from the output: Qty
Combined field: on  (Dup_Key, joined by ' -:- ')
[n] is the column's place in the grouping order, [-] means it is left out of the output.
```

| Key | Action |
|---|---|
| Up / Down (or `k` / `j`) | Move the cursor one row |
| Home / End | Jump to the first / last column |
| Page Up / Page Down | Move ten rows |
| Space | Add or remove the current column from the grouping keys |
| Delete (or `x`) | Remove the current column from the output entirely, or bring it back |
| `c` | Switch the combined `Dup_Key` field on or off |
| Enter | Confirm and proceed to the export |
| Escape (or Ctrl+C / Ctrl+D) | Cancel; nothing is written |

The number in brackets is the column's **position in the grouping order**, so the picker shows the
ordering the user is building, not just which columns are on. `[-]` marks a column that will be left
out of the output. Pressing Enter with nothing selected shows "Pick at least one column to group
by." rather than doing nothing.

Enter confirms the selection *and* proceeds — the arrow-key picker needs no second confirmation
prompt, because the selection is already visible on screen as it is being built.

**Fallback.** When stdin is not a terminal (piped input, a script, a scheduled job), the app prints
the numbered column list and accepts a typed selection instead:

```
Opened orders.csv
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

> city,item

Grouping by 2 columns, in this order: City, Item
Output layout: Dup_Count, City, Item, OrderID, Customer, Qty
Proceed? [Y/n] y

Read 5 data rows from orders.csv
2 distinct key combinations; 1 group contains duplicates
Wrote 5 rows to orders_dupcount.csv
Done.
```

Selection input rules (typed fallback):

- Commas and semicolons separate the columns. `3,1,5` and `3;1;5` are equivalent.
- Within a segment, a name matching a column exactly wins. This is what lets a column whose name
  contains a space (`Order Date`) be selected by name.
- A segment that is not an exact column name is split on whitespace, so `3 1 5` works too.
- Index tokens are 1-based and refer to the displayed column list.
- Name tokens match column names case-insensitively; a name that does not exist is an error naming the
  bad token.
- A token may mix indices and names.
- A blank header cell is selectable by its displayed label, `(column 4)`.
- `all` selects every column in file order. `list` and `all` are reserved words — a column with
  either name is reachable by its number.
- `list` reprints the column list and re-prompts.
- Empty input cancels the run cleanly (exit code 0, no file written).
- Duplicate tokens are rejected with a clear message — the order must be unambiguous.
- The confirmation prompt accepts `y`/`yes` (default) or `n`/`no`; anything else re-asks.

### 4.3 Output layout

Output column order is:

1. `Dup_Count` — the count column.
2. `Dup_Key` — the combined field, only when it is switched on.
3. The selected columns, **in the order the user selected them**.
4. All remaining original columns, in the original file's column order, minus any column the user
   removed with the delete key.

**Removing a column.** The delete key drops a column from the output entirely — its values are not
written at all. A column cannot be both a grouping key and removed: deleting one that was selected
also unselects it, and selecting one that was deleted brings it back. Deleted columns keep their
original place in the file, so the remaining columns stay in their original order around the gap.

**The combined field.** When switched on, `Dup_Key` holds the count and the selected field values
joined by the separator: with `City` and `Item` chosen and a count of 2, it reads
`2 -:- Lisbon -:- Widget`. The count comes first, then each selected field in the order the user
picked them. The separator defaults to `" -:- "` and is configurable with `--separator`.

Edge cases fixed here:

- If **every** column is selected, the output is `Dup_Count` followed by all columns in the chosen
  order. The "remaining" section is simply empty.
- If the source already contains a column named `Dup_Count`, the count column is still emitted first
  and the source column keeps its original name in the remaining section. A one-line warning is
  printed so the resulting duplicate header is not a surprise.
- A source column with a blank header cell keeps that blank header in the output. In the interactive
  prompt it is displayed as `(column 4)` so it can still be selected by index or by that label.

### 4.4 Duplicate-count semantics

The count starts at **0** for the first row of each key combination and rises for every further row
that shares it. A group is the set of rows whose selected column values are exactly equal.

- The first row of a group gets `Dup_Count = 0` — it is the original.
- The second row of that group gets `Dup_Count = 1` — one record after the first.
- The third gets `2`, the fourth `3`, and so on.

So the count says *how many records after the first this row is*, and it increases as more
duplicates are found. The rows worth reviewing are exactly those with a count above zero: filtering
`Dup_Count > 0` in Excel selects the duplicate rows and leaves the original in place, whereas a
group-size count would mark every row in the group, including the one you want to keep.

Because the count depends on the order of the rows within a group, the input order is preserved in
the output.

### 4.5 Worked example

Input `orders.csv` (cp1252):

```
OrderID,Customer,City,Item,Qty
1001,Acme,Lisbon,Widget,2
1002,Globex,Lisbon,Widget,5
1003,Acme,Lisbon,Gadget,1
1004,Initech,Lisbon,Widget,3
1005,Acme,Lisbon,Widget,7
```

User selects `City, Item`. Groups: `(Lisbon, Widget)` → rows 1001, 1002, 1004, 1005 (size 4);
`(Lisbon, Gadget)` → row 1003 (size 1).

Output `orders_dupcount.csv`:

```
Dup_Count,City,Item,OrderID,Customer,Qty
3,Lisbon,Widget,1001,Acme,2
3,Lisbon,Widget,1002,Globex,5
0,Lisbon,Gadget,1003,Acme,1
3,Lisbon,Widget,1004,Initech,3
3,Lisbon,Widget,1005,Acme,7
```

Row order is preserved from the input. Note `Dup_Count = 3` on four rows: each of those rows has three
*other* rows sharing its `City`+`Item` key.

## 5. Encoding strategy

Resolution order, first match wins:

1. `--encoding CODEPAGE` if supplied on the command line.
2. UTF-8 BOM detection: if the file's first three bytes are `EF BB BF`, use `utf-8-sig`. A BOM is an
   explicit encoding declaration by the writer, so it is honored rather than guessed at.
3. Windows ANSI code page: `ctypes.windll.kernel32.GetACP()` → e.g. `1252` → Python codec `cp1252`.
4. `locale.getpreferredencoding()` as the fallback for non-Windows or a failed `GetACP()` call.

Both reading and writing use the resolved codec with `errors="replace"`, so a byte that is undefined
in the code page (cp1252 has five such bytes) becomes `?` instead of raising `UnicodeDecodeError` or
`UnicodeEncodeError` mid-run.

The Windows ANSI code page is a Windows guarantee. Off Windows — Linux, macOS — step 3 cannot run, so
the locale's preferred encoding is used instead (usually UTF-8). The `--encoding` flag makes the
behaviour explicit on any platform.

Note on round-tripping: `errors="replace"` means unencodable characters are lossy. This is a
deliberate trade-off — a run that completes with a few `?` characters is more useful than a crash.
Files read with `errors="replace"` cannot be perfectly restored; the original input file is never
modified.

## 6. CSV dialect detection

The delimiter is detected from the first 64 KB of decoded text.

1. **`csv.Sniffer` (primary).** The stdlib Sniffer is given the candidate set `,` `;` `\t` `|` and
   asked to sniff. It weighs how consistent each candidate is against how plausible the resulting
   fields look, which is what identifies the real delimiter when a value happens to contain the
   other candidate often enough to look consistent. Passing the candidate set explicitly is also
   what lets it detect `|`, which it would not otherwise consider. The result is rejected unless
   that character actually appears in the header line.
2. **Consistency scoring (fallback).** If the Sniffer raises or is rejected, each candidate is
   scored by splitting the first ~50 non-blank lines and taking the most common field count (the
   *mode*). A candidate qualifies when that mode covers at least 80% of the lines and the width is
   greater than 1; the widest qualifying candidate wins, ties breaking on the candidate order.
3. **`,`** if that also fails.

**Ambiguity is reported, not hidden.** When more than one candidate splits the file consistently,
the file is genuinely ambiguous — a value contains the other delimiter often enough to be
indistinguishable from a real one. The app prints a note naming the candidates and tells the user
to re-run with `--delimiter`. The chosen delimiter is always displayed in the header block, so a
wrong guess is visible immediately rather than silently corrupting the output. The output file is
written with the **same delimiter as the input**, so the export opens correctly in Excel with the
same layout as the source.

## 7. Architecture

One file, `csv_dupcounter.py`. Internally decomposed into small functions with one responsibility
each, so each can be tested in isolation.

| Function | Responsibility |
|---|---|
| `resolve_encoding(path, override)` | Apply the resolution order in §5. Returns a codec name. |
| `detect_dialect(path, encoding)` | Return the delimiter character. |
| `consistent_delimiters(path, encoding)` | Every candidate that splits the file consistently — more than one means the file is ambiguous. |
| `read_header(path, encoding, delimiter)` | Return the list of column names exactly as stored, minus a leading BOM character. Names are never stripped or renamed. |
| `parse_selection(text, columns)` | Turn user input into an ordered list of 0-based indices. Raise `SelectionError` with a readable message on bad input. |
| `parse_column_list(text, columns)` | Resolve a separated list of names or indices to indices — used by `--exclude`. |
| `Selection` | What the user picked: the grouping columns, the removed columns, and the combined flag. |
| `pick_columns(columns, use_arrow_keys, context)` | Interactive chooser. Uses the arrow-key picker on a terminal, the typed prompt otherwise. |
| `ColumnPicker` | Draws the column list and handles up/down/space/enter/escape. |
| `KeyReader` | Reads single keypresses including the arrow keys, on Windows (`msvcrt`) and POSIX (`termios`/`select`). |
| `write_output(...)` | One pass over the source: write `Dup_Count`, the optional `Dup_Key`, the selected columns and the remaining ones. Returns an `ExportStats`. |
| `ExportStats` | Rows written, distinct key combinations, groups with duplicates, rows with a count above 0. |
| `build_output_order(header, indices)` | Compute the final column order. |
| `_same_file(first, second)` | True when two paths point at the same file — used to refuse writing over the input. |
| `main(argv)` | Parse args, orchestrate, print the summary. |

### 7.1 Memory strategy

`write_output` makes a single pass over the input file rather than loading it into memory. Rows are
read one at a time and written straight out; the only thing kept is
`{tuple_of_selected_values: how_many_times_seen}`, which tracks both the count written to each row
and the group statistics for the summary. Memory is proportional to the number of *distinct key
combinations*, not the number of rows.

A 500 MB CSV therefore never has to fit in RAM.

A single pass is also what makes the count semantics natural: the count of a row is simply how many
rows with that key have already been written, so the first row of a group is 0, the second 1, and so
on, in input order.

### 7.2 Row handling

Rows are read as lists, not dicts, so that duplicate column names in the header cannot cause a column
to be silently overwritten. A row shorter than the header is padded with empty strings for missing
trailing fields; a row longer than the header has its extra fields appended at the end of the output
row rather than dropped. Header names are never used as keys — only indices are — so duplicate header
names are harmless.

Blank lines are not data. A blank line before the header is skipped, and a blank line after the header
is skipped too — a trailing blank line is a common artefact of an Excel export and would otherwise
become an empty data row with a duplicate count of its own, inflating both the row count and the
group count in the summary. `read_header` and the row iterator use the same definition of a blank row
(an empty row, or one whose cells are all whitespace), so they always agree on which row is the
header.

### 7.3 Module map

```
csv_dupcounter.py          the application (stdlib only)
test_csv_dupcounter.py     unittest suite, runnable with `python test_csv_dupcounter.py`
README.md                  usage notes
```

### 7.4 Terminal handling

The arrow-key picker needs raw keypresses, which the two target platforms deliver differently:

- **Windows** — `msvcrt.getwch()` returns the arrow keys as a two-byte sequence: a leading `\x00`
  or `\xe0` followed by a scan code (`H` up, `P` down, `G` home, `O` end, `I` page up, `Q` page
  down). ANSI escape sequences are drawn only if the console has virtual terminal processing
  enabled, which the app turns on itself with `SetConsoleMode`; if that fails, the app falls back to
  the typed prompt rather than drawing a broken screen.
- **POSIX** — the terminal is put into `cbreak` mode with `termios` (not raw, so Ctrl+C still
  raises `KeyboardInterrupt`), and bytes are read with `os.read`. An escape sequence is recognised
  by reading ahead with `select` under a short timeout, so a lone Escape is not confused with the
  start of a sequence.

The screen is redrawn in place with ANSI cursor-home and clear-to-end sequences, and the cursor is
hidden while the picker is active and restored afterwards even if the run is interrupted. On
Windows the console is also restored by `KeyReader.__exit__` when the context manager unwinds.

The picker is skipped entirely when stdin or stdout is not a terminal, which is what makes the
typed fallback — and therefore scripted use — possible.

## 8. Error handling

| Condition | Behavior |
|---|---|
| Input file missing | `Error: no such file: <path>` and exit 1 |
| Input is a directory | `Error: <path> is a directory, not a CSV file` and exit 1 |
| File is empty (0 bytes) | `Error: <path> is empty` and exit 1 |
| File has no header row | `Error: no header row found in <path>` and exit 1 |
| Output path is the input path | `Error: the output file must be different from the input file` and exit 1, before any prompt |
| Selected column name not found | `Error: unknown column 'xyz'` and re-prompt |
| `--exclude` names a column that does not exist | `Error: unknown column 'xyz'` and exit 1 |
| `--exclude` names two columns with the same name | `Error: column 'K' is ambiguous (2 matches) - use its number instead` and exit 1 |
| Out-of-range index | `Error: column 99 does not exist (file has 5 columns)` and re-prompt |
| Duplicate token in selection | `Error: 'City' selected more than once` and re-prompt |
| Two columns share the typed name | `Error: column 'K' is ambiguous (2 matches) - use its number instead` and re-prompt |
| Blank selection | Cancel: print `Cancelled.` and exit 0 without writing |
| Output path unwritable | `Error: cannot write output: <path>` and exit 1 |
| Input unreadable mid-run | `Error: cannot read input: <path>` and exit 1 |
| Undecodable bytes | Replaced with `?` — run continues |

Every user-facing failure is a single clear line. No traceback reaches the user except for genuinely
unexpected bugs.

**The output path may never be the input path.** Writing the output truncates the file it is about to
re-read for pass 2, so the same path would silently destroy the input. The check compares both the
absolute path and the file identity (`os.path.samefile`), so it also catches a symlink or a
differently-spelled path to the same file. The check runs before the user is prompted for anything.

## 9. Testing plan

`test_csv_dupcounter.py` uses plain `unittest` (no pytest dependency) and runs with
`python test_csv_dupcounter.py`. It writes fixtures to a temp directory at runtime.

Coverage:

1. **Count math** — group of 4 → `3`; group of 1 → `0`; extra copies, not group size.
2. **Output column order** — `Dup_Count` first, then selected columns in the *chosen* order, then the
   remaining columns in original file order.
3. **Selected order is honored** — selecting `Item, City` yields a different layout than `City, Item`.
4. **Row order preserved** — output rows appear in input order.
5. **cp1252 round-trip** — a file containing `ä ö ü ñ é` and `€` reads and writes without corruption.
6. **Delimiter sniffing** — semicolon-, tab-, and pipe-delimited files are detected correctly.
7. **UTF-8 BOM** — detected and stripped from the first header name.
8. **Undecodable byte** — does not raise; produces `?`.
9. **Empty file / header-only file** — raises the specified error, not a crash.
10. **Ragged rows** — short row padded, long row's extra field preserved.
11. **Selection parsing** — `3,1,5`, `3 1 5`, names, mixed, `all`, `list`; each error case raises
    `SelectionError` with a usable message.
12. **All columns selected** — output is `Dup_Count` plus every column in the chosen order, with an
    empty "remaining" section.
13. **Blank header cell** — displayed as `(column N)` in the prompt, preserved as a blank header in
    the output, and selectable by that label.
14. **Input immutability** — the input file is byte-identical after a full CLI run.
15. **Output may not be the input** — the run is refused, the file is untouched, exit code 1.
16. **Blank lines** — blank lines before and after the header are not exported as data rows and do
    not inflate the row or group counts.
17. **Whitespace-only leading line** — skipped, so the real header is found.
18. **Windows ANSI code page** — simulated with an injected `GetACP` returning 1252, 932, 936, 949,
    950, 874, 1250 and 1258; each maps to the matching `cpXXXX` codec, and `resolve_encoding`
    prefers it over the locale fallback.
19. **Delimiter ambiguity** — when two delimiters split the file consistently, both are reported and
    `--delimiter` settles it.
20. **Same delimiter out as in** — a semicolon-delimited input produces a semicolon-delimited output.
21. **Pre-existing `Dup_Count` column** — the count column is still first, the source column keeps its
    name, and a warning is printed.
22. **Duplicate header names** — both are preserved; index selection works; name selection reports
    ambiguity.
23. **Empty values** — blanks group together; a space is a different key from an empty string.
24. **Error message text** — the exact strings in §8 are asserted.
25. **Arrow-key picker** — space toggles, the order of picking is preserved, toggling twice removes
    a column, Enter with nothing selected does not exit, Escape cancels, Home/End jump to the ends.
26. **Picker rendering** — the picked order is shown and the bracket numbers mark each column's
    position in that order.
27. **Output naming** — the default output path is `dupes-<input name>`; an existing output file is
    overwritten and the app says so.
28. **End to end through a pseudo-terminal** — the real picker is driven with real arrow-key escape
    sequences over a pty, and the exported file is checked for correctness. The arrow-key, space,
    delete, `c` and enter paths, and the escape-cancels path, are all exercised this way.
29. **Count starts at zero** — the first row of a group is 0 and each further row rises by one.
30. **Deleted columns** — a column marked deleted is absent from the output header and from every
    row; deleting a grouping column also unselects it, and selecting a deleted column brings it back.
31. **Combined field** — `Dup_Key` holds the count and the chosen fields joined by the separator,
    with the count first and the fields in the order they were picked; the separator is configurable.
32. **Script equivalents** — `--exclude`, `--combined` and `--separator` produce the same result as
    the corresponding picker keys.

**Verification environment note:** the test suite runs on Linux in the development sandbox. The
`GetACP()` branch of `resolve_encoding` cannot execute there directly, so it is covered by injecting
a fake `ctypes` module and a patched `sys.platform` — this exercises the real code path, including the
`"cp%d" % acp` codec naming, for the code pages Windows actually uses. Every other code path,
including all `cp1252` encode/decode behavior, is verified against real files in the sandbox.

An independent adversarial audit of the first build found one high-severity defect (output path equal
to input path destroyed the file), three further deviations, and a set of untested behaviours; all of
the above were added in response and are covered by the tests listed here.

## 10. Out of scope for v1

- Progress reporting on very large files.
- Writing a summary sheet of which groups are duplicated.
- Sorting the output by count (the user sorts in Excel).
- Remembering the last selection between runs.
