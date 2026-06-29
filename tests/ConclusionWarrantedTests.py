import os
import unittest

from Configuration import Configuration
from ABDAShell import ABDAShell
from ArgumentationSystem.ArgumentBuilder import build_arguments, build_attacks
from ArgumentationSystem.ArgumentationGraph import ArgumentationGraph
from KnowledgeBase.AspicRulesLoader import load_rules

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))


class ConclusionWarrantedTests(unittest.TestCase):
    @staticmethod
    def bootstrap_shell(file, wl, do):
        # Set configuration (so DefeasibleRuleCollection and Argument can access it)
        Configuration.WeakestLink = wl
        Configuration.DemocraticOrder = do

        # Load rules from file
        rules = load_rules(os.path.join(TESTS_DIR, file))

        # Build argumentation system
        arguments = build_arguments(rules.get_all_rules())
        attacks = build_attacks(arguments)
        graph = ArgumentationGraph(arguments, attacks)

        # Compute grounded extension and minMax - Numbering
        grounded_labelling = graph.get_grounded_labelling()
        min_max_numbering = graph.get_min_max(grounded_labelling)

        # Start shell for user interaction
        return ABDAShell(graph, grounded_labelling, min_max_numbering)

    def test_abstract_2_cycle_broken(self):
        for wl in [True, False]:
            for do in [True, False]:
                shell = self.bootstrap_shell("RuleFiles/test_abstract_2_cycle_broken.txt", wl, do)
                self.assertTrue(shell.is_warranted("a"))

    def test_abstract_2_cycle_unbroken(self):
        for wl in [True, False]:
            for do in [True, False]:
                shell = self.bootstrap_shell("RuleFiles/test_abstract_2_cycle_unbroken.txt", wl, do)
                self.assertFalse(shell.is_warranted("a"))

    def test_abstract_6_cycle_broken(self):
        for wl in [True, False]:
            for do in [True, False]:
                shell = self.bootstrap_shell("RuleFiles/test_abstract_6_cycle_broken.txt", wl, do)
                self.assertTrue(shell.is_warranted("a"))

    def test_abstract_6_cycle_unbroken(self):
        for wl in [True, False]:
            for do in [True, False]:
                shell = self.bootstrap_shell("RuleFiles/test_abstract_6_cycle_unbroken.txt", wl, do)
                self.assertFalse(shell.is_warranted("a"))

    def test_abstract_exists_in(self):
        for wl in [True, False]:
            for do in [True, False]:
                shell = self.bootstrap_shell("RuleFiles/test_abstract_exists_in.txt", wl, do)
                self.assertTrue(shell.is_warranted("j"))

    def test_abstract_forall_out(self):
        for wl in [True, False]:
            for do in [True, False]:
                shell = self.bootstrap_shell("RuleFiles/test_abstract_forall_out.txt", wl, do)
                self.assertTrue(shell.is_warranted("g"))

    def test_looping_blocking(self):
        for wl in [True, False]:
            for do in [True, False]:
                self.bootstrap_shell("RuleFiles/test_looping_blocking.txt", wl, do)
                self.assertTrue(True)  # Test passed, if no infinite loop occurs

    def test_looping_continuing1(self):
        for do in [True, False]:
            shell = self.bootstrap_shell("RuleFiles/test_looping_continuing1.txt", False, do)
            self.assertTrue(shell.is_warranted("p"))
            self.assertFalse(shell.is_warranted("-p"))

    def test_looping_continuing2(self):
        for wl in [True, False]:
            for do in [True, False]:
                shell = self.bootstrap_shell("RuleFiles/test_looping_continuing2.txt", wl, do)
                self.assertTrue(shell.is_warranted("d"))

    def test_rebut_strength1(self):
        for do in [True, False]:
            shell = self.bootstrap_shell("RuleFiles/test_rebut_strength1.txt", False, do)
            self.assertTrue(shell.is_warranted("p"))
            shell = self.bootstrap_shell("RuleFiles/test_rebut_strength1.txt", True, do)
            self.assertFalse(shell.is_warranted("p"))
            self.assertFalse(shell.is_warranted("-p"))
        pass

    def test_rebut_strength2(self):
        for wl in [True, False]:
            shell = self.bootstrap_shell("RuleFiles/test_rebut_strength2.txt", wl, False)
            self.assertTrue(shell.is_warranted("-p"))
            shell = self.bootstrap_shell("RuleFiles/test_rebut_strength2.txt", wl, True)
            self.assertTrue(shell.is_warranted("p"))

    def test_rebut_strength3(self):
        for do in [True, False]:
            shell = self.bootstrap_shell("RuleFiles/test_rebut_strength3.txt", False, do)
            self.assertFalse(shell.is_warranted("p"))
            self.assertFalse(shell.is_warranted("-p"))
            shell = self.bootstrap_shell("RuleFiles/test_rebut_strength3.txt", True, do)
            self.assertTrue(shell.is_warranted("-p"))

    def test_simple_argument_construction(self):
        for wl in [True, False]:
            for do in [True, False]:
                shell = self.bootstrap_shell("RuleFiles/test_simple_argument_construction.txt", wl, do)
                self.assertTrue(shell.is_warranted("e"))

    def test_subargument_attack(self):
        for wl in [True, False]:
            for do in [True, False]:
                shell = self.bootstrap_shell("RuleFiles/test_subargument_attack.txt", wl, do)
                self.assertFalse(shell.is_warranted("c"))
                self.assertTrue(shell.is_warranted("-c"))

    def test_undercutting_strength(self):
        for wl in [True, False]:
            for do in [True, False]:
                shell = self.bootstrap_shell("RuleFiles/test_undercutting_strength.txt", wl, do)
                self.assertFalse(shell.is_warranted("a"))


if __name__ == '__main__':
    unittest.main()
