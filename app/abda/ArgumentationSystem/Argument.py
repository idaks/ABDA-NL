from Configuration import Configuration
from KnowledgeBase.DefeasibleRule import DefeasibleRule
from KnowledgeBase.DefeasibleRuleSet import DefeasibleRuleSet


class Argument(object):

    def __init__(self, rule, arguments=None):
        self.BuildFromArguments = arguments
        # ASPIC Definitions
        self.TopRule = rule
        self.Conclusion = rule.RightSide
        # Flattened must be computed before any self.Sub.add(self) call,
        # because __hash__ reads Flattened to index the argument in a set.
        self.Flattened = self.get_flattened_tree(self.TopRule)
        self.Sub = set()
        if arguments:
            for arg in arguments:
                self.Sub = self.Sub.union(arg.Sub)
        self.Sub.add(self)
        self.DefRules = DefeasibleRuleSet()
        if arguments:
            for arg in arguments:
                self.DefRules.update(arg.DefRules)
        if isinstance(rule, DefeasibleRule):
            self.DefRules.add(rule)
        self.LastDefRules = DefeasibleRuleSet()
        if isinstance(rule, DefeasibleRule):
            self.LastDefRules.add(rule)
        elif arguments:
            for arg in arguments:
                self.LastDefRules.update(arg.LastDefRules)
        # Graph/Argumentation System properties
        self.AttacksArguments = set()
        self.AttackedFromArguments = set()

    def get_flattened_tree(self, rule):
        output_string = "=>" if isinstance(rule, DefeasibleRule) else "->"
        output_string += rule.RightSide
        # Include the rule's identity so distinct rules with identical
        # premise/conclusion shape (e.g., two bodyless assumptions that
        # conclude the same literal) don't collide in Flattened -- which
        # would collapse them into one argument via __hash__/__eq__.
        rule_id = getattr(rule, "_scenario_id", None) or getattr(rule, "Name", None) or ""
        if rule_id:
            output_string += f"[{rule_id}]"
        left_side_string = []
        if self.BuildFromArguments:
            for condition in rule.LeftSide:
                for s in self.BuildFromArguments:
                    if s.Conclusion == condition:
                        left_side_string.append(f"({s.Flattened})")
                        break
        if left_side_string:
            if len(left_side_string) > 1:
                output_string = "(" + ",".join(left_side_string) + ")" + output_string
            else:
                output_string = left_side_string[0] + output_string
        return output_string

    def dump(self):
        print("Argument " + str(self))
        print("Conclusion: " + str(self.Conclusion))
        print("SubArguments: {" + '; '.join((str(s) for s in self.Sub)) + "}")
        print("DefeasibleRules: {" + '; '.join((str(d) for d in self.DefRules)) + "}")
        print("LastDefeasibleRules: {" + '; '.join((str(l) for l in self.LastDefRules)) + "}")
        print("TopRule: " + str(self.TopRule))


    def __le__(self, other):
        if Configuration.WeakestLink:
            return self.DefRules <= other.DefRules
        else:
            return self.LastDefRules <= other.LastDefRules

    def __lt__(self, other):
        return self <= other and not other <= self

    def __str__(self):
        if not self.Flattened: 
            self.Flattened = self.get_flattened_tree(self.TopRule)
        return str(self.Flattened)

    def __hash__(self):
        # Flattened encodes the full recursive derivation tree (top rule +
        # each sub-argument's Flattened, in LeftSide order). The earlier
        # implementation hashed only immediate-sub TopRule strings, which
        # collided arguments that shared a top rule but diverged deeper --
        # e.g., two derivations of the same proposition via distinct
        # sub-argument chains got collapsed into one set entry.
        return hash(self.Flattened)

    def __eq__(self, other):
        return self.Flattened == other.Flattened
