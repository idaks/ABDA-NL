"""Tests asserting invariants ABDA's engine must uphold for the NL app.

Not tests of ABDA per se but of the specific behaviors the scenario
substrate relies on. If the engine is ever replaced or upgraded, these
guard the contract.
"""
from __future__ import annotations

import pytest

from app.abda_bridge import (
    ArgumentationGraph,
    build_arguments,
    build_attacks,
    init_engine,
)
from app.scenario.loader import scenario_from_dict, scenario_to_rule_collection

from ArgumentationSystem.ArgumentBuilder import ArgumentConstructionError  # noqa: E402


def _label_by_conclusion(raw: dict) -> dict[str, set[str]]:
    """Build the AF from a raw scenario dict and return {conclusion: {labels}}.

    Aggregates argument labels per conclusion literal -- a conclusion is
    considered supported ("in") iff at least one argument for it is in.
    """
    scenario = scenario_from_dict(raw)
    rc = scenario_to_rule_collection(scenario)
    args = build_arguments(rc.get_all_rules())
    build_attacks(args)  # wires AttacksArguments / AttackedFromArguments
    labelling = ArgumentationGraph(args, None).get_grounded_labelling()
    by_concl: dict[str, set[str]] = {}
    for arg, label in labelling.items():
        by_concl.setdefault(arg.Conclusion, set()).add(label)
    return by_concl


@pytest.fixture(scope="module", autouse=True)
def _engine():
    init_engine()


def test_rule_with_multi_candidate_premise_enumerates_all_derivations():
    """When a rule's premise has multiple sub-argument derivations,
    ``build_arguments`` must generate the full Cartesian product of premise
    fulfillments -- one argument per (sub-arg-for-p1, sub-arg-for-p2, ...)
    tuple.

    Regression guard: ABDA's original build_arguments greedy-picked one
    sub-arg per premise and under-enumerated arguments when premises had
    uneven candidate counts, introducing Python-hash-seed nondeterminism
    in argument count and AF topology.

    Minimal failing scenario:
      facts: a, b, c
      r1: [a] => m    # two ways to derive m
      r2: [b] => m
      r3: [m, c] => n

    Arguments for n should be {r3(r1(a), c), r3(r2(b), c)} -- two total.
    """
    raw = {
        "title": "multi-candidate-premise",
        "facts": {
            "a": {"description": "a"},
            "b": {"description": "b"},
            "c": {"description": "c"},
        },
        "propositions": {"m": {"description": "m"}},
        "conclusions": {"n": {"description": "n"}},
        "rules": {
            "r1": {"type": "defeasible", "premises": ["a"], "conclusion": "m"},
            "r2": {"type": "defeasible", "premises": ["b"], "conclusion": "m"},
            "r3": {"type": "defeasible", "premises": ["m", "c"], "conclusion": "n"},
        },
    }
    scenario = scenario_from_dict(raw)
    rc = scenario_to_rule_collection(scenario)
    args = build_arguments(rc.get_all_rules())
    n_args = [a for a in args if a.Conclusion == "n"]
    assert len(n_args) == 2, (
        f"expected 2 arguments for n (one per m-derivation); got {len(n_args)}:\n"
        + "\n".join(f"  {a}" for a in n_args)
    )


def test_rule_with_three_premises_each_two_candidates_produces_eight_args():
    """Three premises with 2 candidates each → full Cartesian = 2 * 2 * 2 = 8
    arguments, even though each premise can be satisfied in at most one way
    per greedy pass. Tighter guard against partial enumeration.
    """
    raw = {
        "title": "three-premise-multi-candidate",
        "facts": {
            "a1": {"description": "a1"}, "a2": {"description": "a2"},
            "b1": {"description": "b1"}, "b2": {"description": "b2"},
            "c1": {"description": "c1"}, "c2": {"description": "c2"},
        },
        "propositions": {
            "p": {"description": "p"},
            "q": {"description": "q"},
            "r": {"description": "r"},
        },
        "conclusions": {"z": {"description": "z"}},
        "rules": {
            "rp1": {"type": "defeasible", "premises": ["a1"], "conclusion": "p"},
            "rp2": {"type": "defeasible", "premises": ["a2"], "conclusion": "p"},
            "rq1": {"type": "defeasible", "premises": ["b1"], "conclusion": "q"},
            "rq2": {"type": "defeasible", "premises": ["b2"], "conclusion": "q"},
            "rr1": {"type": "defeasible", "premises": ["c1"], "conclusion": "r"},
            "rr2": {"type": "defeasible", "premises": ["c2"], "conclusion": "r"},
            "rz": {"type": "defeasible", "premises": ["p", "q", "r"], "conclusion": "z"},
        },
    }
    scenario = scenario_from_dict(raw)
    rc = scenario_to_rule_collection(scenario)
    args = build_arguments(rc.get_all_rules())
    z_args = [a for a in args if a.Conclusion == "z"]
    assert len(z_args) == 8, (
        f"expected 8 arguments for z (2x2x2 Cartesian); got {len(z_args)}"
    )


def test_two_assumptions_sharing_conclusion_produce_distinct_arguments():
    """Two bodyless assumptions concluding the same literal must build two
    distinct arguments (one per assumption). Regression guard: before the
    rule-identity addition to Flattened, ``Argument.__hash__`` collapsed
    them into one set entry because both Flattened strings rendered as
    ``=>p``.
    """
    raw = {
        "title": "two-assumptions-one-conclusion",
        "assumptions": {
            "a1": {"description": "first assumption"},
            "a2": {"description": "second assumption"},
        },
        "propositions": {"p": {"description": "p"}},
        "conclusions": {"c": {"description": "c"}},
        "rules": {
            # Bodyless rules authored under `rules:` with empty premises
            # aren't directly supported by the schema, so we route a1/a2
            # through defeasible rules that map each assumption to p.
            "r1": {"type": "defeasible", "premises": ["a1"], "conclusion": "p"},
            "r2": {"type": "defeasible", "premises": ["a2"], "conclusion": "p"},
            "r3": {"type": "defeasible", "premises": ["p"], "conclusion": "c"},
        },
    }
    scenario = scenario_from_dict(raw)
    rc = scenario_to_rule_collection(scenario)
    args = build_arguments(rc.get_all_rules())
    assumption_args = [a for a in args if a.Conclusion in ("a1", "a2")]
    # Two assumptions -> two bodyless defeasible args, neither deduped.
    assert len(assumption_args) == 2
    top_rule_names = {a.TopRule.Name for a in assumption_args}
    assert top_rule_names == {"a1", "a2"}


def test_self_referential_rule_does_not_appear_twice_on_any_branch():
    """A rule may reappear across branches of a derivation but not within
    one. Per Caminada et al. (2015), Def 7 footnote 6: this prevents a
    finite rule set from generating infinitely many arguments.

    Scenario:
      fact: seed
      r_seed: [seed] => p      # one finite path to p
      r_loop: [p] => p         # self-referential in p; allowed to fire once
      r_c: [p] => c

    Expected arguments: exactly 5 --
      - A_seed (from fact seed)
      - A_p_via_seed (r_seed applied to A_seed)
      - A_p_via_loop (r_loop applied to A_p_via_seed; legal because r_loop
        is not yet in A_p_via_seed's subtree)
      - A_c_via_seed (r_c applied to A_p_via_seed)
      - A_c_via_loop (r_c applied to A_p_via_loop)
    Crucially, there is no A with r_loop applied twice on a single path
    (e.g., r_loop(r_loop(A_p_via_seed)) is rejected).
    """
    raw = {
        "title": "self-referential",
        "facts": {"seed": {"description": "seed"}},
        "propositions": {"p": {"description": "p"}},
        "conclusions": {"c": {"description": "c"}},
        "rules": {
            "r_seed": {"type": "defeasible", "premises": ["seed"], "conclusion": "p"},
            "r_loop": {"type": "defeasible", "premises": ["p"], "conclusion": "p"},
            "r_c": {"type": "defeasible", "premises": ["p"], "conclusion": "c"},
        },
    }
    scenario = scenario_from_dict(raw)
    rc = scenario_to_rule_collection(scenario)
    args = build_arguments(rc.get_all_rules())
    assert len(args) == 5, (
        f"expected 5 arguments, got {len(args)}:\n"
        + "\n".join(f"  {a}" for a in sorted(args, key=str))
    )
    # No argument's derivation tree uses r_loop more than once.
    for arg in args:
        loop_uses = sum(
            1 for s in arg.Sub if getattr(s.TopRule, "_scenario_id", None) == "r_loop"
        )
        assert loop_uses <= 1, (
            f"argument {arg} uses r_loop {loop_uses} times on one branch"
        )


def test_undercut_of_one_derivation_leaves_conclusion_supported():
    """Two derivations of ``p``; one uses a rule that is undercut, the
    other does not. A downstream rule ``rq: p => q`` fires via each. The
    conclusion ``q`` must be labelled *in* because the surviving derivation
    of ``p`` supports an un-attacked argument for ``q``.

    Regression guard (Martin Caminada's concern, 2026-04): under the old
    greedy sub-argument selection, ``build_arguments`` could pick the
    undercut derivation as the sole witness for ``p`` in ``rq``'s
    premises, leaving no un-attacked argument for ``q``. The Cartesian
    enumeration from commit 50fdee5 makes this impossible: every
    derivation path exists as its own argument, and grounded labelling
    sees both.

    Setup:
      fact: fa, fb
      r1: fa => p       # defeasible, name 'r1' -- undercuttable
      r2: fb => p       # defeasible, name 'r2' -- not undercut
      rq: p => q
      u:  => -r1        # bodyless defeasible rule: an assumption for -r1
    """
    raw = {
        "title": "undercut-one-of-two-derivations",
        "facts": {
            "fa": {"description": "fa"},
            "fb": {"description": "fb"},
        },
        "propositions": {"p": {"description": "p"}},
        "conclusions": {"q": {"description": "q"}},
        "rules": {
            "r1": {"type": "defeasible", "premises": ["fa"], "conclusion": "p"},
            "r2": {"type": "defeasible", "premises": ["fb"], "conclusion": "p"},
            "rq": {"type": "defeasible", "premises": ["p"], "conclusion": "q"},
            "u": {"type": "defeasible", "premises": [], "conclusion": "-r1"},
        },
    }
    labels = _label_by_conclusion(raw)
    # Both arg-objects for q exist: q-via-r1 is undercut (out), q-via-r2
    # is unattacked (in). q is supported iff at least one is in.
    assert labels.get("q") == {"in", "out"}, (
        f"expected q labels {{'in','out'}} (one arg per derivation); "
        f"got {labels.get('q')}"
    )
    # Same story for p: out via r1 (undercut), in via r2.
    assert labels.get("p") == {"in", "out"}, (
        f"expected p labels {{'in','out'}}; got {labels.get('p')}"
    )
    # The undercut argument -r1 is itself in (nothing attacks it).
    assert labels.get("-r1") == {"in"}


def test_rebut_defeats_weak_derivation_but_stronger_survives():
    """Two derivations of ``p`` with different strengths; an attacker
    concluding ``-p`` sits strictly between them. Under weakest-link +
    democratic ordering the attacker defeats the weak derivation but not
    the strong one, so ``q = rq(p)`` remains supported via the strong path.

    Regression guard (Martin Caminada's concern, 2026-04): a greedy
    selector that materialised only one derivation per premise could
    leave ``q`` vulnerable if it happened to pick the weak derivation.
    Cartesian enumeration means both paths are built and labelled
    independently.

    Strength layout:
      r1: fa => p      block 1  (weak)
      r2: fb => p      block 3  (strong)
      r_att: fc => -p  block 2  (middle -- beats r1, loses to r2)
      rq: p => q       block 1
    """
    raw = {
        "title": "rebut-beats-weak-loses-to-strong",
        "facts": {
            "fa": {"description": "fa"},
            "fb": {"description": "fb"},
            "fc": {"description": "fc"},
        },
        "propositions": {"p": {"description": "p"}},
        "conclusions": {"q": {"description": "q"}},
        "rules": {
            "r1": {
                "type": "defeasible",
                "premises": ["fa"],
                "conclusion": "p",
                "block": 1,
            },
            "r2": {
                "type": "defeasible",
                "premises": ["fb"],
                "conclusion": "p",
                "block": 3,
            },
            "r_att": {
                "type": "defeasible",
                "premises": ["fc"],
                "conclusion": "-p",
                "block": 2,
            },
            "rq": {
                "type": "defeasible",
                "premises": ["p"],
                "conclusion": "q",
                "block": 1,
            },
        },
    }
    labels = _label_by_conclusion(raw)
    # Both arg-objects for q end up in: the strong A2 rebuts r_att
    # unilaterally (A2 is block 3, r_att is block 2), knocking r_att out;
    # with r_att out, both A_q_via_r1 and A_q_via_r2 have no live
    # attackers. The case for "in" labelling on q is robust either way --
    # what matters is that q is supported and r_att does not block it.
    assert labels.get("q") == {"in"}, (
        f"expected q labels {{'in'}}; got {labels.get('q')}"
    )
    # -p is labelled out because the stronger A2 rebuts it successfully.
    assert labels.get("-p") == {"out"}, (
        f"expected -p labels {{'out'}}; got {labels.get('-p')}"
    )


def test_rule_may_reappear_in_different_branches():
    """The Def 7 footnote 6 example: for P = {a <= b; b <= d; c <= d; d},
    the argument (a <= (b <= (d))) combined with (c <= (d)) uses the rule
    for d in two sibling branches. That must be allowed.
    """
    raw = {
        "title": "shared-rule-across-branches",
        "facts": {"d_fact": {"description": "d fact"}},
        "propositions": {
            "a": {"description": "a"},
            "b": {"description": "b"},
            "c": {"description": "c"},
            "d": {"description": "d"},
        },
        "conclusions": {"joined": {"description": "joined conclusion"}},
        "rules": {
            "r_d": {"type": "defeasible", "premises": ["d_fact"], "conclusion": "d"},
            "r_b": {"type": "defeasible", "premises": ["d"], "conclusion": "b"},
            "r_c": {"type": "defeasible", "premises": ["d"], "conclusion": "c"},
            "r_a": {"type": "defeasible", "premises": ["b"], "conclusion": "a"},
            # r_joined uses both b-via-d and c-via-d, so r_d appears twice
            # across sibling branches -- must be built.
            "r_joined": {
                "type": "defeasible",
                "premises": ["a", "c"],
                "conclusion": "joined",
            },
        },
    }
    scenario = scenario_from_dict(raw)
    rc = scenario_to_rule_collection(scenario)
    args = build_arguments(rc.get_all_rules())
    joined_args = [a for a in args if a.Conclusion == "joined"]
    # A_joined exists: the cycle check must NOT reject the combo just
    # because r_d appears in both sub-arg subtrees (that's the
    # "different-branches-OK" rule from Def 7 footnote 6). If it were
    # over-restrictive, joined_args would be empty.
    assert len(joined_args) == 1
    joined = joined_args[0]
    # r_d is one unique argument object referenced from both A_a's and
    # A_c's chains; in Sub-set terms it appears once, but it is reachable
    # via both branches from A_joined.
    scenario_ids_in_sub = {
        getattr(s.TopRule, "_scenario_id", None) for s in joined.Sub
    }
    assert "r_d" in scenario_ids_in_sub
