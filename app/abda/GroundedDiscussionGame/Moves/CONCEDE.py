from GroundedDiscussionGame.Moves.Move import OpponentMove


class CONCEDE(OpponentMove):

    def is_enabled(self):
        if not self.Game.Moves:
            return False
        # Has there been an HTB-move?
        htb_move = self.Game.get_last_move_of_type("HTB", self.Argument)
        if htb_move is None:
            return False
        # Has the argument not been conceded yet?
        if len(self.Game.get_moves_of_type("CONCEDE", self.Argument)) > 0:
            return False
        # Is every attacker retracted?
        for attacker in htb_move.Argument.AttackedFromArguments:
            if attacker not in self.Game.RetractedArguments:
                return False
        return True

    def action(self):
        self.Game.Moves.append(self)
        self.Game.ConcededArguments.add(self.Argument)
