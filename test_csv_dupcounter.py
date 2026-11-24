#!/usr/bin/env python3
"""Tests for csv_dupcounter. Run with: python test_csv_dupcounter.py"""

import codecs
import contextlib
import csv
import io
import os
import sys
import tempfile
import types
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

    def group_sizes(self, source, indices, encoding="cp1252", delimiter=","):
        """How many rows share each key combination, computed independently."""
        counts = {}
        for row in app.iter_data_rows(source, encoding, delimiter):
            key = tuple(row[i] if i < len(row) else "" for i in indices)
            counts[key] = counts.get(key, 0) + 1
        return counts

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
            handle.write(b"Name,City\n\x81Ana,Koln\n")
        encoding = app.resolve_encoding(target, "cp1252")
        header = app.read_header(target, encoding, ",")
        self.assertEqual(header, ["Name", "City"])


class DialectTests(BaseTestCase):
    def test_detects_comma_semicolon_tab_and_pipe(self):
        for delimiter in (",", ";", "\t", "|"):
            source = self.write_csv("delim%s.csv" % delimiter, ORDERS, delimiter=delimiter)
            encoding = app.resolve_encoding(source)
            self.assertEqual(app.detect_dialect(source, encoding), delimiter)

    def test_ambiguous_delimiter_is_reported(self):
        # A semicolon-delimited file whose values contain commas is genuinely
        # ambiguous: both ';' and ',' split every row consistently.
        rows = [["a,b", "c", "d"], ["1,2", "3", "4"], ["5,6", "7", "8"]]
        source = self.write_csv("mixed.csv", rows, delimiter=";")
        encoding = app.resolve_encoding(source)
        candidates = app.consistent_delimiters(source, encoding)
        self.assertIn(";", candidates)
        self.assertIn(",", candidates)

    def test_delimiter_override_settles_ambiguity(self):
        rows = [["a,b", "c", "d"], ["1,2", "3", "4"], ["5,6", "7", "8"]]
        source = self.write_csv("mixed.csv", rows, delimiter=";")
        header = app.read_header(source, "cp1252", ";")
        self.assertEqual(header, ["a,b", "c", "d"])
        counts = self.group_sizes(source, [0], delimiter=";")
        self.assertEqual(counts, {("1,2",): 1, ("5,6",): 1})

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


class ExportTests(BaseTestCase):
    def export(self, source, indices, encoding="cp1252", delimiter=",",
               deleted=(), combined=False, separator=" -:- "):
        """Run the same export that main() runs."""
        output = self.path("out.csv")
        header = app.read_header(source, encoding, delimiter)
        stats = app.write_output(
            source, output, header, encoding, delimiter, indices,
            deleted=deleted, combined=combined, separator=separator)
        return output, stats

    def test_count_starts_at_zero_and_rises_per_duplicate(self):
        source = self.write_csv("orders.csv", ORDERS)
        output, stats = self.export(source, [2, 3])
        rows = self.read_csv(output)
        self.assertEqual(stats.rows, 5)
        # (Lisbon, Widget) appears 4 times -> 0, 1, 2, 3. (Lisbon, Gadget) -> 0.
        self.assertEqual([row[0] for row in rows[1:]], ["0", "1", "0", "2", "3"])

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

    def test_deleted_columns_are_left_out_of_the_output(self):
        source = self.write_csv("orders.csv", ORDERS)
        output, _ = self.export(source, [2], deleted=[4, 1])
        rows = self.read_csv(output)
        self.assertEqual(rows[0], ["Dup_Count", "City", "OrderID", "Item"])
        self.assertEqual(rows[1], ["0", "Lisbon", "1001", "Widget"])

    def test_combined_field_joins_count_and_chosen_fields(self):
        source = self.write_csv("orders.csv", ORDERS)
        output, _ = self.export(source, [2, 3], combined=True)
        rows = self.read_csv(output)
        self.assertEqual(rows[0], ["Dup_Count", "Dup_Key", "City", "Item",
                                   "OrderID", "Customer", "Qty"])
        self.assertEqual(rows[1][1], "0 -:- Lisbon -:- Widget")
        self.assertEqual(rows[2][1], "1 -:- Lisbon -:- Widget")
        self.assertEqual(rows[3][1], "0 -:- Lisbon -:- Gadget")

    def test_combined_field_separator_is_configurable(self):
        source = self.write_csv("orders.csv", ORDERS)
        output, _ = self.export(source, [2, 3], combined=True, separator="::")
        rows = self.read_csv(output)
        self.assertEqual(rows[1][1], "0::Lisbon::Widget")

    def test_stats_report_duplicates(self):
        source = self.write_csv("orders.csv", ORDERS)
        _, stats = self.export(source, [2, 3])
        self.assertEqual(stats.rows, 5)
        self.assertEqual(stats.distinct_keys, 2)
        self.assertEqual(stats.duplicate_groups, 1)
        self.assertEqual(stats.duplicate_rows, 3)


class CliTests(BaseTestCase):
    def test_default_output_path(self):
        self.assertEqual(
            app.default_output_path(os.path.join("data", "orders.csv")),
            os.path.join(os.path.abspath("data"), "orders-dupes.csv"))

    def test_default_output_path_without_extension(self):
        self.assertTrue(
            app.default_output_path(os.path.join("data", "orders")).endswith("orders-dupes.csv"))

    def test_default_output_path_keeps_the_original_extension(self):
        self.assertTrue(
            app.default_output_path(os.path.join("data", "orders.TXT")).endswith("orders-dupes.TXT"))

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
        self.assertEqual([row[0] for row in rows[1:]], ["0", "1", "0", "2", "3"])
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


class RegressionTests(BaseTestCase):
    """Tests for the defects found in an independent audit of the first build."""

    def test_cli_does_not_modify_the_input(self):
        source = self.write_csv("orders.csv", ORDERS)
        output = self.path("out.csv")
        with open(source, "rb") as handle:
            before = handle.read()
        answers = iter(["city,item", "y"])
        with mock.patch("builtins.input", side_effect=answers):
            with contextlib.redirect_stdout(io.StringIO()):
                code = app.main([source, output, "--encoding", "cp1252"])
        self.assertEqual(code, 0)
        with open(source, "rb") as handle:
            self.assertEqual(handle.read(), before)

    def test_cli_refuses_to_overwrite_the_input(self):
        source = self.write_csv("orders.csv", ORDERS)
        with open(source, "rb") as handle:
            before = handle.read()
        buffer = io.StringIO()
        # Only one answer is queued: the program must refuse before prompting.
        with mock.patch("builtins.input", side_effect=["city,item"]):
            with contextlib.redirect_stdout(buffer):
                code = app.main([source, source, "--encoding", "cp1252"])
        self.assertEqual(code, 1)
        self.assertIn("must be different", buffer.getvalue())
        with open(source, "rb") as handle:
            self.assertEqual(handle.read(), before)

    def test_refusal_also_covers_a_relative_and_symlinked_path(self):
        source = self.write_csv("orders.csv", ORDERS)
        link = self.path("link.csv")
        os.symlink(source, link)
        buffer = io.StringIO()
        with mock.patch("builtins.input", side_effect=["city,item"]):
            with contextlib.redirect_stdout(buffer):
                code = app.main([source, link, "--encoding", "cp1252"])
        self.assertEqual(code, 1)
        self.assertIn("must be different", buffer.getvalue())

    def test_blank_lines_are_not_exported_as_data_rows(self):
        target = self.path("blanks.csv")
        with open(target, "w", encoding="cp1252", newline="") as handle:
            handle.write("A,B\r\n1,2\r\n\r\n3,4\r\n\r\n")
        output = self.path("out.csv")
        header = app.read_header(target, "cp1252", ",")
        counts = self.group_sizes(target, [0])
        stats = app.write_output(target, output, header, "cp1252", ",", [0])
        self.assertEqual(stats.rows, 2)
        self.assertEqual(len(counts), 2)
        rows = self.read_csv(output)
        self.assertEqual(len(rows), 3)
        self.assertEqual([row[0] for row in rows[1:]], ["0", "0"])

    def test_whitespace_only_leading_line_is_skipped(self):
        target = self.path("ws.csv")
        with open(target, "w", encoding="cp1252", newline="") as handle:
            handle.write("   \r\nA,B\r\n1,2\r\n")
        header = app.read_header(target, "cp1252", ",")
        self.assertEqual(header, ["A", "B"])
        counts = self.group_sizes(target, [0])
        self.assertEqual(counts, {("1",): 1})

    def test_column_label_is_selectable(self):
        columns = ["A", "B", "", "D"]
        self.assertEqual(app.parse_selection("(column 3)", columns), [2])
        self.assertEqual(app.parse_selection("(column 3),a", columns), [2, 0])

    def test_column_name_with_a_space_is_selectable(self):
        columns = ["Order Date", "City"]
        self.assertEqual(app.parse_selection("order date", columns), [0])
        self.assertEqual(app.parse_selection("order date,city", columns), [0, 1])

    def test_existing_dup_count_column_keeps_its_name(self):
        rows = [["Dup_Count", "City"], ["99", "Lisbon"], ["88", "Lisbon"]]
        source = self.write_csv("hasdup.csv", rows)
        output = self.path("out.csv")
        header = app.read_header(source, "cp1252", ",")
        counts = self.group_sizes(source, [1])
        app.write_output(source, output, header, "cp1252", ",", [1])
        rows_out = self.read_csv(output)
        self.assertEqual(rows_out[0], ["Dup_Count", "City", "Dup_Count"])
        self.assertEqual(rows_out[1], ["0", "Lisbon", "99"])
        self.assertEqual(rows_out[2], ["1", "Lisbon", "88"])

    def test_cli_warns_about_an_existing_dup_count_column(self):
        rows = [["Dup_Count", "City"], ["99", "Lisbon"], ["88", "Lisbon"]]
        source = self.write_csv("hasdup.csv", rows)
        output = self.path("out.csv")
        answers = iter(["city", "y"])
        buffer = io.StringIO()
        with mock.patch("builtins.input", side_effect=answers):
            with contextlib.redirect_stdout(buffer):
                code = app.main([source, output, "--encoding", "cp1252"])
        self.assertEqual(code, 0)
        self.assertIn("Warning", buffer.getvalue())

    def test_duplicate_header_names_are_preserved(self):
        rows = [["K", "K", "V"], ["a", "b", "1"], ["a", "b", "1"]]
        source = self.write_csv("dup.csv", rows)
        output = self.path("out.csv")
        header = app.read_header(source, "cp1252", ",")
        counts = self.group_sizes(source, [0])
        app.write_output(source, output, header, "cp1252", ",", [0])
        rows_out = self.read_csv(output)
        self.assertEqual(rows_out[0], ["Dup_Count", "K", "K", "V"])
        self.assertEqual(rows_out[1], ["0", "a", "b", "1"])
        self.assertEqual(rows_out[2], ["1", "a", "b", "1"])

    def test_empty_values_group_together(self):
        rows = [["A", "B"], ["", "x"], ["", "x"], [" ", "x"]]
        source = self.write_csv("empties.csv", rows)
        counts = self.group_sizes(source, [0])
        self.assertEqual(counts[("",)], 2)
        self.assertEqual(counts[(" ",)], 1)

    def test_output_uses_the_same_delimiter_as_the_input(self):
        source = self.write_csv("semi.csv", ORDERS, delimiter=";")
        output = self.path("out.csv")
        header = app.read_header(source, "cp1252", ";")
        counts = self.group_sizes(source, [2, 3], delimiter=";")
        app.write_output(source, output, header, "cp1252", ";", [2, 3])
        with open(output, "r", encoding="cp1252", newline="") as handle:
            first = handle.readline()
        self.assertTrue(first.startswith("Dup_Count;City;Item"))

    def test_declining_the_confirmation_writes_no_file(self):
        source = self.write_csv("orders.csv", ORDERS)
        output = self.path("out.csv")
        answers = iter(["city,item", "n"])
        with mock.patch("builtins.input", side_effect=answers):
            with contextlib.redirect_stdout(io.StringIO()):
                code = app.main([source, output, "--encoding", "cp1252"])
        self.assertEqual(code, 0)
        self.assertFalse(os.path.exists(output))

    def test_delimiter_override_is_used(self):
        source = self.write_csv("semi.csv", ORDERS, delimiter=";")
        output = self.path("out.csv")
        answers = iter(["city,item", "y"])
        buffer = io.StringIO()
        with mock.patch("builtins.input", side_effect=answers):
            with contextlib.redirect_stdout(buffer):
                code = app.main([source, output, "--encoding", "cp1252",
                                 "--delimiter", ";"])
        self.assertEqual(code, 0)
        self.assertIn("delimiter: ;", buffer.getvalue())

    def test_error_messages_match_the_spec(self):
        columns = ["City", "Qty"]
        with self.assertRaises(app.SelectionError) as ctx:
            app.parse_selection("99", columns)
        self.assertEqual(str(ctx.exception),
                         "column 99 does not exist (file has 2 columns)")
        with self.assertRaises(app.SelectionError) as ctx:
            app.parse_selection("xyz", columns)
        self.assertEqual(str(ctx.exception), "unknown column 'xyz'")
        with self.assertRaises(app.SelectionError) as ctx:
            app.parse_selection("city,city", columns)
        self.assertEqual(str(ctx.exception), "'City' selected more than once")

    def test_read_header_strips_bom_when_utf8_is_forced(self):
        target = self.path("bom.csv")
        with open(target, "w", encoding="utf-8-sig", newline="") as handle:
            handle.write("Name,City\nAna,Køln\n")
        header = app.read_header(target, "utf-8", ",")
        self.assertEqual(header[0], "Name")

    def test_commas_inside_values_do_not_confuse_detection(self):
        rows = [["a;b;c;d", "e"], ["f;g;h;i", "j"], ["k;l;m;n", "o"]]
        source = self.write_csv("tricky.csv", rows, delimiter=",")
        encoding = app.resolve_encoding(source)
        self.assertEqual(app.detect_dialect(source, encoding), ",")


class WindowsEncodingTests(BaseTestCase):
    """The ANSI code page branch, simulated off-Windows."""

    @staticmethod
    def fake_ctypes(acp):
        return types.SimpleNamespace(
            windll=types.SimpleNamespace(
                kernel32=types.SimpleNamespace(GetACP=lambda: acp)))

    def test_ansi_code_page_maps_to_a_python_codec(self):
        for acp, expected in ((1252, "cp1252"), (932, "cp932"), (936, "cp936"),
                              (949, "cp949"), (950, "cp950"), (874, "cp874"),
                              (1250, "cp1250"), (1258, "cp1258")):
            with mock.patch.object(app.sys, "platform", "win32"):
                with mock.patch.dict(sys.modules, {"ctypes": self.fake_ctypes(acp)}):
                    self.assertEqual(app.windows_ansi_codepage(), expected)

    def test_resolve_encoding_prefers_the_windows_code_page(self):
        source = self.write_csv("plain.csv", ORDERS)
        with mock.patch.object(app.sys, "platform", "win32"):
            with mock.patch.dict(sys.modules, {"ctypes": self.fake_ctypes(1252)}):
                self.assertEqual(app.resolve_encoding(source), "cp1252")

    def test_explicit_override_beats_the_windows_code_page(self):
        source = self.write_csv("plain.csv", ORDERS)
        with mock.patch.object(app.sys, "platform", "win32"):
            with mock.patch.dict(sys.modules, {"ctypes": self.fake_ctypes(1252)}):
                self.assertEqual(app.resolve_encoding(source, "utf-8"), "utf-8")

    def test_bom_still_wins_over_the_windows_code_page(self):
        target = self.path("bom.csv")
        with open(target, "w", encoding="utf-8-sig", newline="") as handle:
            handle.write("Name,City\nAna,Køln\n")
        with mock.patch.object(app.sys, "platform", "win32"):
            with mock.patch.dict(sys.modules, {"ctypes": self.fake_ctypes(1252)}):
                self.assertEqual(app.resolve_encoding(target), "utf-8-sig")

    def test_no_code_page_off_windows(self):
        self.assertIsNone(app.windows_ansi_codepage())


class PickerTests(BaseTestCase):
    """The arrow-key column chooser."""

    COLUMNS = ["OrderID", "Customer", "City", "Item", "Qty"]

    class FakeReader:
        def __init__(self, keys):
            self.keys = list(keys)

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def read_key(self):
            return self.keys.pop(0) if self.keys else None

    def run_picker(self, keys, columns=None):
        picker = app.ColumnPicker(columns or self.COLUMNS)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            result = picker.run(reader=self.FakeReader(keys))
        return result, buffer.getvalue()

    def test_space_toggles_and_enter_returns_the_chosen_order(self):
        result, _ = self.run_picker(["down", "down", "space", "down", "space", "enter"])
        self.assertEqual(result.columns, [2, 3])

    def test_toggling_a_column_twice_removes_it(self):
        result, _ = self.run_picker(
            ["down", "space", "space", "down", "space", "enter"])
        self.assertEqual(result.columns, [2])

    def test_escape_cancels(self):
        result, _ = self.run_picker(["esc"])
        self.assertIsNone(result)

    def test_enter_without_a_selection_does_not_exit(self):
        result, _ = self.run_picker(["enter", "space", "enter"])
        self.assertEqual(result.columns, [0])

    def test_home_and_end_jump_to_the_ends(self):
        result, _ = self.run_picker(["end", "space", "home", "space", "enter"])
        self.assertEqual(result.columns, [4, 0])

    def test_render_shows_the_pick_order(self):
        _, screen = self.run_picker(["down", "space", "enter"])
        self.assertIn("[1]", screen)
        self.assertIn("Order: Customer", screen)

    def test_pick_columns_uses_the_arrow_picker_when_asked(self):
        picker = app.ColumnPicker(self.COLUMNS)
        reader = self.FakeReader(["down", "space", "enter"])
        with contextlib.redirect_stdout(io.StringIO()):
            result = picker.run(reader=reader)
        self.assertEqual(result.columns, [1])


class OutputNamingTests(BaseTestCase):
    def test_existing_output_file_is_overwritten(self):
        source = self.write_csv("orders.csv", ORDERS)
        output = self.path("orders-dupes.csv")
        with open(output, "w", encoding="cp1252") as handle:
            handle.write("stale content\r\n")
        answers = iter(["city,item", "y"])
        buffer = io.StringIO()
        with mock.patch("builtins.input", side_effect=answers):
            with contextlib.redirect_stdout(buffer):
                code = app.main([source, output, "--encoding", "cp1252"])
        self.assertEqual(code, 0)
        self.assertIn("Overwriting existing file", buffer.getvalue())
        rows = self.read_csv(output)
        self.assertEqual(rows[0][0], "Dup_Count")
        self.assertEqual([row[0] for row in rows[1:]], ["0", "1", "0", "2", "3"])

    def test_cli_uses_the_dupes_prefixed_default_output_path(self):
        source = self.write_csv("orders.csv", ORDERS)
        answers = iter(["city,item", "y"])
        buffer = io.StringIO()
        with mock.patch("builtins.input", side_effect=answers):
            with contextlib.redirect_stdout(buffer):
                code = app.main([source, "--encoding", "cp1252"])
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(self.path("orders-dupes.csv")))
        self.assertIn("orders-dupes.csv", buffer.getvalue())


class DeleteAndCombinedTests(BaseTestCase):
    """The delete key, the combined field, and their script equivalents."""

    COLUMNS = ["OrderID", "Customer", "City", "Item", "Qty"]

    class FakeReader:
        def __init__(self, keys):
            self.keys = list(keys)

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def read_key(self):
            return self.keys.pop(0) if self.keys else None

    def run_picker(self, keys, columns=None, **kwargs):
        picker = app.ColumnPicker(columns or self.COLUMNS, **kwargs)
        with contextlib.redirect_stdout(io.StringIO()):
            return picker.run(reader=self.FakeReader(keys))

    def test_delete_key_removes_a_column(self):
        selection = self.run_picker(["down", "space", "end", "delete", "enter"])
        self.assertEqual(selection.deleted, {4})
        self.assertEqual(selection.columns, [1])

    def test_enter_with_nothing_selected_shows_a_message(self):
        picker = app.ColumnPicker(self.COLUMNS)
        with contextlib.redirect_stdout(io.StringIO()):
            picker._loop(self.FakeReader(["enter"]))
            screen = sys.stdout.getvalue()
        self.assertIn("Pick at least one column", screen)

    def test_delete_key_twice_brings_the_column_back(self):
        selection = self.run_picker(["down", "space", "end", "delete", "delete", "enter"])
        self.assertEqual(selection.deleted, set())
        self.assertEqual(selection.columns, [1])

    def test_deleting_a_grouping_column_also_ungroups_it(self):
        selection = self.run_picker(
            ["down", "space", "delete", "down", "space", "enter"])
        self.assertEqual(selection.deleted, {1})
        self.assertEqual(selection.columns, [2])

    def test_grouping_a_deleted_column_brings_it_back(self):
        selection = self.run_picker(["down", "delete", "space", "enter"])
        self.assertEqual(selection.deleted, set())
        self.assertEqual(selection.columns, [1])

    def test_c_toggles_the_combined_field(self):
        selection = self.run_picker(["down", "space", "c", "enter"])
        self.assertTrue(selection.combined)
        selection = self.run_picker(["down", "space", "c", "c", "enter"])
        self.assertFalse(selection.combined)

    def test_picker_starts_from_the_given_deleted_and_combined_state(self):
        selection = self.run_picker(
            ["down", "space", "enter"], deleted=[3], combined=True)
        self.assertEqual(selection.deleted, {3})
        self.assertTrue(selection.combined)

    def test_render_marks_deleted_columns(self):
        picker = app.ColumnPicker(self.COLUMNS)
        picker.deleted.add(2)
        with contextlib.redirect_stdout(io.StringIO()):
            picker._render()
            screen = sys.stdout.getvalue()
        self.assertIn("[-]", screen)
        self.assertIn("Removed from the output: City", screen)

    def test_parse_column_list_accepts_names_and_numbers(self):
        self.assertEqual(app.parse_column_list("City,5", self.COLUMNS), [2, 4])

    def test_parse_column_list_accepts_names_with_spaces(self):
        columns = ["Order Date", "City"]
        self.assertEqual(app.parse_column_list("order date", columns), [0])

    def test_parse_column_list_rejects_unknown_columns(self):
        with self.assertRaises(app.AppError):
            app.parse_column_list("nope", self.COLUMNS)

    def test_parse_column_list_rejects_out_of_range(self):
        with self.assertRaises(app.AppError):
            app.parse_column_list("99", self.COLUMNS)

    def test_cli_exclude_flag_drops_a_column(self):
        source = self.write_csv("orders.csv", ORDERS)
        output = self.path("out.csv")
        answers = iter(["city,item", "y"])
        buffer = io.StringIO()
        with mock.patch("builtins.input", side_effect=answers):
            with contextlib.redirect_stdout(buffer):
                code = app.main([source, output, "--encoding", "cp1252",
                                 "--exclude", "Qty"])
        self.assertEqual(code, 0)
        rows = self.read_csv(output)
        self.assertEqual(rows[0], ["Dup_Count", "City", "Item", "OrderID", "Customer"])

    def test_cli_combined_flag_adds_the_combined_field(self):
        source = self.write_csv("orders.csv", ORDERS)
        output = self.path("out.csv")
        answers = iter(["city,item", "y"])
        buffer = io.StringIO()
        with mock.patch("builtins.input", side_effect=answers):
            with contextlib.redirect_stdout(buffer):
                code = app.main([source, output, "--encoding", "cp1252",
                                 "--combined"])
        self.assertEqual(code, 0)
        rows = self.read_csv(output)
        self.assertEqual(rows[0][:2], ["Dup_Count", "Dup_Key"])
        self.assertEqual(rows[1][1], "0 -:- Lisbon -:- Widget")

    def test_cli_separator_flag_changes_the_joiner(self):
        source = self.write_csv("orders.csv", ORDERS)
        output = self.path("out.csv")
        answers = iter(["city,item", "y"])
        buffer = io.StringIO()
        with mock.patch("builtins.input", side_effect=answers):
            with contextlib.redirect_stdout(buffer):
                code = app.main([source, output, "--encoding", "cp1252",
                                 "--combined", "--separator", " | "])
        self.assertEqual(code, 0)
        rows = self.read_csv(output)
        self.assertEqual(rows[1][1], "0 | Lisbon | Widget")


if __name__ == "__main__":
    unittest.main(verbosity=2)
