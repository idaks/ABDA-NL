class Attack:
    def __init__(self, from_argument, to_argument):
        self.From = from_argument
        self.To = to_argument
        from_argument.AttacksArguments.add(to_argument)
        to_argument.AttackedFromArguments.add(from_argument)

    def __str__(self):
        return "[" + str(self.From) + "] -> [" + str(self.To) + "]"
