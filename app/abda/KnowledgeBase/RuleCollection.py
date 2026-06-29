from KnowledgeBase.DefeasibleRuleSet import DefeasibleRuleSet
from KnowledgeBase.StrictRule import StrictRule


class RuleCollection(object):
    def __init__(self):
        self.StrictRules = set()
        self.DefeasibleRules = DefeasibleRuleSet()

    def get_all_rules(self):
        return self.StrictRules.union(self.DefeasibleRules)

    def is_closed_under_transposition(self):
        for sr in self.StrictRules:
            if self.get_missing_rules_for_transposition(sr):
                return False
        return True

    def get_missing_rules_for_transposition(self, rule):
        rules = set()
        for condition in rule.LeftSide:
            found = False
            other_conditions = [c for c in rule.LeftSide if c is not condition]
            for otherRule in self.StrictRules:
                if len(rule.LeftSide) != len(otherRule.LeftSide):
                    continue
                if otherRule.RightSide != self.get_negation(condition):
                    continue
                if self.get_negation(rule.RightSide) not in otherRule.LeftSide:
                    continue
                if any([c for c in other_conditions if c not in otherRule.LeftSide]):
                    continue
                found = True
            if not found:
                left_side = other_conditions + [self.get_negation(rule.RightSide)]
                new_rule = StrictRule(left_side, self.get_negation(condition))
                rules.add(new_rule)
        return rules

    def close_under_transposition(self):
        for sr in self.StrictRules:
            for r in self.get_missing_rules_for_transposition(sr):
                if r not in self.StrictRules:
                    print("Closing under transposition: added rule " + str(r))
            self.StrictRules = self.StrictRules.union(self.get_missing_rules_for_transposition(sr))

    @staticmethod
    def get_negation(statement):
        if statement.startswith("-"):
            return statement[1:]
        return "-" + statement

    def __str__(self):
        return "Strict rules:\n\r" + "\n\r".join(map(str, self.StrictRules)) \
               + "\n\rDefeasible rules:\n\r" + "\n\r".join(map(str, self.DefeasibleRules))
