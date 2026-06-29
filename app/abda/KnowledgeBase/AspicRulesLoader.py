from KnowledgeBase.RuleCollection import RuleCollection
from KnowledgeBase.DefeasibleRule import DefeasibleRule
from KnowledgeBase.StrictRule import StrictRule


class InvalidRuleFileException(Exception):
    pass


def load_rules(path):
    file = open(path, "r")
    rules = RuleCollection()
    current_strength = 1
    line = file.readline()
    while line:
        if line.startswith("#"):
            line = file.readline()
            continue
        # newline -> block ended, increment strength value
        if line == "\n":
            current_strength += 1
            line = file.readline()
            continue
        strict_rule = StrictRule.parse(line)
        defeasible_rule = DefeasibleRule.parse(line, current_strength)
        if strict_rule:
            rules.StrictRules.add(strict_rule)
        elif defeasible_rule:
            rules.DefeasibleRules.add(defeasible_rule)
        else:
            raise InvalidRuleFileException(f"{line} could not be parsed.")
        line = file.readline()
    file.close()
    return rules
