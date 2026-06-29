from itertools import product

from ArgumentationSystem.Argument import Argument
from KnowledgeBase.DefeasibleRule import DefeasibleRule
from ArgumentationSystem.Attack import Attack
from Configuration import Configuration


def _would_create_cycle(rule, combo):
    """True iff ``rule`` already appears as the top rule of some argument in
    any sub-argument's transitive Sub set.

    Implements the well-formedness constraint from Caminada et al. (2015),
    Definition 7, footnote 6: a rule may reappear across different branches
    of a derivation but never twice on the same root-to-leaf path. Applying
    ``rule`` on top of ``combo`` would place ``rule`` at the new root; if
    any sub-argument in ``combo`` has ``rule`` somewhere in its subtree,
    the resulting argument would violate the constraint -- and admitting
    it would also let the fixpoint diverge on self-referential chains.
    """
    for sub_arg in combo:
        for s in sub_arg.Sub:
            if s.TopRule is rule:
                return True
    return False


def get_applicable_argument_tuples(rule, arguments):
    """Yield every valid tuple of sub-arguments that satisfies ``rule``'s
    premises.

    For each condition in ``rule.LeftSide`` (in order), collect every
    candidate sub-argument whose conclusion matches, then yield the
    Cartesian product filtered by the well-formedness constraint from
    Caminada et al. (2015), Definition 7 footnote 6 (no rule repeats on
    a single root-to-leaf path). Returns ``None`` when any premise has no
    candidate (the rule cannot fire at all).

    Replaces an earlier greedy selection that picked the first matching
    sub-argument per premise from an unordered set and under-enumerated
    arguments for rules with uneven premise-candidate counts -- introducing
    Python-hash-seed nondeterminism in the resulting AF.
    """
    candidate_lists = []
    for condition in rule.LeftSide:
        candidates = [a for a in arguments if a.Conclusion == condition]
        if not candidates:
            return None
        candidate_lists.append(candidates)
    return (
        combo for combo in product(*candidate_lists)
        if not _would_create_cycle(rule, combo)
    )


# Bound the fixpoint loop. In a well-founded rule set, argument construction
# converges in at most O(depth-of-rule-graph) iterations -- single-digit for
# the scenarios we ship. A self-referential chain (e.g., rule p => p) makes
# the loop add strictly deeper Flattened trees forever; bound + explicit
# error is preferable to infinite hang.
MAX_BUILD_ITERATIONS = 100


class ArgumentConstructionError(Exception):
    """Raised when build_arguments fails to converge within MAX_BUILD_ITERATIONS.

    Typically indicates a self-referential rule chain (some conclusion is
    transitively a premise of its own derivation). The scenario-level fix
    is to reject such chains at the scenario-integrity layer; the engine
    guards against runaway construction as a defensive fallback.
    """


def build_arguments(rules):
    arguments = set()
    # Bodyless rules -> ground arguments (facts / assumptions).
    for rule in set(filter(lambda r: not r.LeftSide, rules)):
        arguments.add(Argument(rule))

    # Fixpoint: re-enumerate every rule until no new arguments appear.
    old_size = -1
    iterations = 0
    while old_size != len(arguments):
        iterations += 1
        if iterations > MAX_BUILD_ITERATIONS:
            raise ArgumentConstructionError(
                f"argument construction did not converge after "
                f"{MAX_BUILD_ITERATIONS} iterations ({len(arguments)} arguments "
                "built so far); likely a self-referential rule chain"
            )
        old_size = len(arguments)
        for rule in rules:
            if not rule.LeftSide:
                continue
            tuples = get_applicable_argument_tuples(rule, arguments)
            if tuples is None:
                continue
            for combo in tuples:
                arguments.add(Argument(rule, set(combo)))
    return arguments


def build_attacks(arguments):
    attacks = set()
    for a in arguments:
        for b in arguments:
            if does_attacks(a, b):
                attacks.add(Attack(a, b))
    return attacks


def does_attacks(a, b):
    # Undercutting?
    for b1 in b.Sub:
        if isinstance(b1.TopRule, DefeasibleRule) and is_negation(b1.TopRule.Name, a.Conclusion):
            if Configuration.Verbose:
                print(str(a) + " undercuts " + str(b) + " on " + str(b1.TopRule.Name))
            return True
    # Rebutting?
    for b1 in b.Sub:
        if  is_negation(a.Conclusion, b1.Conclusion) and not a < b1:
            if Configuration.Verbose:
                print(str(a) + " rebuts " + str(b) + " on " + str(b1))
            return True
    return False


def is_negation(a, b):
    return a == "-" + b or b == "-" + a
