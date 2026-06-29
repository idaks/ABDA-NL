from Configuration import Configuration


class DefeasibleRuleSet(set):

    def __le__(self, other):
        if not self:
            return False
        if not other and self:
            return True
        if Configuration.DemocraticOrder:
            for a in self:
                if not any([a.Strength <= x.Strength for x in other]):
                    return False
            return True
        else:
            for a in self:
                if all([a.Strength <= x.Strength for x in other]):
                    return True
            return False

    def __lt__(self, other):
        return self <= other and not other <= self
