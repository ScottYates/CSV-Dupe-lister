# CSV Duplicate Counter

Looks at a CSV file, shows you the column names, lets you pick the columns you
care about **in the order you want them**, and writes a new CSV with a
`Dup_Count` column telling you how many duplicates each row has.

## Requirements

Python 3.8 or newer. Nothing to install — standard library only.

## Running it

```
python csv_dupcounter.py <input.csv> [output.csv] [--encoding CODEPAGE]
                          [--delimiter CHAR] [--exclude COLUMNS] [--combined]
                          [--separator TEXT]
```

| Argument | Meaning |
|---|---|
| `<input.csv>` | The file to examine. Required. |
| `[output.csv]` | Where to write the result. Defaults to `<input name>-dupes.<ext>` beside the input — `orders.csv` → `orders-dupes.csv`. Must not be the input file. An existing output file is overwritten. |
| `--encoding` | Override the encoding, e.g. `cp1252` or `utf-8`. Usually unnecessary. |
| `--delimiter` | Override the detected delimiter, e.g. `--delimiter ";"`. Use when the note below appears. |
| `--exclude` | Columns to leave out of the output entirely, by name or number: `--exclude "Notes,4"`. |
| `--combined` | Add the `Dup_Key` column (same as pressing `c` in the picker). |
| `--separator` | The separator used inside `Dup_Key`. Defaults to `" -:- "`. |

Example:

```
python csv_dupcounter.py orders.csv
```

## Choosing columns

On a terminal you get an arrow-key picker:

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
| Up / Down (or `k` / `j`) | Move the cursor |
| Home / End | Jump to the first / last column |
| Page Up / Page Down | Move ten rows |
| Space | Add or remove the current column from the grouping keys |
| Delete (or `x`) | Drop the current column from the output entirely, or bring it back |
| `c` | Switch the combined `Dup_Key` field on or off |
| Enter | Confirm and write the file |
| Escape (or Ctrl+C) | Cancel, nothing is written |

The number in brackets is where that column sits in your grouping order, so you can see the order
you are building. `[-]` marks a column that will be left out of the output. A column cannot be both
a grouping key and removed — deleting one unselects it, selecting one brings it back.

If the input is piped rather than typed at a terminal, you get the numbered list and can type your
selection instead:

```
> 3,1,5        by number
> city,item    by name (case-insensitive)
> 1,item       numbers and names can be mixed
> order date   a name containing a space works too
> (column 4)   a column with a blank header
> all          every column, in file order
> list         show the column list again
> <Enter>      cancel without writing anything
```

Commas and semicolons separate the columns. A segment that is not an exact column name is split
on whitespace, so `3 1 5` works too.

The order you give is the order the columns appear in the output.

## What the output looks like

The output is written next to the input as `<input name>-dupes.<ext>`. If a
file with that name already exists it is overwritten, and the app tells you when
it does that. It contains every original column except any you removed, plus
the new columns:

- `Dup_Count` starts at **0** for the first row of each key combination and
  rises for every further row that shares it. The first row is the original
  (`0`), the next is `1`, then `2`, and so on — so filtering `Dup_Count > 0`
  selects exactly the duplicate rows and leaves the original in place.
- `Dup_Key` (only when switched on with `c`) holds the count and your chosen
  field values joined by the separator: `2 -:- Lisbon -:- Widget`. The
  separator is configurable with `--separator`.
- Column order: `Dup_Count`, `Dup_Key`, then the columns you chose in your
  order, then all remaining columns in the order they appeared in the original
  file.
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
0,Lisbon,Widget,1001,Acme,2
1,Lisbon,Widget,1002,Globex,5
0,Lisbon,Gadget,1003,Acme,1
2,Lisbon,Widget,1004,Initech,3
3,Lisbon,Widget,1005,Acme,7
```

`Lisbon`/`Widget` appears four times, so those rows are numbered 0, 1, 2, 3 —
the 0 is the original, the other three are duplicates.

In Excel: filter on `Dup_Count > 0` to see only the rows you might want to
review.

## Encoding

The file is read and written with the **same** encoding, so accented characters
survive the round trip:

1. `--encoding` if you pass it.
2. Otherwise, if the file starts with a UTF-8 BOM, it is treated as UTF-8.
3. Otherwise the Windows ANSI code page is used — `cp1252` on most machines,
   `cp932` on Japanese ones, and so on.
4. Otherwise the system's preferred encoding.

Step 3 is what happens on Windows. On Linux or macOS there is no ANSI code
page, so step 4 applies (usually UTF-8) — pass `--encoding cp1252` if you need
a specific code page there.

The output file uses the same encoding as the input and the same delimiter, so
it opens in Excel exactly like the original.

Bytes that cannot be represented in the chosen encoding become `?` instead of
causing an error. Your original file is never modified — and the output path is
refused outright if it would overwrite the input.

## Delimiter

The delimiter is detected from the file itself: `,` `;` tab and `|` are all
recognised. The detected one is printed when the file opens.

If your values contain the delimiter often enough to be mistaken for a real
one, more than one candidate will fit and the app says so:

```
  delimiter: ,
  note     : ',', ';' would all fit this file; if the columns look wrong, re-run with --delimiter
```

In that case, force the right one:

```
python csv_dupcounter.py orders.csv --delimiter ";"
```

## Tests

```
python test_csv_dupcounter.py
```
