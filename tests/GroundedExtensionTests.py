import unittest
from ArgumentationSystem.Argument import Argument
from ArgumentationSystem.ArgumentationGraph import ArgumentationGraph
from ArgumentationSystem.Attack import Attack
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
        x = graph.get_grounded_labelling()
        self.assertEqual(x[a], "in")
        self.assertEqual(x[b], "out")
        self.assertEqual(x[c], "undec")
        self.assertEqual(x[d], "undec")

    # Graph: a -> b -> c -> (a) (Circle)
    # Grounded extension: {}
    # Grounded labelling: ({}, {}, {a, b c})
    def test_grounded_extension_2(self):
        a = Argument(StrictRule([], "a"))
        b = Argument(StrictRule([], "b"))
        c = Argument(StrictRule([], "c"))
        args = {a, b, c}

        ab = Attack(a, b)
        bc = Attack(b, c)
        ca = Attack(c, a)
        attacks = {ab, bc, ca}

        graph = ArgumentationGraph(args, attacks)
        x = graph.get_grounded_labelling()
        self.assertEqual(x[a], "undec")
        self.assertEqual(x[b], "undec")
        self.assertEqual(x[c], "undec")

    # Graph: a -> b -> c -> d
    # Grounded extension: {a,c}
    # Grounded labelling: ({a,c}, {b,d}, {})
    def test_grounded_extension_3(self):
        a = Argument(StrictRule([], "a"))
        b = Argument(StrictRule([], "b"))
        c = Argument(StrictRule([], "c"))
        d = Argument(StrictRule([], "d"))
        args = {a, b, c, d}

        ab = Attack(a, b)
        bc = Attack(b, c)
        cd = Attack(c, d)
        attacks = {ab, bc, cd}

        graph = ArgumentationGraph(args, attacks)
        x = graph.get_grounded_labelling()
        self.assertEqual(x[a], "in")
        self.assertEqual(x[b], "out")
        self.assertEqual(x[c], "in")
        self.assertEqual(x[d], "out")


if __name__ == '__main__':
    unittest.main()
