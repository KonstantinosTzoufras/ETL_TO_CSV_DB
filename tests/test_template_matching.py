"""Name matching proposes bindings; it never decides one.

Every rule here is an identity rule. The negative tests are the point: a matcher
that reaches KOD_PEL from customer_code would be worse than no matcher at all.
"""
from pathlib import Path
import tempfile
import unittest

from etl.spec import ConfigError
from etl.templates import (TemplateStore, apply_template, bind_template, match_template,
                           propose_bindings, template_to_dict)


def blueprint(*names, processing_version=2):
    return {"format_version": 1, "name": "Matching target", "processing_version": processing_version,
            "fields": [{"output_name": name, "target_type": "string"} for name in names]}


def draft():
    return {"version": 2, "name": "Target pipeline", "source": {"kind": "csv", "path": "input.csv"},
            "columns": [], "destination": {"kind": "csv"}}


class MatchingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = TemplateStore(Path(temp.name) / "templates")

    def propose(self, names, columns):
        template = template_to_dict(self.store.create(blueprint(*names)))
        return propose_bindings(template, columns)

    def one(self, name, columns):
        return self.propose([name], columns)[0]

    def assertUnmatched(self, name, columns, why):
        proposal = self.one(name, columns)
        self.assertEqual(proposal["status"], "unmatched", why)
        self.assertIsNone(proposal["binding"], why)

    # 1-3: the three tiers, each tagged with the rule that produced it.

    def test_identical_name_matches(self):
        proposal = self.one("customer_code", ["other", "customer_code"])
        self.assertEqual((proposal["status"], proposal["rule"]), ("matched", "identical"))
        self.assertEqual(proposal["binding"], {"source": "customer_code"})

    def test_case_only_difference_matches(self):
        proposal = self.one("customer_code", ["CUSTOMER_CODE"])
        self.assertEqual((proposal["status"], proposal["rule"]), ("matched", "case"))
        self.assertEqual(proposal["binding"], {"source": "CUSTOMER_CODE"})

    def test_separator_difference_matches(self):
        for column in ("CustomerCode", "customer code", "customer-code", "customer.code"):
            with self.subTest(column=column):
                proposal = self.one("customer_code", [column])
                self.assertEqual((proposal["status"], proposal["rule"]), ("matched", "separators"))
                self.assertEqual(proposal["binding"], {"source": column})

    # 4-6: several candidates is a question for the operator, never a guess.

    def test_two_columns_differing_only_by_case_are_ambiguous(self):
        proposal = self.one("amount", ["Amount", "AMOUNT"])
        self.assertEqual((proposal["status"], proposal["rule"]), ("ambiguous", "case"))
        self.assertIsNone(proposal["binding"])
        self.assertEqual(sorted(proposal["candidates"]), ["AMOUNT", "Amount"])

    def test_two_columns_differing_only_by_separators_are_ambiguous(self):
        proposal = self.one("customer_code", ["customer code", "CustomerCode"])
        self.assertEqual((proposal["status"], proposal["rule"]), ("ambiguous", "separators"))
        self.assertEqual(sorted(proposal["candidates"]), ["CustomerCode", "customer code"])

    def test_a_repeated_source_name_is_ambiguous_rather_than_arbitrary(self):
        proposal = self.one("code", ["code", "code"])
        self.assertEqual(proposal["status"], "ambiguous")
        self.assertEqual(proposal["candidates"], ["code", "code"])

    def test_ambiguity_never_widens_to_a_looser_rule(self):
        # "Amount"/"AMOUNT" tie at the case tier. Separators would only add
        # "amount " to the tie, so the looser tier must not be consulted.
        proposal = self.one("amount", ["Amount", "AMOUNT", "a mount"])
        self.assertEqual((proposal["status"], proposal["rule"]), ("ambiguous", "case"))
        self.assertNotIn("a mount", proposal["candidates"])

    # 7-9: what must stay unmatched.

    def test_no_candidate_anywhere_is_unmatched(self):
        self.assertUnmatched("origin", ["a", "b"], "nothing resembles it")

    def test_real_world_abbreviations_are_never_reached(self):
        for column in ("KOD_PEL", "CUSTNO", "IDCLIENT", "cust_code", "code", "customer_code_2", "customer"):
            with self.subTest(column=column):
                self.assertUnmatched("customer_code", [column], f"{column} is a different name")

    def test_accents_are_not_folded_away(self):
        self.assertUnmatched("ΠΟΣΟ", ["ΠΟΣΌ"], "accented Greek names are distinct names")

    def test_a_blank_source_column_is_not_a_candidate(self):
        # A template cannot hold a blank target name, but a CSV header can be blank.
        self.assertUnmatched("code", ["   ", ""], "whitespace is not an identity")
        self.assertEqual(self.one("code", ["   ", "code"])["binding"], {"source": "code"})

    # 10: precedence between tiers.

    def test_an_exact_name_wins_over_a_case_variant(self):
        proposal = self.one("country", ["Country", "country", "COUNTRY"])
        self.assertEqual((proposal["status"], proposal["rule"]), ("matched", "identical"))
        self.assertEqual(proposal["binding"], {"source": "country"})

    # 11-12: what matching must not touch.

    def test_proposals_never_invent_a_literal_and_never_mutate_the_template(self):
        template = template_to_dict(self.store.create(blueprint("code", "origin")))
        before = template_to_dict(self.store.read(template["id"], 1))
        proposals = propose_bindings(template, ["CODE"])
        self.assertTrue(all("literal" not in (p["binding"] or {}) for p in proposals))
        self.assertEqual(template_to_dict(self.store.read(template["id"], 1)), before)

    def test_locked_targets_are_left_alone_and_counted(self):
        template = template_to_dict(self.store.create(blueprint("code", "amount", "origin")))
        result = match_template(template, ["code", "amount"], [False, True, False])
        self.assertEqual(result["counts"], {"matched": 1, "ambiguous": 0, "unmatched": 1, "kept": 1})
        self.assertEqual(result["proposals"][1], {"binding": None, "status": "kept", "rule": None, "candidates": []})

    def test_locked_must_cover_every_target(self):
        template = template_to_dict(self.store.create(blueprint("code", "amount")))
        for locked in ([True], [True, True, True], ["yes", "no"]):
            with self.subTest(locked=locked):
                with self.assertRaises(ConfigError):
                    match_template(template, ["code"], locked)

    def test_columns_must_be_a_list_of_names(self):
        template = template_to_dict(self.store.create(blueprint("code")))
        for columns in ("code", [1], None, [{"name": "code"}]):
            with self.subTest(columns=columns):
                with self.assertRaises(ConfigError):
                    propose_bindings(template, columns)

    # 13-15: the matcher sits beside the existing contract, it does not move it.

    def test_proposed_bindings_generate_the_same_pipeline_as_binding_by_hand(self):
        template = template_to_dict(self.store.create(blueprint("code", "name")))
        proposals = propose_bindings(template, ["CODE", "Name"])
        proposed = bind_template(template, draft(), [p["binding"] for p in proposals])
        manual = bind_template(template, draft(), [{"source": "CODE"}, {"source": "Name"}])
        self.assertEqual(proposed, manual)

    def test_a_matched_target_still_blocks_while_its_lookup_is_unresolved(self):
        definition = blueprint("code")
        definition["fields"][0]["lookup_required"] = True
        template = template_to_dict(self.store.create(definition))
        proposal = propose_bindings(template, ["code"])[0]
        self.assertEqual(proposal["status"], "matched")
        with self.assertRaises(ConfigError):
            bind_template(template, draft(), [proposal["binding"]])

    def test_apply_still_binds_nothing_by_itself(self):
        template = self.store.create(blueprint("code", "name"))
        pending = apply_template(template, draft())
        self.assertEqual(pending["bindings"], [None, None])
        self.assertEqual(pending["pipeline"]["columns"], [])


if __name__ == "__main__":
    unittest.main()
