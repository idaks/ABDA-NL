from GroundedDiscussionGame.Moves.Move import OpponentMove


class CB(OpponentMove):

    def is_enabled(self):
        if not self.Game.Moves:
            return False
        last_move = self.Game.get_last_move()
        if last_move.MoveType == "CB":
            return False
        for m in self.Game.EnabledMoves:
            if m.MoveType == "CONCEDE" or m.MoveType == "RETRACT":
                return False
        if self.Game.get_last_move_of_type("RETRACT", self.Argument):
            return False
        for move in reversed(self.Game.Moves):
            if move.MoveType == "HTB" and move.Argument not in self.Game.ConcededArguments:
                return self.Argument in move.Argument.AttackedFromArguments
        return False

    def action(self):
        self.Game.Moves.append(self)
