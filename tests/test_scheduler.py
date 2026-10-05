"""Tests du planificateur.

On ne cree aucune tache reelle : on verifie la construction des arguments, la
validation des saisies et la lecture de la sortie de schtasks. C'est la partie
qui casse en silence, et une tache mal formee ne se voit qu'un matin ou le
rapport ne s'ouvre pas.
"""
from __future__ import annotations

import re
import unittest
from unittest import mock

from infoshebdo import paths, scheduler

# Chemins avec espaces : le cas qui exige des guillemets. On ne peut pas
# compter sur l'emplacement reel du depot, qui n'en contient peut-etre pas.
SPACED_LAUNCHER = r"C:\Mes Outils\InfosHebdo\run.py"
SPACED_PREFIX = [r"C:\Python 3\pythonw.exe", "-X", "utf8", SPACED_LAUNCHER]


class TestValidation(unittest.TestCase):
    def test_valid_times(self):
        for value in ("00:00", "9:05", "09:15", "23:59"):
            self.assertEqual(scheduler.validate_time(value), value.strip())

    def test_invalid_times(self):
        for value in ("24:00", "9h15", "09:60", "", "abc"):
            with self.assertRaises(scheduler.SchedulerError):
                scheduler.validate_time(value)


class TestCommandBuilding(unittest.TestCase):
    def test_spaced_paths_are_quoted(self):
        # Sans guillemets, un chemin avec espaces ferait echouer la tache.
        with mock.patch.object(paths, "command_prefix", return_value=SPACED_PREFIX):
            command = scheduler.task_command()
            arguments = scheduler.task_arguments()
        self.assertTrue(command.startswith('"'))
        self.assertIn(f'"{SPACED_LAUNCHER}"', command)
        self.assertEqual(arguments, f'-X utf8 "{SPACED_LAUNCHER}" auto')

    def test_plain_paths_are_left_alone(self):
        prefix = [r"C:\Python\pythonw.exe", "-X", "utf8", r"C:\Outils\run.py"]
        with mock.patch.object(paths, "command_prefix", return_value=prefix):
            self.assertEqual(scheduler.task_arguments(), r"-X utf8 C:\Outils\run.py auto")

    def test_command_ends_with_auto(self):
        self.assertTrue(scheduler.task_command().endswith(" auto"))

    def test_launcher_is_absolute(self):
        # La tache demarre depuis system32 : un chemin relatif echouerait.
        self.assertTrue(paths.LAUNCHER.is_absolute())

    def test_describe_mentions_auto(self):
        self.assertIn("auto", "\n".join(scheduler.describe()))


class TestTaskXml(unittest.TestCase):
    """Le XML est la definition reellement inscrite dans le Planificateur.

    Une erreur ici ne se voit pas : la tache est creee, Windows annonce un
    succes, et rien ne s'execute. C'est exactement ce qui arrivait quand la
    commande passait par /TR, qui rangeait le chemin de l'interpreteur avec ses
    guillemets dans le champ Command.
    """

    def _xml(self) -> str:
        return scheduler.task_xml("Description de test", scheduler.triggers_xml("09:00"))

    def test_command_is_not_quoted(self):
        xml = self._xml()
        command = re.search(r"<Command>(.*?)</Command>", xml, re.S).group(1)
        self.assertFalse(command.startswith('"'))
        self.assertTrue(command.lower().endswith(("python.exe", "pythonw.exe")))

    def test_arguments_quote_a_spaced_launcher(self):
        # Les guillemets restent des guillemets : ils sont legaux dans un
        # contenu d'element XML, et c'est la chaine litterale que Windows
        # transmet a l'interpreteur.
        with mock.patch.object(paths, "command_prefix", return_value=SPACED_PREFIX):
            xml = self._xml()
        arguments = re.search(r"<Arguments>(.*?)</Arguments>", xml, re.S).group(1)
        self.assertIn(f'"{SPACED_LAUNCHER}"', arguments)
        self.assertTrue(arguments.endswith("auto"))

    def test_working_directory_is_the_project(self):
        xml = self._xml()
        directory = re.search(
            r"<WorkingDirectory>(.*?)</WorkingDirectory>", xml, re.S
        ).group(1)
        self.assertEqual(directory, str(paths.PROJECT_DIR))

    def test_special_characters_in_paths_are_escaped(self):
        prefix = [r"C:\R&D\pythonw.exe", "-X", "utf8", r"C:\R&D\run.py"]
        with mock.patch.object(paths, "command_prefix", return_value=prefix):
            xml = self._xml()
        self.assertIn(r"C:\R&amp;D\pythonw.exe", xml)
        self.assertNotIn("R&D", xml)

    def test_catch_up_is_enabled(self):
        # Sans cela, une journee ou le poste est eteint est perdue, et les
        # releves instantanes ne se rattrapent pas.
        self.assertIn("<StartWhenAvailable>true</StartWhenAvailable>", self._xml())

    def test_single_instance_policy(self):
        # Deux traitements simultanes se disputeraient le verrou SQLite.
        self.assertIn("<MultipleInstancesPolicy>IgnoreNew", self._xml())

    def test_daily_and_logon_triggers(self):
        # Le declencheur d'ouverture de session est ce qui garantit
        # « lundi, sinon mardi » : un poste eteint a 9 h rate le quotidien.
        xml = self._xml()
        self.assertIn("<ScheduleByDay>", xml)
        self.assertIn("<LogonTrigger>", xml)
        self.assertIn(f"<Delay>{scheduler.LOGON_DELAY}</Delay>", xml)

    def test_logon_trigger_targets_the_current_user(self):
        with mock.patch.object(scheduler, "_current_user", return_value=r"PC\Moi"):
            xml = scheduler.triggers_xml("09:00")
        self.assertIn(r"<UserId>PC\Moi</UserId>", xml)

    def test_logon_trigger_without_known_user(self):
        with mock.patch.object(scheduler, "_current_user", return_value=""):
            xml = scheduler.triggers_xml("09:00")
        self.assertNotIn("<UserId>", xml)

    def test_start_boundary_carries_the_time(self):
        self.assertIn("T09:00:00</StartBoundary>", self._xml())

    def test_xml_is_well_formed(self):
        from xml.etree import ElementTree

        # L'entete annonce UTF-16 pour schtasks ; on parse la version texte.
        body = self._xml().split("?>", 1)[1]
        ElementTree.fromstring(body)

    def test_runs_the_auto_command_and_nothing_else(self):
        # Plus aucune commande d'envoi : la tache ne doit jamais appeler
        # une sous-commande qui n'existe plus.
        xml = self._xml()
        arguments = re.search(r"<Arguments>(.*?)</Arguments>", xml, re.S).group(1)
        self.assertTrue(arguments.endswith(" auto"))
        self.assertNotIn("weekly", arguments)


FRENCH_OUTPUT = """
Dossier: \\
Nom de la tâche:                             \\InfosHebdo
Prochaine exécution:                         22/08/2026 09:15:00
Statut:                                      Prêt
Heure de la dernière exécution:              30/11/1999 00:00:00
Dernier résultat:                            267011
Type de planification:                       Tous les jours
Heure de début:                              09:15:00
"""

ENGLISH_OUTPUT = """
Folder: \\
TaskName:                             \\InfosHebdo
Next Run Time:                        25/08/2026 08:30:00
Status:                               Ready
Last Run Time:                        30/11/1999 00:00:00
Last Result:                          0
Schedule Type:                        Daily
Start Time:                           08:30:00
"""


class TestQueryParsing(unittest.TestCase):
    def test_french_labels(self):
        state = scheduler._parse_query(FRENCH_OUTPUT, "InfosHebdo")
        self.assertTrue(state.exists)
        self.assertEqual(state.status, "Prêt")
        self.assertEqual(state.next_run, "22/08/2026 09:15:00")
        self.assertEqual(state.kind, "Tous les jours")

    def test_english_labels(self):
        state = scheduler._parse_query(ENGLISH_OUTPUT, "InfosHebdo")
        self.assertEqual(state.status, "Ready")
        self.assertEqual(state.next_run, "25/08/2026 08:30:00")

    def test_time_in_value_is_not_split(self):
        # Le libelle se separe au premier deux-points : l'heure doit rester
        # entiere dans la valeur.
        state = scheduler._parse_query(FRENCH_OUTPUT, "x")
        self.assertTrue(state.next_run.endswith("09:15:00"))

    def test_result_code_translated(self):
        state = scheduler._parse_query(FRENCH_OUTPUT, "x")
        self.assertIn("jamais executee", state.last_result)

    def test_success_code_translated(self):
        state = scheduler._parse_query(ENGLISH_OUTPUT, "x")
        self.assertIn("succes", state.last_result)

    def test_summary_when_absent(self):
        state = scheduler.TaskState(name="x", exists=False)
        self.assertEqual(state.summary, "non planifiee")

    def test_summary_when_present(self):
        state = scheduler._parse_query(FRENCH_OUTPUT, "x")
        self.assertIn("Prêt", state.summary)
        self.assertIn("prochaine", state.summary)


class TestLegacyCleanup(unittest.TestCase):
    """Les anciennes taches (collecte + envoi) ne doivent pas survivre."""

    def test_legacy_names_are_known(self):
        self.assertIn("InfosHebdo-Rapport", scheduler.LEGACY_TASKS)
        self.assertIn("InfosHebdo-Collecte", scheduler.LEGACY_TASKS)

    def test_uninstall_removes_current_and_legacy_tasks(self):
        deleted: list[str] = []

        def fake_run(args):
            deleted.append(args[args.index("/TN") + 1])
            return 0, "", ""

        with mock.patch.object(scheduler, "is_supported", return_value=True), \
             mock.patch.object(scheduler, "_run", side_effect=fake_run):
            scheduler.uninstall()
        self.assertEqual(
            deleted, [scheduler.TASK_NAME, *scheduler.LEGACY_TASKS]
        )

    def test_install_rejects_a_bad_time_before_touching_windows(self):
        with mock.patch.object(scheduler, "is_supported", return_value=True), \
             mock.patch.object(scheduler, "_run") as run:
            with self.assertRaises(scheduler.SchedulerError):
                scheduler.install(time="9h15")
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
