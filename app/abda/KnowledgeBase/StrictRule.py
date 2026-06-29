from KnowledgeBase.BaseRule import BaseRule
import re


class StrictRule(BaseRule):

    @staticmethod
    def parse(text):
        strict_rule_pattern = re.compile(r"(((?P<LeftSide>((-)?\w+)(\s*,\s*((-)?\w+))*))?)\s*->"
                                         r"\s*(?P<RightSide>((-)?\w+))")
        match = strict_rule_pattern.match(text)
        if not match:
            return None
        left_side = []
        if match.group("LeftSide") is not None:
            left_side = [x.strip() for x in match.group("LeftSide").split(",")]
        right_side = match.group("RightSide").strip()
        new_rule = StrictRule(left_side, right_side)
        return new_rule

    def __str__(self):
        if self.LeftSide is not None:
            return ",".join(self.LeftSide) + f" -> {self.RightSide}"
        else:
            return f"-> {self.RightSide}"
