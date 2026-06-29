from GroundedDiscussionGame.Moves.CB import CB
from GroundedDiscussionGame.Moves.RETRACT import RETRACT
from GroundedDiscussionGame.Moves.HTB import HTB
from GroundedDiscussionGame.Moves.CONCEDE import CONCEDE


class Game:

    def __init__(self, graph, main_argument, grounded_labeling, min_max):
        self.Graph = graph
        self.Moves = []
        self.EnabledMoves = []
        self.MainArgument = main_argument
        self.OpenArguments = set()
        self.RetractedArguments = set()
        self.ConcededArguments = set()
        self.GroundedLabeling = grounded_labeling
        self.MinMax = min_max
        self.update_enabled_moves()

    def do_move(self, move):
        if move in self.EnabledMoves:
            move.action()
            self.update_enabled_moves()
            return True
        return False

    def update_enabled_moves(self):
        self.EnabledMoves.clear()
        available_moves = [HTB, CONCEDE, RETRACT, CB]
        for arg in self.Graph.Arguments:
            for move in available_moves:
                if move(self, arg).is_enabled():
                    self.EnabledMoves.append(move(self, arg))

    def get_last_move(self):
        return self.Moves[len(self.Moves) - 1] if len(self.Moves) > 0 else None

    def get_last_move_of_type(self, move_type, argument=None):
        for move in reversed(self.Moves):
            if move.MoveType == move_type:
                if argument is None:
                    return move
                elif move.Argument == argument:
                    return move
        return None

    def get_moves_of_type(self, move_type, argument=None):
        moves = []
        for move in self.Moves:
            if move.MoveType == move_type:
                if argument is None:
                    moves.append(move)
                elif move.Argument == argument:
                    moves.append(move)
        return moves

    def get_outcome(self):
        # Main argument conceded -> proponent has won
        if self.MainArgument in self.ConcededArguments:
            return f"Proponent has won: {self.MainArgument} has been conceded."
        # Main argument not conceded and no more moves possible -> opponent has won
        if not self.EnabledMoves:
            return f"Opponent has won: {self.MainArgument} has not been conceded and no more moves are possible"
        for a in self.Graph.Arguments:
            htb_moves_count = len(self.get_moves_of_type("HTB", a))
            cb_moves_count = len(self.get_moves_of_type("CB", a))
            if htb_moves_count > 1:
                return f"Opponent has won: HTB({a.Flattened}) has been uttered more than once."
            if cb_moves_count > 1:
                return f"Opponent has won: CB({a.Flattened}) has been uttered more than once."
            if htb_moves_count > 0 and cb_moves_count > 0:
                return f"Opponent has won: proponent has contradicted himself."
        return None

