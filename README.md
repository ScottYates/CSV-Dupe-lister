# CSV Duplicate Counter

Looks at a CSV file, shows you the column names, lets you pick the columns you
care about **in the order you want them**, and writes a new CSV with a
`Dup_Count` column telling you how many duplicates each row has.

## Requirements

Python 3.8 or newer. Nothing to install — standard library only.

## Running it

```
python csv_dupcounter.py <input.csv> [output.csv] [--encoding CODEPAGE] [--delimiter CHAR]
```

| Argument | Meaning |
|---|---|
| `<input.csv>` | The file to examine. Required. |
| `[output.csv]` | Where to write the result. Defaults to `dupes-<input name>` beside the input — `orders.csv` → `dupes-orders.csv`. Must not be the input file. An existing output file is overwritten. |
| `--encoding` | Override the encoding, e.g. `cp1252` or `utf-8`. Usually unnecessary. |
| `--delimiter` | Override the detected delimiter, e.g. `--delimiter ";"`. Use when the note below appears. |

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
  up/down move   space toggle   enter confirm   esc cancel

     1. OrderID
     2. Customer
 >   3. City        [1]
     4. Item        [2]
     5. Qty

Order: City, Item
Space toggles a column in or out; the number in brackets is its place in the order.
```

| Key | Action |
|---|---|
| Up / Down (or `k` / `j`) | Move the cursor |
| Home / End | Jump to the first / last column |
| Page Up / Page Down | Move ten rows |
| Space | Toggle the current column in or out |
| Enter | Confirm and write the file |
| Escape (or Ctrl+C) | Cancel, nothing is written |

The number in brackets is where that column sits in your selection, so you can see the order you
are building. Toggling a column again removes it.

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

The output is written next to the input as `dupes-<input name>`. If a file with
that name already exists it is overwritten, and the app tells you when it does
that. It contains every original column plus one new column, `Dup_Count`:

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
