from GroundedDiscussionGame.Moves.Move import ProponentMove


class HTB(ProponentMove):

    def is_enabled(self):
        # First Move?
        last_move = self.Game.get_last_move()
        if last_move is None:
            return self.Argument == self.Game.MainArgument
        for m in self.Game.EnabledMoves:
            if m.MoveType == "CONCEDE" or m.MoveType == "RETRACT":
                return False
        if last_move.MoveType == "CB" and last_move.Argument in self.Argument.AttacksArguments:
            return True
        return False

    def action(self):
        self.Game.Moves.append(self)
        if self.Game.MainArgument is None:
            self.Game.MainArgument = self.Argument

