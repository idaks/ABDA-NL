class ArgumentationGraph:
    def __init__(self, arguments, attacks):
        self.Arguments = arguments
        self.Attacks = attacks

    def get_grounded_labelling(self):
        # Get all arguments which are not attacked
        in_arguments = set(filter(lambda a: not a.AttackedFromArguments, self.Arguments))
        out_arguments = set()
        changes = True
        while changes:
            changes = False
            for x in list(a.AttacksArguments for a in in_arguments):
                for y in x:
                    if y not in out_arguments:
                        out_arguments.add(y)
                        changes = True
            for x in list(a for a in self.Arguments if all(b in out_arguments for b in a.AttackedFromArguments)):
                if x not in in_arguments:
                    in_arguments.add(x)
                    changes = True

        labelling = dict()
        for a in self.Arguments:
            labelling[a] = "undec"
        for a in in_arguments:
            labelling[a] = "in"
            for b in a.AttacksArguments:
                labelling[b] = "out"
        return labelling

    @staticmethod
    def get_min_max(labelling):
        in_arguments = set(filter(lambda a: labelling[a] == "in", labelling.keys()))
        out_arguments = set(filter(lambda a: labelling[a] == "out", labelling.keys()))
        min_max = dict()
        changes = True
        while changes:
            changes = False
            unnumbered_in = set(filter(lambda a: a not in min_max.keys()
                                and all(b in out_arguments and b in min_max.keys() for b in a.AttackedFromArguments),
                                in_arguments))
            for arg in unnumbered_in:
                min_max[arg] = max([min_max[a] for a in arg.AttackedFromArguments if a in out_arguments], default=0) + 1
                changes = True

            unnumbered_out = set(filter(lambda a: a not in min_max.keys()
                                 and any(b in in_arguments and b in min_max.keys() for b in a.AttackedFromArguments),
                                 out_arguments))
            for arg in unnumbered_out:
                min_max[arg] = min([min_max[a] for a in arg.AttackedFromArguments if a in in_arguments
                                    and a in min_max.keys()], default=0) + 1
                changes = True

        # Assign "inf" to in/out arguments that couldn't be numbered (in cycles)
        for a in in_arguments | out_arguments:
            if a not in min_max:
                min_max[a] = "inf"
        return min_max
