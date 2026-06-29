class Move:
    def __init__(self, game, argument):
        self.Game = game
        self.Argument = argument
        self.MoveType = self.__class__.__name__

    def is_enabled(self):
        raise NotImplementedError()

    def action(self):
        raise NotImplementedError()

    def __hash__(self):
        return hash(self.__str__())

    def __eq__(self, other):
        return hash(self) == hash(other)

    def __str__(self):
        return f"{self.__class__.__name__ } {self.Argument}"


class OpponentMove(Move):
    def is_enabled(self):
        raise NotImplementedError()

    def action(self):
        raise NotImplementedError()


class ProponentMove(Move):
    def is_enabled(self):
        raise NotImplementedError()

    def action(self):
        raise NotImplementedError()
