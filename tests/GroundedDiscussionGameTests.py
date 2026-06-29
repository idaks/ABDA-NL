import unittest

from ArgumentationSystem.Argument import Argument
from ArgumentationSystem.ArgumentationGraph import ArgumentationGraph
from ArgumentationSystem.Attack import Attack
from GroundedDiscussionGame.Game import Game
from GroundedDiscussionGame.GameShell import GameShell
from GroundedDiscussionGame.Moves.CONCEDE import CONCEDE
from GroundedDiscussionGame.Moves.HTB import HTB
from KnowledgeBase.StrictRule import StrictRule


class GroundedExtensionTests(unittest.TestCase):

    # Graph: a -> b -> c <-> d
    # Grounded extension: {a}
    # Grounded labelling: ({a}, {b}, {c, d})
    def test_grounded_extension_1(self):
        a = Argument(StrictRule([], "a"))
        b = Argument(StrictRule([], "b"))
        c = Argument(StrictRule([], "c"))
        d = Argument(StrictRule([], "d"))
        args = {a, b, c, d}

        ab = Attack(a, b)
        bc = Attack(b, c)
        cd = Attack(c, d)
        dc = Attack(d, c)
        attacks = {ab, bc, cd, dc}

        graph = ArgumentationGraph(args, attacks)
        labelling = graph.get_grounded_labelling()
        min_max = graph.get_min_max(labelling)

        game = Game(graph, a, labelling, min_max)
        # Game for in-labelled argument a
        game_shell = GameShell(game, labelling, "O", a)
        self.assertIn(HTB(game, a), game.EnabledMoves)
        game_shell.onecmd("HTB ->a")
        # Only CONCEDE(a) possible
        self.assertIn(CONCEDE(game, a), game.EnabledMoves)
        self.assertTrue(len(game.EnabledMoves) == 1)
        game_shell.ai_move()
        self.assertFalse(game.EnabledMoves)

        # TODO this test is broken :(
        # game.reset()
        # Game for out-labelled argument b
        # game_shell = GameShell(game, "P", b)
        # self.assertIn(HTB(game, b), game.EnabledMoves)
        # game_shell.ai_move()
        # Only CB(a) possible
        # self.assertIn(CB(game, a), game.EnabledMoves)
        # self.assertTrue(len(game.EnabledMoves) == 1)
        # game_shell.onecmd("CB ->a")
