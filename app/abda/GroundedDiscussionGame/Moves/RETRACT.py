from GroundedDiscussionGame.Moves.Move import OpponentMove


class RETRACT(OpponentMove):

    def is_enabled(self):
        if not self.Game.Moves:
            return False
        # Has there been an CB-move?
        htb = self.Game.get_last_move_of_type("CB", self.Argument)
        if htb is None:
            return False
        # Has the argument not been retracted yet?
        if len(self.Game.get_moves_of_type("RETRACT", self.Argument)) > 0:
            return False
        # Does a conceded attacker exist?
        for attacker in htb.Argument.AttackedFromArguments:
            if attacker in self.Game.ConcededArguments:
                return True
        return False

    def action(self):
        self.Game.Moves.append(self)
        self.Game.RetractedArguments.add(self.Argument)
