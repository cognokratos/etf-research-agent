"""Offline tests for the agent half of the approval boundary.

Needs no cluster, no model and no database. The Rust half — signature
verification, binding, lifetime ceiling, the transition policy — is tested by
`cargo test -p etf-mcp-server`; this covers what the agent is responsible for:

* the minted token's shape and its signature, verified against an independent
  reimplementation of the check, so the two sides cannot silently diverge;
* the canonical payload encoding both sides must agree on;
* which prompts a human is asked for, and when;
* the interaction-ownership and offered-choice checks that close NAT's
  two-UUIDs-is-authorization gap;
* the identity boundary in front of both: exactly one non-empty asserted
  identity per request, enforced ahead of NAT (whose own 1.9 refusal does not
  reach the client on the workflow routes) and applied identically to the
  approval responder;
* that cancellation mints nothing at all.

Run inside the agent image::

    docker compose exec agent python /app/verify_approval_tokens.py
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import unittest

# setdefault is not enough: docker-compose passes HITL_APPROVAL_SECRET through as
# an *empty string* when the approval feature is off, so the key exists and
# setdefault leaves it empty. These tests mint tokens, so they need a real one.
if len(os.environ.get("HITL_APPROVAL_SECRET", "")) < 24:
    os.environ["HITL_APPROVAL_SECRET"] = "a-test-secret-of-at-least-24-characters"

from nat_streaming_react.approval import (  # noqa: E402
    ACTION_ASSIGN,
    ACTION_COMMIT,
    ACTION_SHORTLIST,
    CANCEL_SENTINEL,
    DECISIONS,
    MAX_TOKEN_TTL_SECONDS,
    AssignEtfRequest,
    CommitEvaluationRequest,
    ShortlistEtfRequest,
    _note_disclosure,
    action_payload,
    approval_result,
    approval_secret,
    build_claims,
    canonical_json,
    cancelled,
    decision_options,
    execute_url,
    mint_token,
    model_supplied_note,
    payload_hash,
    prompt_text,
    required_prompts,
)
from nat_streaming_react.fastapi_worker import (  # noqa: E402
    PUBLIC_PATHS,
    RequireIdentityHeaderMiddleware,
    StaticServiceKeyMiddleware,
)
from nat_streaming_react.interaction_guard import (  # noqa: E402
    IDENTITY_HEADER,
    InteractionAuthorizationError,
    OfferedChoice,
    OwnerAwareExecutionStore,
    ResponderIdentityMiddleware,
    _responder,
    current_responder,
    prompt_offer,
    submitted_choice,
)

ACTOR = "researcher-1"
REQUEST_ID = "11111111-1111-4111-8111-111111111111"
RESOURCE = "VWCE-XETRA"


def verify_independently(secret: bytes, token: str) -> dict:
    """A second implementation of the check, written from the format alone.

    Deliberately not a call into the minter's own helpers: if this agrees with
    `mint_token`, the format is what both this file and the Rust verifier
    believe it is.
    """

    payload_b64, signature_b64 = token.split(".")

    def unpad(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

    expected = hmac.new(secret, payload_b64.encode("ascii"), hashlib.sha256).digest()
    if not hmac.compare_digest(expected, unpad(signature_b64)):
        raise AssertionError("signature does not verify")
    return json.loads(unpad(payload_b64))


def claims(**overrides) -> dict:
    base = dict(
        action=ACTION_COMMIT,
        resource_id=RESOURCE,
        actor_id=ACTOR,
        request_id=REQUEST_ID,
        ttl_seconds=600,
        # A promotion: the engine said `research`, the human chose `shortlist`.
        choice="shortlist",
        expected_choice="research",
        rationale="Accepting the tracking-difference gap deliberately.",
        payload=action_payload(
            llm_recommendation="research",
            research_note="Broad developed-market exposure at 0.12% TER.",
        ),
    )
    base.update(overrides)
    return build_claims(**base)


class TokenShapeTests(unittest.TestCase):
    def test_a_minted_token_verifies_and_carries_every_binding(self):
        token = mint_token(approval_secret(), claims())
        decoded = verify_independently(approval_secret(), token)

        self.assertEqual(decoded["v"], 1)
        self.assertEqual(decoded["action"], ACTION_COMMIT)
        self.assertEqual(decoded["resource_id"], RESOURCE)
        self.assertEqual(decoded["actor_id"], ACTOR)
        self.assertEqual(decoded["request_id"], REQUEST_ID)
        self.assertEqual(decoded["choice"], "shortlist")
        self.assertEqual(decoded["expected_choice"], "research")
        self.assertTrue(decoded["override_requested"])
        self.assertTrue(decoded["nonce"])

    def test_the_signature_covers_the_payload(self):
        token = mint_token(approval_secret(), claims())
        payload_b64, signature = token.split(".")
        tampered_payload = json.loads(
            base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4))
        )
        tampered_payload["resource_id"] = "IWDA-AMS"
        forged = (
            base64.urlsafe_b64encode(canonical_json(tampered_payload).encode())
            .decode()
            .rstrip("=")
            + "."
            + signature
        )
        with self.assertRaises(AssertionError):
            verify_independently(approval_secret(), forged)

    def test_a_different_secret_does_not_verify(self):
        token = mint_token(b"a-different-secret-also-24-chars+", claims())
        with self.assertRaises(AssertionError):
            verify_independently(approval_secret(), token)

    def test_every_nonce_is_unique(self):
        nonces = {claims()["nonce"] for _ in range(50)}
        self.assertEqual(len(nonces), 50, "a reused nonce would make an approval replayable")

    def test_the_expiry_is_bounded_by_the_configured_ttl(self):
        now = int(time.time())
        self.assertEqual(claims(ttl_seconds=600, **{})["exp"] - now, 600)
        for invalid in (0, 59, MAX_TOKEN_TTL_SECONDS + 1, 86_400):
            with self.subTest(ttl=invalid), self.assertRaises(ValueError):
                claims(ttl_seconds=invalid)

    def test_a_short_secret_is_refused(self):
        previous = os.environ["HITL_APPROVAL_SECRET"]
        os.environ["HITL_APPROVAL_SECRET"] = "too-short"
        try:
            with self.assertRaises(ValueError):
                approval_secret()
        finally:
            os.environ["HITL_APPROVAL_SECRET"] = previous

    def test_override_requested_is_derived_not_asserted(self):
        self.assertTrue(
            claims(choice="shortlist", expected_choice="research")["override_requested"]
        )
        self.assertFalse(
            claims(choice="research", expected_choice="research")["override_requested"]
        )


class CanonicalEncodingTests(unittest.TestCase):
    """Both sides hash the payload independently, in different languages."""

    def test_key_order_does_not_change_the_digest(self):
        self.assertEqual(
            payload_hash({"research_note": "n", "assignee": "a"}),
            payload_hash({"assignee": "a", "research_note": "n"}),
        )

    def test_content_does_change_the_digest(self):
        self.assertNotEqual(
            payload_hash({"research_note": "a"}), payload_hash({"research_note": "b"})
        )

    def test_the_digest_matches_the_payload_the_token_carries(self):
        built = claims()
        self.assertEqual(built["payload_sha256"], payload_hash(built["payload"]))

    def test_the_canonical_form_is_compact_and_sorted(self):
        self.assertEqual(canonical_json({"b": 1, "a": 2}), '{"a":2,"b":1}')
        # Pinned against the Rust side's canonical_json, which produces the same
        # bytes for the same value.
        self.assertEqual(
            payload_hash({"research_note": "x"}),
            hashlib.sha256(b'{"research_note":"x"}').hexdigest(),
        )


class PromptRuleTests(unittest.TestCase):
    """Which prompts a human is asked for, and when. Pure, so no model runs."""

    def test_a_different_decision_requires_a_rationale_and_confirming_does_not(self):
        needs_rationale, _ = required_prompts(
            "shortlist", rules_decision="research", note_available=True
        )
        self.assertTrue(needs_rationale)
        needs_rationale, _ = required_prompts(
            "reject", rules_decision="research", note_available=True
        )
        self.assertTrue(needs_rationale, "a demotion is an override too")
        needs_rationale, _ = required_prompts(
            "research", rules_decision="research", note_available=True
        )
        self.assertFalse(needs_rationale)

    def test_a_human_promotion_can_be_completed_without_the_model(self):
        """The property `verify_hitl_override.py` drives end to end.

        The MCP refuses a shortlist with no grounded note, and a model proposing
        `research` has no reason to have drafted one. If the note were only ever
        the model's, a person could reach shortlist exactly where the model had
        already been -- permission rather than initiative. So choosing
        `shortlist` with no drafted note must ask the human for one.
        """

        needs_rationale, needs_note = required_prompts(
            "shortlist", rules_decision="research", note_available=False
        )
        self.assertTrue(needs_rationale)
        self.assertTrue(needs_note, "a human-initiated promotion must be completable")

        # With a drafted note already on screen, confirming stays one interaction.
        needs_rationale, needs_note = required_prompts(
            "shortlist", rules_decision="research", note_available=True
        )
        self.assertTrue(needs_rationale)
        self.assertFalse(needs_note)

        # A note is only ever required *for* a shortlist.
        for decision in ("reject", "research"):
            with self.subTest(decision=decision):
                _, needs_note = required_prompts(
                    decision, rules_decision="research", note_available=False
                )
                self.assertFalse(needs_note)

    def test_every_decision_is_offered_plus_cancel(self):
        """All three, regardless of what the model proposed or what a hard
        constraint forbids. The MCP is the authority on constraints and
        re-checks them after approval; filtering here would duplicate policy
        into the prompt and could imply the constraint lives there."""

        options = decision_options("research", note_available=True)
        ids = [option.id for option in options]
        self.assertEqual(ids, [*DECISIONS, "cancel"])
        values = {option.id: option.value for option in options}
        self.assertEqual(values["cancel"], CANCEL_SENTINEL)
        for decision in DECISIONS:
            self.assertEqual(values[decision], decision)

        confirm = next(option for option in options if option.id == "research")
        self.assertIn("Confirm", confirm.label)
        self.assertIn("no override", confirm.description)
        promote = next(option for option in options if option.id == "shortlist")
        self.assertIn("Override", promote.label)
        self.assertIn("rationale", promote.description)

    def test_a_shortlist_with_no_drafted_note_says_so_on_the_option(self):
        options = decision_options("research", note_available=False)
        promote = next(option for option in options if option.id == "shortlist")
        self.assertIn("research note", promote.description)
        self.assertIn("asked for one", promote.description)

    def test_the_prompt_separates_the_engine_the_model_and_the_human(self):
        """A person authorizing an investment decision has to see who said what.

        Collapsing the engine and the model into one "system decision" is how
        the model's opinion ends up ratified as policy.
        """

        lines = [
            f"ETF: {RESOURCE}",
            "Action: commit review decision",
            "Deterministic engine (authoritative): research",
            "Model recommendation (advisory only): research",
            "Default decision: research",
        ]
        text = prompt_text(lines, "Broad global equity exposure.", choosing=True)
        self.assertIn(RESOURCE, text)
        self.assertIn("Deterministic engine (authoritative): research", text)
        self.assertIn("Model recommendation (advisory only)", text)
        self.assertIn("Default decision: research", text)
        self.assertIn("requires a rationale", text)
        self.assertIn("Nothing is bought, sold or held", text)

    def test_a_confirmation_prompt_does_not_offer_a_choice(self):
        text = prompt_text([f"ETF: {RESOURCE}"], "s", choosing=False)
        self.assertIn("Confirm this state-changing action", text)
        self.assertNotIn("choose a different one", text)

    def test_a_model_drafted_note_is_disclosed_before_approval(self):
        raw = "  Broad exposure; café-listed share class ☕  "
        note = model_supplied_note(raw)
        self.assertEqual(note, "Broad exposure; café-listed share class ☕")

        disclosure = _note_disclosure(note)
        self.assertIn(note, disclosure)
        self.assertIn("not verified by a human", disclosure)
        self.assertIn("signed and recorded verbatim", disclosure)

        shown = prompt_text([f"ETF: {RESOURCE}", disclosure], "s", choosing=True)
        self.assertIn("Model-drafted research note", shown)
        self.assertIn(note, shown)

    def test_displayed_note_exactly_equals_the_persisted_note(self):
        """The same normalized value must be shown, signed and persisted.

        A hidden or substituted payload note -- one that differs from what was
        displayed -- would mean the human approved something other than what
        actually gets signed. Covers plain text, Unicode, embedded whitespace
        and the empty-note case together so none of them can drift apart.
        """

        for raw in (" plain note ", "unicode: café ☕", "line1\nline2\t indented", "", None):
            with self.subTest(raw=raw):
                note = model_supplied_note(raw)
                persisted = action_payload(research_note=note)
                shown = prompt_text(
                    [f"ETF: {RESOURCE}", *([_note_disclosure(note)] if note else [])],
                    "s",
                    choosing=True,
                )
                if note:
                    self.assertIn(note, shown)
                    self.assertEqual(persisted["research_note"], note)
                    signed = claims(payload=persisted)
                    self.assertEqual(signed["payload"]["research_note"], note)
                    self.assertEqual(
                        payload_hash(persisted), payload_hash({"research_note": note})
                    )
                else:
                    self.assertNotIn("Model-drafted research note", shown)
                    self.assertEqual(persisted, {})

    def test_blank_payload_fields_are_omitted_rather_than_signed_empty(self):
        """`payload_str` on the Rust side treats blank as absent, so the minter
        must not ship a key the verifier will then ignore."""

        self.assertEqual(action_payload(research_note="   ", assignee=""), {})
        self.assertEqual(
            action_payload(llm_recommendation="research", research_note="  x  "),
            {"llm_recommendation": "research", "research_note": "x"},
        )

    def test_the_request_schema_rejects_a_decision_outside_the_vocabulary(self):
        for field in ("rules_decision", "requested_decision", "llm_recommendation"):
            payload = {
                "etf_id": RESOURCE,
                "rules_decision": "research",
                "requested_decision": "research",
                "summary": "s",
            }
            payload[field] = "buy"
            with self.subTest(field=field), self.assertRaises(Exception):
                CommitEvaluationRequest(**payload)

    def test_the_request_schema_rejects_an_empty_identifier_or_summary(self):
        for field in ("etf_id", "summary"):
            payload = {
                "etf_id": RESOURCE,
                "rules_decision": "research",
                "requested_decision": "research",
                "summary": "s",
            }
            payload[field] = ""
            with self.subTest(field=field), self.assertRaises(Exception):
                CommitEvaluationRequest(**payload)

    def test_a_shortlist_request_requires_a_grounded_note(self):
        with self.assertRaises(Exception):
            ShortlistEtfRequest(
                etf_id=RESOURCE, rules_decision="research", research_note="", summary="s"
            )
        request = ShortlistEtfRequest(
            etf_id=RESOURCE,
            rules_decision="research",
            research_note="Grounded in the evaluation components.",
            summary="s",
        )
        self.assertEqual(request.etf_id, RESOURCE)

    def test_an_assignment_requires_an_owner_and_carries_no_decision(self):
        with self.assertRaises(Exception):
            AssignEtfRequest(etf_id=RESOURCE, assignee="", summary="s")
        built = claims(
            action=ACTION_ASSIGN,
            choice=None,
            expected_choice=None,
            rationale=None,
            payload=action_payload(assignee="researcher-2"),
        )
        self.assertIsNone(built["choice"])
        self.assertIsNone(built["expected_choice"])
        self.assertFalse(built["override_requested"])
        self.assertEqual(built["payload"], {"assignee": "researcher-2"})

    def test_a_shortlist_action_is_minted_against_the_engines_decision(self):
        built = claims(action=ACTION_SHORTLIST, choice="shortlist", expected_choice="research")
        self.assertEqual(built["action"], ACTION_SHORTLIST)
        self.assertTrue(
            built["override_requested"],
            "shortlisting above the engine's decision is a promotion",
        )
        confirmed = claims(
            action=ACTION_SHORTLIST, choice="shortlist", expected_choice="shortlist"
        )
        self.assertFalse(confirmed["override_requested"])


class ModelFacingResultTests(unittest.TestCase):
    """An approval is not an outcome; the model must not be told otherwise."""

    def test_a_refused_change_tells_the_model_nothing_was_applied(self):
        result = json.loads(
            approval_result(
                resource_id=RESOURCE,
                action=ACTION_COMMIT,
                request_id=REQUEST_ID,
                committed=False,
                result={"refused": "Hard constraint HC-UCITS: the fund is not UCITS"},
            )
        )
        self.assertFalse(result["committed"])
        self.assertIn("NOTHING was applied", result["next_step"])
        self.assertIn("never", result["next_step"])

    def test_an_applied_change_is_reported_as_already_applied(self):
        result = json.loads(
            approval_result(
                resource_id=RESOURCE,
                action=ACTION_COMMIT,
                request_id=REQUEST_ID,
                committed=True,
                result={"new_state": "SHORTLISTED", "final_decision": "shortlist"},
            )
        )
        self.assertTrue(result["committed"])
        self.assertIn("already applied", result["next_step"])

    def test_a_cancellation_is_reported_as_unapproved(self):
        result = json.loads(cancelled(RESOURCE, ACTION_COMMIT, "The human cancelled."))
        self.assertFalse(result["approved"])
        self.assertNotIn("approval_token", result)


class ExecutionEndpointTests(unittest.TestCase):
    def test_the_execution_url_is_derived_from_the_mcp_url(self):
        previous = os.environ.get("ETF_MCP_URL")
        try:
            os.environ["ETF_MCP_URL"] = "http://mcp-server:8080/mcp"
            self.assertEqual(execute_url(), "http://mcp-server:8080/approvals/execute")
            os.environ["ETF_MCP_URL"] = "http://mcp-server:8080/mcp/"
            self.assertEqual(execute_url(), "http://mcp-server:8080/approvals/execute")
            os.environ.pop("ETF_MCP_URL")
            self.assertIsNone(execute_url())
        finally:
            if previous is None:
                os.environ.pop("ETF_MCP_URL", None)
            else:
                os.environ["ETF_MCP_URL"] = previous


class _Option:
    def __init__(self, identifier, value):
        self.id = identifier
        self.value = value


class _Prompt:
    def __init__(self, options, input_type="radio"):
        self.options = options
        self.input_type = input_type


class _Response:
    def __init__(self, selected_option, response_type="radio"):
        self.selected_option = selected_option
        self.type = response_type


class InteractionAuthorizationTests(unittest.TestCase):
    """The gap in stock NAT: two UUIDs are not authorization.

    NAT's endpoint calls resolve_interaction with no notion of who is asking and
    no notion of what the prompt offered. These assert both halves, plus that
    an offered id and an offered value only authorize a response when they came
    from the *same* offered option, not from anywhere in the offered set.
    """

    EXECUTION = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    INTERACTION = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"

    #: Deliberately asymmetric ids and values, so a bug that checks either
    #: field independently against the whole offered set (rather than the
    #: matching pair) is caught by a mismatched cross-reference.
    OPTIONS = [
        _Option("research", "RESEARCH"),
        _Option("shortlist", "SHORTLIST"),
        _Option("cancel", CANCEL_SENTINEL),
    ]

    def setUp(self):
        self.store = OwnerAwareExecutionStore(strict=False)
        self.store.record_owner_for_test(self.EXECUTION, ACTOR)
        self.store.record_offer_for_test(
            self.EXECUTION,
            self.INTERACTION,
            prompt_offer(_Prompt(self.OPTIONS)).choices,
            prompt_type="radio",
        )
        self._token = _responder.set(ACTOR)

    def tearDown(self):
        _responder.reset(self._token)

    def _authorize(self, response):
        self.store.authorize(self.EXECUTION, self.INTERACTION, response)

    def test_the_owner_may_answer_with_an_offered_choice(self):
        self._authorize(_Response(_Option("research", "RESEARCH")))
        self._authorize(_Response(_Option("shortlist", "SHORTLIST")))

    def test_cancellation_is_always_acceptable(self):
        self._authorize(_Response(_Option("cancel", CANCEL_SENTINEL)))
        # Cancellation is protocol-level, not application vocabulary: it is
        # accepted even with an id/value pair this specific prompt never
        # declared, as long as the whole selection is self-consistently a
        # cancellation.
        self._authorize(_Response(_Option(CANCEL_SENTINEL, CANCEL_SENTINEL)))
        self._authorize(_Response(_Option(None, CANCEL_SENTINEL)))

    def test_another_authenticated_user_may_not_answer(self):
        _responder.set("researcher-2")
        with self.assertRaises(InteractionAuthorizationError) as raised:
            self._authorize(_Response(_Option("shortlist", "SHORTLIST")))
        self.assertIn("not addressed to the authenticated user", str(raised.exception))

    def test_an_unauthenticated_response_is_refused(self):
        _responder.set(None)
        with self.assertRaises(InteractionAuthorizationError) as raised:
            self._authorize(_Response(_Option("shortlist", "SHORTLIST")))
        self.assertIn("authenticated identity", str(raised.exception))

    def test_a_choice_the_prompt_never_offered_is_refused(self):
        for identifier, value in (
            ("deleted", "DELETED"),
            ("escalated", "ESCALATED"),
            ("", ""),
            ("research", "research"),  # right id, wrong (lowercased) value
        ):
            with self.subTest(identifier=identifier, value=value):
                with self.assertRaises(InteractionAuthorizationError) as raised:
                    self._authorize(_Response(_Option(identifier, value)))
                self.assertIn("not offered", str(raised.exception))

    def test_offered_id_with_unoffered_value_is_refused(self):
        """A valid id does not license an arbitrary value on that option."""

        with self.assertRaises(InteractionAuthorizationError) as raised:
            self._authorize(_Response(_Option("research", "unoffered-value")))
        self.assertIn("not offered", str(raised.exception))

    def test_offered_value_with_unoffered_id_is_refused(self):
        """A valid value does not license an arbitrary id on that option."""

        with self.assertRaises(InteractionAuthorizationError) as raised:
            self._authorize(_Response(_Option("unoffered-id", "RESEARCH")))
        self.assertIn("not offered", str(raised.exception))

    def test_two_individually_offered_but_mismatched_fields_are_refused(self):
        """id from one option plus value from another must not authorize."""

        with self.assertRaises(InteractionAuthorizationError) as raised:
            self._authorize(_Response(_Option("shortlist", "RESEARCH")))
        self.assertIn("not offered", str(raised.exception))

    def test_cancel_id_with_a_real_action_value_is_refused(self):
        """The cancel sentinel on one field must not authorize a real value on
        the other: cancellation is only recognised when the *whole* selection
        is self-consistently a cancellation."""

        with self.assertRaises(InteractionAuthorizationError) as raised:
            self._authorize(_Response(_Option(CANCEL_SENTINEL, "RESEARCH")))
        self.assertIn("not offered", str(raised.exception))

    def test_cancel_value_with_an_unoffered_real_id_is_refused(self):
        with self.assertRaises(InteractionAuthorizationError) as raised:
            self._authorize(_Response(_Option("unoffered-id", CANCEL_SENTINEL)))
        self.assertIn("not offered", str(raised.exception))

    def test_wrong_response_type_is_refused(self):
        """A response shaped for a different prompt type is never valid, even
        if its selected_option happens to collide with an offered pair."""

        with self.assertRaises(InteractionAuthorizationError) as raised:
            self._authorize(_Response(_Option("research", "RESEARCH"), response_type="dropdown"))
        self.assertIn("does not match the pending prompt type", str(raised.exception))

    def test_a_choice_bearing_prompt_requires_a_selection(self):
        with self.assertRaises(InteractionAuthorizationError):
            self._authorize(_Response(None))

    def test_a_free_text_prompt_has_no_choice_set_to_validate(self):
        self.store.record_offer_for_test(
            self.EXECUTION, self.INTERACTION, None, prompt_type="text"
        )
        self._authorize(_Response(None, response_type="text"))

    def test_a_free_text_prompt_still_rejects_the_wrong_response_type(self):
        self.store.record_offer_for_test(
            self.EXECUTION, self.INTERACTION, None, prompt_type="text"
        )
        with self.assertRaises(InteractionAuthorizationError) as raised:
            self._authorize(_Response(_Option("research", "RESEARCH"), response_type="radio"))
        self.assertIn("does not match the pending prompt type", str(raised.exception))

    def test_binary_options_are_validated_not_skipped(self):
        """Boolean-valued options must still be checked, not waved through."""

        self.store.record_offer_for_test(
            self.EXECUTION,
            self.INTERACTION,
            prompt_offer(
                _Prompt([_Option("confirm", True), _Option("deny", False)], input_type="binary_choice")
            ).choices,
            prompt_type="binary_choice",
        )
        self._authorize(_Response(_Option("confirm", True), response_type="binary_choice"))
        with self.assertRaises(InteractionAuthorizationError) as raised:
            self._authorize(_Response(_Option("confirm", False), response_type="binary_choice"))
        self.assertIn("not offered", str(raised.exception))

    def test_rejection_does_not_consume_the_pending_interaction(self):
        """A rejected submission must leave the legitimate offer still usable."""

        with self.assertRaises(InteractionAuthorizationError):
            self._authorize(_Response(_Option("research", "unoffered-value")))
        # The offer is still there, and a legitimate choice still succeeds.
        self._authorize(_Response(_Option("research", "RESEARCH")))

    def test_an_unowned_interaction_is_allowed_unless_strict(self):
        lenient = OwnerAwareExecutionStore(strict=False)
        lenient.authorize("unknown-execution", "unknown-interaction", _Response(None))

        strict = OwnerAwareExecutionStore(strict=True)
        with self.assertRaises(InteractionAuthorizationError) as raised:
            strict.authorize("unknown-execution", "unknown-interaction", _Response(None))
        self.assertIn("no recorded owner", str(raised.exception))

    def test_prompt_offer_pairs_id_and_value_from_the_same_option(self):
        offer = prompt_offer(_Prompt(self.OPTIONS))
        self.assertIsNotNone(offer.choices)
        self.assertIn(OfferedChoice(id="research", value="RESEARCH"), offer.choices)
        self.assertIn(OfferedChoice(id="cancel", value=CANCEL_SENTINEL), offer.choices)
        # The flattened-union bug this replaces would also accept this cross
        # pairing; the structured offer must not contain it.
        self.assertNotIn(OfferedChoice(id="research", value="SHORTLIST"), offer.choices)

        binary_offer = prompt_offer(_Prompt([_Option("confirm", True), _Option("deny", False)]))
        self.assertIsNotNone(binary_offer.choices)
        self.assertIn(OfferedChoice(id="confirm", value="True"), binary_offer.choices)

        text_offer = prompt_offer(_Prompt(None, input_type="text"))
        self.assertIsNone(text_offer.choices)
        self.assertEqual(text_offer.prompt_type, "text")

    def test_submitted_choice_reads_id_and_value_as_one_pair(self):
        self.assertEqual(submitted_choice(_Response(_Option("research", "RESEARCH"))), OfferedChoice("research", "RESEARCH"))
        self.assertIsNone(submitted_choice(_Response(None)))


class IdentityBoundaryTests(unittest.IsolatedAsyncioTestCase):
    """Exactly one asserted identity, or the request is not served.

    Driven through the real middleware classes in the order
    ``AuthenticatedFastApiFrontEndPluginWorker.build_app`` installs them
    (service key outermost, then identity, then responder), over plain ASGI, so
    the offline suite proves the same four cases ``make auth-test`` proves
    against the running stack: no key, key without identity, key with a
    repeated identity, and key with one identity.

    NAT 1.9's own ``identity_header`` cannot be relied on for this: on the
    workflow routes its refusal is caught by the interactive runner and turned
    into a 200 response carrying a WORKFLOW_ERROR.
    """

    KEY = "an-offline-service-key"
    HEADER = IDENTITY_HEADER.encode("ascii")

    def _stack(self):
        reached: list[str | None] = []

        async def app(scope, receive, send):
            # What an approval response handler would see as its responder.
            reached.append(current_responder())
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"{}"})

        inner = ResponderIdentityMiddleware(app)
        identity = RequireIdentityHeaderMiddleware(inner, public_paths=PUBLIC_PATHS)
        return StaticServiceKeyMiddleware(identity, api_key=self.KEY, public_paths=PUBLIC_PATHS), reached

    async def _call(self, headers, path="/v1/workflow/full", scope_type="http"):
        stack, reached = self._stack()
        sent: list[dict] = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        scope = {"type": scope_type, "method": "POST", "path": path, "headers": headers}
        await stack(scope, receive, send)
        status = next((m["status"] for m in sent if m["type"] == "http.response.start"), None)
        return status, reached

    def _keyed(self, *identities: bytes):
        headers = [(b"authorization", f"Bearer {self.KEY}".encode()), (b"content-type", b"application/json")]
        return headers + [(self.HEADER, value) for value in identities]

    async def test_no_service_key_is_refused_before_identity_is_considered(self):
        status, reached = await self._call([(self.HEADER, b"researcher-1")])
        self.assertEqual(status, 401)
        self.assertEqual(reached, [])

    async def test_a_keyed_request_with_no_identity_is_refused(self):
        status, reached = await self._call(self._keyed())
        self.assertEqual(status, 401)
        self.assertEqual(reached, [], "the workflow must not run for an unattributed request")

    async def test_an_empty_or_blank_identity_is_refused(self):
        for blank in (b"", b"   "):
            status, reached = await self._call(self._keyed(blank))
            self.assertEqual(status, 401, blank)
            self.assertEqual(reached, [])

    async def test_a_repeated_identity_is_ambiguous_not_first_wins(self):
        for values in ((b"researcher-1", b"researcher-2"), (b"researcher-1", b"researcher-1")):
            status, reached = await self._call(self._keyed(*values))
            self.assertEqual(status, 401, values)
            self.assertEqual(reached, [])

    async def test_header_name_matching_is_case_insensitive_for_repeats(self):
        headers = self._keyed(b"researcher-1") + [(IDENTITY_HEADER.upper().encode(), b"researcher-2")]
        status, reached = await self._call(headers)
        self.assertEqual(status, 401)
        self.assertEqual(reached, [])

    async def test_one_identity_with_the_key_is_served_and_names_the_responder(self):
        status, reached = await self._call(self._keyed(b"  researcher-1 "))
        self.assertEqual(status, 200)
        self.assertEqual(reached, ["researcher-1"])

    async def test_the_interaction_response_route_is_covered_too(self):
        path = "/executions/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/interactions/bbbb/response"
        status, reached = await self._call(self._keyed(), path=path)
        self.assertEqual(status, 401)
        self.assertEqual(reached, [])
        status, reached = await self._call(self._keyed(b"a", b"b"), path=path)
        self.assertEqual(status, 401)

    async def test_liveness_needs_neither_key_nor_identity(self):
        for path in sorted(PUBLIC_PATHS):
            status, _ = await self._call([], path=path)
            self.assertEqual(status, 200, path)

    async def test_version_requires_an_identity_like_every_other_route(self):
        status, _ = await self._call(self._keyed(), path="/version")
        self.assertEqual(status, 401)

    async def test_a_repeated_responder_cannot_answer_an_owned_prompt(self):
        """Defence in depth: even without the outer layer, ambiguity is not identity.

        ResponderIdentityMiddleware alone must resolve a repeated header to "no
        responder", so the owner check refuses rather than taking whichever
        occurrence came first.
        """

        store = OwnerAwareExecutionStore(strict=False)
        execution, interaction = "exec-1", "int-1"
        store.record_owner_for_test(execution, ACTOR)
        store.record_offer_for_test(
            execution,
            interaction,
            frozenset({OfferedChoice("research", "research")}),
            prompt_type="radio",
        )
        outcomes: list[str] = []

        async def app(scope, receive, send):
            try:
                store.authorize(execution, interaction, _Response(_Option("research", "research")))
                outcomes.append("authorized")
            except InteractionAuthorizationError:
                outcomes.append("refused")

        middleware = ResponderIdentityMiddleware(app)

        async def receive():  # pragma: no cover - never read
            return {}

        async def send(message):  # pragma: no cover - app sends nothing
            pass

        for values in ((ACTOR.encode(), b"intruder"), (b"intruder", ACTOR.encode())):
            await middleware(
                {"type": "http", "path": "/x", "headers": [(self.HEADER, v) for v in values]},
                receive,
                send,
            )
        await middleware({"type": "http", "path": "/x", "headers": [(self.HEADER, ACTOR.encode())]}, receive, send)
        self.assertEqual(outcomes, ["refused", "refused", "authorized"])


class RealExecutionStoreRoundTripTests(unittest.IsolatedAsyncioTestCase):
    """Drive the actual NAT ``ExecutionStore`` path, not test-only accessors.

    ``record_owner_for_test``/``record_offer_for_test`` exist so the rejection
    matrix above is testable without an event loop, but they do not prove that
    a *real* ``set_interaction_required`` call records an offer this guard can
    later check, or that a *real* ``resolve_interaction`` call — the one NAT's
    own HTTP route calls — actually resolves the pending interaction's future.
    """

    async def test_a_real_prompt_is_recorded_and_a_valid_response_resolves_it(self):
        store = OwnerAwareExecutionStore(strict=False)
        record = await store.create_execution()
        store.record_owner_for_test(record.execution_id, ACTOR)

        prompt = _Prompt([_Option("research", "RESEARCH"), _Option("shortlist", "SHORTLIST")])
        pending = await store.set_interaction_required(record.execution_id, prompt)

        token = _responder.set(ACTOR)
        try:
            response = _Response(_Option("shortlist", "SHORTLIST"))
            await store.resolve_interaction(record.execution_id, pending.interaction_id, response)
        finally:
            _responder.reset(token)

        self.assertTrue(pending.future.done())
        self.assertIs(pending.future.result(), response)

    async def test_a_rejected_response_leaves_the_real_pending_future_unresolved(self):
        store = OwnerAwareExecutionStore(strict=False)
        record = await store.create_execution()
        store.record_owner_for_test(record.execution_id, ACTOR)

        prompt = _Prompt([_Option("research", "RESEARCH"), _Option("shortlist", "SHORTLIST")])
        pending = await store.set_interaction_required(record.execution_id, prompt)

        token = _responder.set(ACTOR)
        try:
            bad_response = _Response(_Option("research", "unoffered-value"))
            with self.assertRaises(InteractionAuthorizationError):
                await store.resolve_interaction(record.execution_id, pending.interaction_id, bad_response)
            self.assertFalse(pending.future.done())

            # The legitimate interaction is still usable after the rejection.
            good_response = _Response(_Option("research", "RESEARCH"))
            await store.resolve_interaction(record.execution_id, pending.interaction_id, good_response)
        finally:
            _responder.reset(token)

        self.assertTrue(pending.future.done())
        self.assertIs(pending.future.result(), good_response)

    async def test_another_users_response_is_rejected_through_the_real_store(self):
        store = OwnerAwareExecutionStore(strict=False)
        record = await store.create_execution()
        store.record_owner_for_test(record.execution_id, ACTOR)

        prompt = _Prompt([_Option("research", "RESEARCH"), _Option("shortlist", "SHORTLIST")])
        pending = await store.set_interaction_required(record.execution_id, prompt)

        token = _responder.set("researcher-2")
        try:
            with self.assertRaises(InteractionAuthorizationError):
                await store.resolve_interaction(
                    record.execution_id, pending.interaction_id, _Response(_Option("research", "RESEARCH"))
                )
        finally:
            _responder.reset(token)
        self.assertFalse(pending.future.done())


if __name__ == "__main__":
    unittest.main(verbosity=2)
