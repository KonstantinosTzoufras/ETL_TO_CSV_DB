"""A binding profile stores one operator's judgement about one dataset.

It must never resolve anything by itself: reconciliation reports, the operator
decides, and only bind_template turns bindings into a pipeline.
"""
from contextlib import closing
from pathlib import Path
import json
import sqlite3
import tempfile
import unittest

from etl.binding_profiles import (build_profile, default_name, dispatch, key_text,
                                  profile_from_dict, rank, reconcile, rule_changes,
                                  bindings_for_binding, source_key)
from etl.spec import ConfigError
from etl.store import Store
from etl.templates import TemplateStore, bind_template, template_to_dict

CSV = {"kind": "csv", "path": "erp/customers.csv", "delimiter": ";"}
SQL = {"kind": "sqlserver", "connection_env": "ETL_SQL_ERP_A", "schema": "dbo", "table": "Customers"}
def query_source(sql="SELECT KOD_PEL FROM dbo.Customers"):
    return {"kind": "sqlserver_query", "connection_env": "ETL_SQL_ERP_A",
            "query": {"format_version": 1, "dialect": "tsql", "sql": sql,
                      "parameters": [], "timeout_seconds": 30}}


QUERY = query_source()


def blueprint(fields=None, revision_marker="Customer export"):
    return {"format_version": 1, "name": revision_marker, "processing_version": 2,
            "fields": fields or [
                {"output_name": "customer_code", "target_type": "string", "required": True, "max_length": 10},
                {"output_name": "amount", "target_type": "string"},
                {"output_name": "origin", "target_type": "string"}]}


def draft(source=None):
    return {"version": 2, "name": "Export", "source": source or dict(CSV),
            "columns": [], "destination": {"kind": "csv"}}


BINDINGS = {"customer_code": {"source": "KOD_PEL"}, "amount": {"source": "POSO"},
            "origin": {"literal": "ERP-A"}}


class ProfileTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.templates = TemplateStore(self.root / "templates")
        self.store = Store(self.root / "state.sqlite3")
        self.template = self.templates.create(blueprint())

    def profile(self, source=None, bindings=None, snapshot=("KOD_PEL", "POSO", "HMNIA"), name="ERP A"):
        return build_profile(name=name, template_id=self.template.id, authored_revision=self.template.revision,
                             source=source or dict(CSV), schema_snapshot=list(snapshot),
                             bindings=bindings if bindings is not None else dict(BINDINGS))

    def call(self, operation, **body):
        return dispatch(self.store, self.templates, operation, body)

    # Identity.

    def test_source_key_omits_read_options_and_never_carries_a_secret(self):
        self.assertEqual(source_key(CSV), {"kind": "csv", "path": "erp/customers.csv"})
        self.assertEqual(source_key({**CSV, "delimiter": ",", "encoding": "utf-8"}), source_key(CSV))
        self.assertEqual(source_key(SQL), {"kind": "sqlserver", "connection_env": "ETL_SQL_ERP_A",
                                           "schema": "dbo", "table": "Customers"})
        self.assertNotIn("password", json.dumps(source_key(SQL)).lower())

    def test_an_edited_query_is_a_different_dataset(self):
        other = query_source("SELECT CUSTNO FROM dbo.Customers")
        self.assertNotEqual(source_key(QUERY), source_key(other))
        self.assertNotIn("SELECT", json.dumps(source_key(QUERY)), "the SQL text itself is not stored in the key")

    def test_rank_orders_but_never_selects(self):
        self.assertEqual(rank(source_key(CSV), CSV), "exact")
        self.assertEqual(rank(source_key(SQL), SQL), "exact")
        # Same table, another connection: DEV and PROD deserve to be visible.
        other_connection = {**SQL, "connection_env": "ETL_SQL_ERP_A_PROD"}
        self.assertEqual(rank(source_key(SQL), other_connection), "same-dataset")
        self.assertEqual(rank(source_key(CSV), SQL), "other")

    def test_default_name_is_recognisable_per_source_kind(self):
        self.assertEqual(default_name(CSV), "customers.csv")
        self.assertEqual(default_name(SQL), "ERP_A · dbo.Customers")
        self.assertTrue(default_name(QUERY).startswith("ERP_A · query "))

    # Validation.

    def test_a_profile_rejects_anything_outside_its_shape(self):
        for mutate in ({"name": ""}, {"format_version": 2}, {"id": "nope"}, {"authored_revision": 0},
                       {"bindings": []}, {"schema_snapshot": [1]}, {"updated": ""}):
            with self.subTest(mutate=mutate):
                with self.assertRaises(ConfigError):
                    profile_from_dict({**self.profile(), **mutate})

    def test_a_binding_holds_a_source_or_a_literal_but_never_both(self):
        for binding in ({"source": "A", "literal": "x"}, {}, {"source": "  "}, {"connection": "x"},
                        {"source": "A", "lookup": {"column": "c"}}):
            with self.subTest(binding=binding):
                with self.assertRaises(ConfigError):
                    self.profile(bindings={"customer_code": binding})

    def test_literals_of_every_kind_are_stored(self):
        bindings = {"a": {"literal": None}, "b": {"literal": ""}, "c": {"literal": True}, "d": {"literal": "003\nΕλλάδα"}}
        self.assertEqual(self.profile(bindings=bindings)["bindings"], bindings)

    # Storage.

    def test_saving_listing_reading_and_deleting(self):
        saved = self.store.save_profile(self.profile())
        listed = self.call("list", template_id=self.template.id, source=dict(CSV))
        self.assertEqual([(item["name"], item["rank"]) for item in listed], [("ERP A", "exact")])
        self.assertEqual(self.store.read_profile(saved["id"])["bindings"], BINDINGS)
        self.call("delete", id=saved["id"])
        self.assertEqual(self.call("list", template_id=self.template.id), [])
        with self.assertRaises(ConfigError):
            self.store.read_profile(saved["id"])

    def test_a_concurrent_edit_is_refused_rather_than_lost(self):
        saved = self.store.save_profile(self.profile())
        self.store.save_profile({**saved, "name": "Renamed elsewhere", "updated": "2099-01-01T00:00:00+00:00"},
                                expected_updated=saved["updated"])
        with self.assertRaises(ConfigError):
            self.store.save_profile({**saved, "name": "Mine"}, expected_updated=saved["updated"])
        self.assertEqual(self.store.read_profile(saved["id"])["name"], "Renamed elsewhere")

    def test_listing_is_scoped_to_one_template(self):
        other = self.templates.create(blueprint(revision_marker="Another export"))
        self.store.save_profile(self.profile())
        self.store.save_profile(build_profile(name="Other", template_id=other.id, authored_revision=1,
                                              source=dict(CSV), schema_snapshot=[], bindings={}))
        self.assertEqual([p["name"] for p in self.call("list", template_id=self.template.id)], ["ERP A"])

    def test_saving_refuses_a_revision_that_does_not_exist(self):
        with self.assertRaises(ConfigError):
            self.call("save", name="X", template_id=self.template.id, revision=99,
                      source=dict(CSV), schema_snapshot=[], bindings=dict(BINDINGS))

    def test_several_profiles_may_serve_the_same_source_and_template(self):
        self.store.save_profile(self.profile(name="Full export"))
        self.store.save_profile(self.profile(name="Codes only", bindings={"customer_code": {"source": "KOD_PEL"}}))
        listed = self.call("list", template_id=self.template.id, source=dict(CSV))
        self.assertEqual(sorted(item["name"] for item in listed), ["Codes only", "Full export"])
        self.assertTrue(all(item["rank"] == "exact" for item in listed))

    # Reconciliation: the heart of the feature.

    def test_every_stored_binding_is_restored_when_nothing_moved(self):
        result = reconcile(self.profile(), template_to_dict(self.template), ["KOD_PEL", "POSO", "HMNIA"])
        self.assertEqual(result["counts"], {"restored": 3, "missing": 0, "new": 0, "removed": 0})
        self.assertEqual(bindings_for_binding(result["entries"]), [BINDINGS["customer_code"], BINDINGS["amount"], BINDINGS["origin"]])
        self.assertFalse(result["revision_changed"])

    def test_a_missing_source_column_becomes_unresolved_and_is_named(self):
        result = reconcile(self.profile(), template_to_dict(self.template), ["POSO", "HMNIA"])
        first = result["entries"][0]
        self.assertEqual((first["target"], first["status"], first["binding"]), ("customer_code", "missing", None))
        self.assertIn("KOD_PEL", first["note"])
        self.assertEqual(result["counts"], {"restored": 2, "missing": 1, "new": 0, "removed": 0})
        # The other two survive: three rows to fix, not thirty.
        self.assertEqual(bindings_for_binding(result["entries"])[1:], [BINDINGS["amount"], BINDINGS["origin"]])

    def test_a_literal_binding_is_unaffected_by_the_source_schema(self):
        result = reconcile(self.profile(), template_to_dict(self.template), [])
        self.assertEqual(result["entries"][2]["status"], "restored")
        self.assertEqual(result["entries"][2]["binding"], {"literal": "ERP-A"})

    def test_new_targets_are_unresolved_and_removed_ones_are_reported(self):
        revised = self.templates.revise(self.template.id, 1, blueprint(fields=[
            {"output_name": "customer_code", "target_type": "string", "required": True, "max_length": 10},
            {"output_name": "vat_number", "target_type": "string"}]))
        result = reconcile(self.profile(), template_to_dict(revised), ["KOD_PEL", "POSO"])
        self.assertEqual([(e["target"], e["status"]) for e in result["entries"]],
                         [("customer_code", "restored"), ("vat_number", "new")])
        self.assertEqual(result["removed"], ["amount", "origin"])
        self.assertTrue(result["revision_changed"])
        self.assertEqual(result["counts"], {"restored": 1, "missing": 0, "new": 1, "removed": 2})

    def test_reconcile_without_columns_checks_the_template_only(self):
        result = reconcile(self.profile(), template_to_dict(self.template), None)
        self.assertEqual(result["counts"]["missing"], 0, "no source read means no column verdict")
        self.assertEqual(result["counts"]["restored"], 3)

    def test_a_profile_cannot_be_reconciled_against_another_template(self):
        other = self.templates.create(blueprint(revision_marker="Another export"))
        with self.assertRaises(ConfigError):
            reconcile(self.profile(), template_to_dict(other), [])

    def test_changed_rules_are_reported_while_the_binding_stays_valid(self):
        revised = self.templates.revise(self.template.id, 1, blueprint(fields=[
            {"output_name": "customer_code", "target_type": "string", "required": True, "max_length": 20},
            {"output_name": "amount", "target_type": "decimal", "transforms": ["trim"]},
            {"output_name": "origin", "target_type": "string"}]))
        changes = rule_changes(template_to_dict(self.template), template_to_dict(revised))
        self.assertEqual({change["target"] for change in changes}, {"customer_code", "amount"})
        amount = next(change for change in changes if change["target"] == "amount")
        self.assertEqual(sorted(amount["rules"]), ["target_type", "transforms"])
        self.assertEqual((amount["before"]["target_type"], amount["after"]["target_type"]), ("string", "decimal"))
        # The binding itself is untouched: where the value comes from did not change.
        result = reconcile(self.profile(), template_to_dict(revised), ["KOD_PEL", "POSO"])
        self.assertEqual(result["entries"][1]["binding"], {"source": "POSO"})

    def test_reconcile_through_dispatch_reports_rules_and_names_the_profile(self):
        saved = self.store.save_profile(self.profile())
        revised = self.templates.revise(self.template.id, 1, blueprint(fields=[
            {"output_name": "customer_code", "target_type": "decimal"},
            {"output_name": "amount", "target_type": "string"},
            {"output_name": "origin", "target_type": "string"}]))
        result = self.call("reconcile", id=saved["id"], template=template_to_dict(revised), columns=["KOD_PEL", "POSO"])
        self.assertEqual(result["profile"]["name"], "ERP A")
        self.assertEqual([change["target"] for change in result["rule_changes"]], ["customer_code"])
        self.assertEqual(result["authored_revision"], 1)

    # Nothing here reaches the pipeline on its own.

    def test_reconciling_writes_nothing_anywhere(self):
        saved = self.store.save_profile(self.profile())
        before = self.store.read_profile(saved["id"])
        self.call("reconcile", id=saved["id"], template=template_to_dict(self.template), columns=[])
        self.assertEqual(self.store.read_profile(saved["id"]), before)
        self.assertEqual(self.store.pipelines(), [])

    def test_a_restored_profile_produces_the_same_pipeline_as_binding_by_hand(self):
        result = reconcile(self.profile(), template_to_dict(self.template), ["KOD_PEL", "POSO"])
        from_profile = bind_template(template_to_dict(self.template), draft(), bindings_for_binding(result["entries"]))
        manual = bind_template(template_to_dict(self.template), draft(),
                               [{"source": "KOD_PEL"}, {"source": "POSO"}, {"literal": "ERP-A"}])
        self.assertEqual(from_profile, manual)

    def test_an_unresolved_target_still_blocks_generation(self):
        result = reconcile(self.profile(), template_to_dict(self.template), ["POSO"])
        with self.assertRaises(ConfigError):
            bind_template(template_to_dict(self.template), draft(), bindings_for_binding(result["entries"]))

    def test_the_generated_pipeline_carries_no_profile_reference(self):
        result = reconcile(self.profile(), template_to_dict(self.template), ["KOD_PEL", "POSO"])
        generated = bind_template(template_to_dict(self.template), draft(), bindings_for_binding(result["entries"]))
        text = json.dumps(generated)
        self.assertNotIn("profile", text)
        self.assertNotIn("template", text)
        self.assertEqual(set(generated), {"version", "name", "source", "columns", "destination"})

    def test_unknown_operations_and_stray_keys_are_refused(self):
        with self.assertRaises(ConfigError):
            self.call("purge", id="x")
        with self.assertRaises(ConfigError):
            self.call("read", id="x", extra=1)

    def test_profiles_live_in_their_own_table_and_leave_pipelines_alone(self):
        self.store.save_profile(self.profile())
        db = sqlite3.connect(self.root / "state.sqlite3")
        with closing(db):
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            stored = db.execute("SELECT source_key FROM binding_profiles").fetchone()[0]
        self.assertIn("binding_profiles", tables)
        self.assertEqual(stored, key_text(source_key(CSV)))


if __name__ == "__main__":
    unittest.main()
