from KnowledgeBase.BaseRule import BaseRule
import re


class DefeasibleRule(BaseRule):
    def __init__(self, left_side, right_side, name, strength):
        self.Strength = strength
        self.Name = name
        super(DefeasibleRule, self).__init__(left_side, right_side)

    def __str__(self):
        return ",".join(self.LeftSide) + f" => {self.RightSide}"

    @staticmethod
    def parse(text, strength):
        defeasible_rule_pattern = re.compile(r"(((?P<LeftSide>((-)?\w+)(\s*,\s*((-)?\w+))*))?)\s*=>"
                                             r"\s*(?P<RightSide>((-)?\w+))\s*(\[(?P<Name>(-)?\w+)\])?")
        match = defeasible_rule_pattern.match(text)
        if not match:
            return None
        left_side = []
        if match.group("LeftSide") is not None:
            left_side = [x.strip() for x in match.group("LeftSide").split(",")]
        right_side = match.group("RightSide")
        name = match.group("Name")
        if not name:
            name = ""
        new_rule = DefeasibleRule(left_side, right_side.strip(), name, strength)
        return new_rule
