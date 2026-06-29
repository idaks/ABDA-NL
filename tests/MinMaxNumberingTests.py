import unittest
from ArgumentationSystem.Argument import Argument
from ArgumentationSystem.ArgumentationGraph import ArgumentationGraph
from ArgumentationSystem.Attack import Attack
from KnowledgeBase.StrictRule import StrictRule


class MinMaxNumberingTests(unittest.TestCase):
    # Graph: a -> b -> c <-> d
    # Grounded extension: {a}
    # Grounded labelling: ({a}, {b}, {c, d})
    def test_min_max_1(self):
        a = Argument(StrictRule([], "a"))
        b = Argument(StrictRule([], "b"))
        c = Argument(StrictRule([], "c"))
        d = Argument(StrictRule([], "d"))
        e = Argument(StrictRule([], "e"))
        f = Argument(StrictRule([], "f"))
        g = Argument(StrictRule([], "g"))
        h = Argument(StrictRule([], "h"))
        args = {a, b, c, d, e, f, g, h}

        attacks = {Attack(a, b), Attack(b, c), Attack(g, h), Attack(h, g), Attack(c, e), Attack(d, e), Attack(e, f),
                   Attack(f, e)}

        graph = ArgumentationGraph(args, attacks)
        lab = {a: "in", c: "in", f: "in", h: "in",
               b: "out", e: "out", g: "out",
               d: "undec"}
        minmax = graph.get_min_max(lab)
        self.assertEqual(minmax[a], 1)
        self.assertEqual(minmax[b], 2)
        self.assertEqual(minmax[c], 3)
        self.assertEqual(minmax[e], 4)
        self.assertEqual(minmax[g], "inf")
        self.assertEqual(minmax[h], "inf")
        self.assertFalse(d in minmax.keys())


if __name__ == '__main__':
    unittest.main()
