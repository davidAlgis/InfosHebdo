"""Planification, pilotee depuis Python.

On passe par `schtasks.exe`, l'outil livre avec Windows : c'est le seul moyen
d'inscrire une tache sans dependance supplementaire (pywin32) et sans laisser
un processus Python tourner en permanence. Les commandes sont construites ici,
en Python, et executees avec une liste d'arguments — jamais une chaine de
shell, donc pas de probleme de guillemets ni d'echappement.

Une seule tache est declaree, et elle lance `run.py auto` :

* chaque jour a l'heure choisie ;
* et deux minutes apres chaque ouverture de session.

Le second declencheur est ce qui garantit « lundi si le poste est allume,
sinon mardi... » : un poste eteint a l'heure prevue manque le declencheur
quotidien, mais la premiere ouverture de session suivante lance la tache. Les
doublons sont sans effet, car `auto` ne collecte qu'une fois par jour et
n'ouvre le rapport qu'une fois par semaine.

Aucun fichier .cmd intermediaire : la tache appelle directement
`pythonw.exe run.py auto`, et la journalisation vers logs/ est faite par le
programme lui-meme. On passe par run.py plutot que par `-m infoshebdo`
parce que le Planificateur demarre ses taches depuis system32, ou le paquet
ne serait pas importable.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

from . import paths

TASK_NAME = "InfosHebdo"
SUBCOMMAND = "auto"

# Taches des versions precedentes (collecte et envoi separes). Supprimees avec
# la tache actuelle pour ne pas laisser un ancien envoi de courriel actif.
LEGACY_TASKS = ("InfosHebdo-Collecte", "InfosHebdo-Rapport")

# Delai apres l'ouverture de session : le reseau et le navigateur ont le temps
# de demarrer, et on n'ouvre pas une fenetre pendant le chargement du bureau.
LOGON_DELAY = "PT2M"

TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


class SchedulerError(RuntimeError):
    """Erreur de planification, avec un message affichable tel quel."""


@dataclass
class TaskState:
    name: str
    exists: bool
    status: str = ""
    next_run: str = ""
    last_run: str = ""
    last_result: str = ""
    kind: str = ""
    start_time: str = ""

    @property
    def summary(self) -> str:
        if not self.exists:
            return "non planifiee"
        parts = [self.status or "?"]
        if self.next_run:
            parts.append(f"prochaine : {self.next_run}")
        if self.last_run:
            parts.append(f"derniere : {self.last_run}")
        return " - ".join(parts)


# --------------------------------------------------------------------------- #
# Environnement
# --------------------------------------------------------------------------- #
def is_supported() -> bool:
    return os.name == "nt"


def _require_windows() -> None:
    if not is_supported():
        raise SchedulerError(
            "La planification integree utilise le Planificateur de taches "
            "Windows. Sur un autre systeme, appeler « python run.py auto » "
            "une fois par jour depuis cron."
        )


def python_launcher() -> Path:
    """Executable a inscrire dans la tache.

    Empaquete, c'est l'executable de l'application. Sinon on prefere
    pythonw.exe : il execute sans ouvrir de fenetre de console, ce qui evite
    une console noire qui apparait chaque matin. Le programme ecrit de toute
    facon son journal dans logs/.
    """
    return Path(paths.command_prefix()[0])


def validate_time(value: str) -> str:
    if not TIME_RE.match(value.strip()):
        raise SchedulerError(f"Heure invalide : « {value} ». Format attendu HH:MM.")
    return value.strip()


# --------------------------------------------------------------------------- #
# schtasks
# --------------------------------------------------------------------------- #
def _console_encoding() -> str:
    """Encodage de sortie de schtasks.

    Ce n'est ni UTF-8 ni l'encodage ANSI de Windows : les outils console
    ecrivent dans la page de codes OEM (850 sur un Windows francais). Decoder
    autrement transforme « Pret » en « Pr?t » et casse la reconnaissance des
    libelles, donc l'affichage de l'etat des taches.
    """
    if os.name == "nt":
        try:
            import ctypes

            return f"cp{ctypes.windll.kernel32.GetOEMCP()}"
        except Exception:  # pragma: no cover - repli defensif
            return "cp850"
    return "utf-8"


def _run(args: list[str]) -> tuple[int, str, str]:
    """Execute schtasks et renvoie (code, sortie, erreur) deja decodes."""
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError as exc:
        raise SchedulerError(f"schtasks.exe introuvable : {exc}") from exc

    encoding = _console_encoding()
    out = completed.stdout.decode(encoding, errors="replace")
    err = completed.stderr.decode(encoding, errors="replace")
    return completed.returncode, out, err


def _quote(part: str) -> str:
    """Entoure de guillemets un argument contenant des espaces."""
    return f'"{part}"' if " " in part else part


def task_command(subcommand: str = SUBCOMMAND) -> str:
    """Ligne de commande, pour affichage uniquement.

    Ce n'est pas ce qui est inscrit dans la tache : voir `task_xml`, qui separe
    l'executable de ses arguments.
    """
    prefix = paths.command_prefix()
    return " ".join([f'"{prefix[0]}"', *(_quote(p) for p in prefix[1:]), subcommand])


def task_arguments(subcommand: str = SUBCOMMAND) -> str:
    """Arguments passes a l'executable.

    En mode source, cela vaut `-X utf8 "<...>\\run.py" auto` : le chemin du
    lanceur est entre guillemets s'il contient des espaces. Empaquete, il ne
    reste que la sous-commande. L'executable, lui, ne doit surtout pas etre
    entoure de guillemets : voir `task_xml`.
    """
    prefix = paths.command_prefix()[1:]
    return " ".join([*(_quote(p) for p in prefix), subcommand]).strip()


def _current_user() -> str:
    """'DOMAINE\\utilisateur', ou '' si l'environnement ne le dit pas."""
    user = os.environ.get("USERNAME", "")
    domain = os.environ.get("USERDOMAIN", "")
    return f"{domain}\\{user}" if user and domain else user


def triggers_xml(at: str) -> str:
    """Declencheurs : chaque jour a `at`, et a l'ouverture de session.

    `StartBoundary` demande une date : on prend aujourd'hui, la premiere
    occurrence utile etant calculee par Windows.
    """
    start = f"{date.today().isoformat()}T{at}:00"
    daily = (
        "<CalendarTrigger>"
        f"<StartBoundary>{start}</StartBoundary>"
        "<Enabled>true</Enabled>"
        "<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>"
        "</CalendarTrigger>"
    )
    user = _current_user()
    user_xml = f"<UserId>{escape(user)}</UserId>" if user else ""
    logon = (
        "<LogonTrigger>"
        "<Enabled>true</Enabled>"
        f"{user_xml}"
        f"<Delay>{LOGON_DELAY}</Delay>"
        "</LogonTrigger>"
    )
    return daily + logon


def task_xml(description: str, triggers: str, subcommand: str = SUBCOMMAND) -> str:
    """Definition complete de la tache.

    On passe par XML plutot que par `/TR` pour trois raisons :

    * `/TR` range toute la ligne, guillemets compris, dans le champ Command.
      Windows cherche alors un executable dont le nom contient des guillemets
      et la tache echoue sans rien journaliser — panne silencieuse, la pire
      possible pour un traitement planifie.
    * `StartWhenAvailable` rattrape une execution manquee. Sur un portable
      eteint a l'heure prevue, la journee serait sinon perdue, et les releves
      instantanes (joueurs simultanes) ne se rattrapent pas.
    * `WorkingDirectory` evite de dependre du repertoire courant, qui est
      system32 pour une tache planifiee.
    """
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>{escape(description)}</Description>
    <Author>InfosHebdo</Author>
  </RegistrationInfo>
  <Triggers>{triggers}</Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT1H</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(str(python_launcher()))}</Command>
      <Arguments>{escape(task_arguments(subcommand))}</Arguments>
      <WorkingDirectory>{escape(str(paths.PROJECT_DIR))}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def install(time: str = "09:00") -> list[str]:
    """Declare (ou remplace) la tache. Renvoie les messages a afficher."""
    _require_windows()
    time = validate_time(time)

    xml = task_xml(
        "InfosHebdo : collecte du jour et ouverture du rapport hebdomadaire.",
        triggers_xml(time),
    )

    # schtasks /XML attend un fichier encode en UTF-16.
    handle = tempfile.NamedTemporaryFile(
        mode="w", suffix=".xml", delete=False, encoding="utf-16"
    )
    try:
        handle.write(xml)
        handle.close()
        code, out, err = _run(
            ["schtasks", "/Create", "/TN", TASK_NAME, "/XML", handle.name, "/F"]
        )
    finally:
        try:
            os.unlink(handle.name)
        except OSError:
            pass

    if code != 0:
        detail = (err or out or "").strip()
        raise SchedulerError(
            f"Creation de « {TASK_NAME} » refusee par Windows : {detail}\n"
            "Relancer l'interface ou la commande depuis une session "
            "administrateur si le message parle de privileges."
        )

    messages = [f"Tache « {TASK_NAME} » enregistree."]
    # Une ancienne installation ne doit pas continuer a tourner en parallele.
    for legacy in LEGACY_TASKS:
        legacy_code, _out, _err = _run(["schtasks", "/Delete", "/TN", legacy, "/F"])
        if legacy_code == 0:
            messages.append(f"Ancienne tache « {legacy} » supprimee.")
    messages.append(f"Declenchement : chaque jour a {time}, et a l'ouverture de session.")
    messages.append(
        "Le rapport s'ouvre une fois par semaine, le premier jour ou le poste "
        "est allume : lundi si possible, sinon mardi, etc."
    )
    messages.append(
        "La tache est creee en mode interactif : elle s'execute quand la "
        "session Windows est ouverte, meme verrouillee, mais pas si le poste "
        "est eteint ou l'utilisateur deconnecte."
    )
    messages.append(f"Journal : {paths.LOG_DIR}")
    return messages


def uninstall() -> list[str]:
    """Supprime la tache (et les anciennes). Une absence n'est pas une erreur."""
    _require_windows()
    messages: list[str] = []
    for name in (TASK_NAME, *LEGACY_TASKS):
        code, _out, _err = _run(["schtasks", "/Delete", "/TN", name, "/F"])
        if code == 0:
            messages.append(f"Tache « {name} » supprimee.")
        elif name == TASK_NAME:
            messages.append(f"Tache « {name} » : rien a supprimer.")
    return messages


def _strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(c for c in normalized if not unicodedata.combining(c))


# Libelles renvoyes par schtasks, en francais et en anglais. On compare sans
# accents ni casse : la sortie depend de la langue de Windows.
QUERY_LABELS = {
    "statut": "status",
    "status": "status",
    "prochaine execution": "next_run",
    "next run time": "next_run",
    "heure de la derniere execution": "last_run",
    "last run time": "last_run",
    "dernier resultat": "last_result",
    "last result": "last_result",
    "type de planification": "kind",
    "schedule type": "kind",
    "heure de debut": "start_time",
    "start time": "start_time",
}

# Codes retournes par le Planificateur, traduits en clair.
RESULT_CODES = {
    "0": "succes",
    "1": "erreur signalee par InfosHebdo (voir logs/)",
    "267011": "jamais executee",
    "267009": "en cours d'execution",
    "267014": "arretee par l'utilisateur",
}


def _parse_query(output: str, name: str) -> TaskState:
    """Lit la sortie de schtasks /Query /FO LIST /V."""
    state = TaskState(name=name, exists=True)
    for line in output.splitlines():
        if ":" not in line:
            continue
        label, _, value = line.partition(":")
        key = _strip_accents(label.strip().lower())
        attribute = QUERY_LABELS.get(key)
        if attribute and not getattr(state, attribute):
            setattr(state, attribute, value.strip())

    code = state.last_result.strip()
    if code in RESULT_CODES:
        state.last_result = f"{code} ({RESULT_CODES[code]})"
    return state


def status() -> list[TaskState]:
    """Etat de la tache, sans lever d'exception si elle n'existe pas."""
    if not is_supported():
        return [TaskState(name=TASK_NAME, exists=False)]

    code, out, _err = _run(["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST", "/V"])
    if code != 0:
        return [TaskState(name=TASK_NAME, exists=False)]
    return [_parse_query(out, TASK_NAME)]


def describe() -> list[str]:
    """Ce qui serait inscrit, pour affichage avant installation."""
    return [
        f"Interpreteur : {python_launcher()}",
        f"Dossier      : {paths.PROJECT_DIR}",
        f"Commande     : {task_command()}",
    ]
