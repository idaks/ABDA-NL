import cmd

from ArgumentationSystem.Argument import Argument
from GroundedDiscussionGame.Moves.HTB import HTB
from GroundedDiscussionGame.Moves.CB import CB
from GroundedDiscussionGame.Moves.CONCEDE import CONCEDE
from GroundedDiscussionGame.Moves.Move import ProponentMove, OpponentMove
from GroundedDiscussionGame.Moves.RETRACT import RETRACT


# noinspection PyMethodMayBeStatic,PyMethodMayBeStatic,PyPep8Naming,PyUnusedLocal
class GameShell(cmd.Cmd):

    def __init__(self, game, grounded_labelling, ai_player, argument):
        super().__init__()
        self.Game = game
        self.GroundedLabelling = grounded_labelling
        game.MainArgument = argument
        game.update_enabled_moves()
        self.AIPlayer = ai_player
        self.prompt = "P> " if ai_player == "O" else "O> "
        self.AIPlaying = False
        self.Argument = argument
        self.ValidMove = False

    def ai_move(self):
        self.AIPlaying = True
        # First move?
        if not self.Game.Moves:
            self.do_HTB(self.Argument)
            self.AIPlaying = False
            return
        while True:
            possible_moves = []
            for move in self.Game.EnabledMoves:
                if self.AIPlayer == "O" and isinstance(move, OpponentMove):
                    possible_moves.append(move)
                elif self.AIPlayer == "P" and isinstance(move, ProponentMove):
                    possible_moves.append(move)
            if possible_moves:
                htb_moves = [move for move in possible_moves if isinstance(move, HTB)]
                if len(htb_moves) > 1:  # lowest number strategy
                    in_argument_moves = [move for move in htb_moves if self.GroundedLabelling[move.Argument] == "in"]
                    minValue = self.Game.MinMax[in_argument_moves[0].Argument]
                    minRule = in_argument_moves[0]
                    for m in in_argument_moves:
                        if self.Game.MinMax[m.Argument] < minValue:
                            minRule = m
                    self.move(minRule)
                else:
                    self.move(possible_moves[0])
            else:
                break
        self.AIPlaying = False

    def do_HTB(self, arg):
        """HTB [statement]
            Proponent move: [statement] has to be the case. """
        self.move(HTB(self.Game, arg if isinstance(arg, Argument) else self.get_argument_by_name(arg)))

    def do_CB(self, arg):
        """CB [statement]
            Opponent move: [statement] can be the case"""
        self.move(CB(self.Game, arg if isinstance(arg, Argument) else self.get_argument_by_name(arg)))

    def do_CONCEDE(self, arg):
        """CONCEDE [statement]
            Opponent move: Agreement, that [statement] holds"""
        self.move(CONCEDE(self.Game, arg if isinstance(arg, Argument) else self.get_argument_by_name(arg)))

    def do_RETRACT(self, arg):
        """RETRACT [statement]
            Opponent move: [statement] cannot be the case"""
        self.move(RETRACT(self.Game, arg if isinstance(arg, Argument) else self.get_argument_by_name(arg)))

    def do_cancel(self, arg):
        """cancel
            Cancels the current grounded discussion game"""
        return True

    def do_show_possible_moves(self, arg):
        """show_possible_moves
        Displays a list of all possible moves the user can make"""
        number = 1
        for m in self.Game.EnabledMoves:
            print(str(number) + ": " + str(m))
            number = number + 1

    def do_do_possible_move(self, arg):
        try:
            move = self.Game.EnabledMoves[int(arg) - 1]
            human_player = "O" if self.AIPlayer == "P" else "P"
            print(human_player + "> " + str(move))
            self.move(move)
        except:
            print(str(arg) + " is not a possible move")

    def get_argument_by_name(self, name):
        for arg in self.Game.Graph.Arguments:
            if arg.Flattened == name:
                return arg

    def move(self, move):
        if self.AIPlaying:
            print(self.AIPlayer + "> " + str(move))
        self.ValidMove = self.Game.do_move(move)
        if not self.ValidMove:
            print(str(move) + " is not a valid move")

    def preloop(self):
        if not self.Game.Moves and self.AIPlayer == "P":
            self.ai_move()

    def postcmd(self, stop, line):
        if stop:
            return True
        if self.ValidMove:
            if not self.AIPlaying:
                self.ai_move()
            outcome = self.Game.get_outcome()
            if outcome is not None:
                print(outcome)
                return True
