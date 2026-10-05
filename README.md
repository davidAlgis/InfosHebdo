# InfosHebdo

Veille hebdomadaire du cinéma et du jeu vidéo, ouverte automatiquement dans le
navigateur.

L'outil collecte chaque jour ce que les sources publient, accumule l'historique
dans une base SQLite, puis ouvre le rapport de la semaine dans votre
navigateur : **le lundi si le poste est allumé, sinon le mardi**, et ainsi de
suite. Aucun compte, aucun serveur, aucun courriel.

```
Sources Internet
      ↓  collecte quotidienne (infoshebdo auto)
Base SQLite  (data/infoshebdo.sqlite3)
      ↓  analyse de la semaine
Rapport HTML + texte  (reports/)
      ↓  une seule fois par semaine
🌐 ouverture dans le navigateur par défaut
```

La collecte est quotidienne parce que les données ne sont pas stables : le
box-office France et Japon est publié avec plusieurs semaines de retard puis
révisé, et les joueurs simultanés Steam sont un instantané qui n'existe plus le
lendemain. Le rapport, lui, s'ouvre une fois par semaine.

---

## Installation

```powershell
cd D:\Recherches\Vendors\Programs\InfosHebdo
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

python -m infoshebdo collect --backfill 12   # première fois : rattraper l'historique
python -m infoshebdo report --open           # voir le rapport tout de suite
python -m infoshebdo schedule install        # activer l'ouverture automatique
```

C'est tout. Le détail de ce que fait la tâche planifiée est dans
[Automatisation](#automatisation).

### Exécutable Windows (sans Python)

```powershell
pip install -r requirements-build.txt     # une fois : PyInstaller
python build_exe.py --register
```

Le script lance les tests, fabrique l'exécutable avec PyInstaller, **le fait
tourner pour vérifier** que rien n'a été oublié (`selftest`), puis l'installe
dans `%LOCALAPPDATA%\Programs\InfosHebdo`, le dossier que Windows réserve aux
programmes installés sans droits administrateur. Rien n'est écrit dans le dossier
d'installation tant que la vérification n'a pas réussi.

| Fichier installé | Rôle |
| --- | --- |
| `InfosHebdo.exe` | sans console : l'interface, et la tâche planifiée (aucune fenêtre noire chaque matin) |
| `InfosHebdo-console.exe` | avec console : pour les commandes dont on veut lire la sortie (`status`, `schedule`, `selftest`...) |
| `config.yaml`, `data/`, `reports/`, `logs/` | vos données, à côté des exécutables. **Une réinstallation n'y touche jamais** |

`--register` inscrit la tâche planifiée et le lancement de l'icône à
l'ouverture de session ; sans lui, le script affiche les deux commandes à
lancer (`InfosHebdo-console.exe schedule install`, puis `... startup enable`).
Autres options : `--dest` (autre dossier), `--no-install` (fabriquer seulement,
résultat dans `build/dist`), `--with-data` (reprendre la base et les imports du
dépôt, sans jamais écraser), `--kill` (arrêter InfosHebdo s'il tourne, pour
pouvoir le remplacer), `--skip-tests`, `--clean`.

Pour mettre à jour : relancer `python build_exe.py`. Si InfosHebdo tourne (icône
près de l'horloge), le quitter d'abord ou ajouter `--kill` : ses fichiers sont
verrouillés tant qu'il tourne.

### Icône près de l'horloge

L'interface peut rester résidente dans la zone de notification pour ouvrir le
rapport **à tout moment**, pas seulement en début de semaine. Clic droit sur
l'icône :

* **Générer et ouvrir le rapport** : reconstruit le rapport depuis la base ;
* **Ouvrir le dernier rapport** : rouvre celui déjà généré, sans rien recalculer ;
* **Collecter maintenant**, **Ouvrir InfosHebdo** (fenêtre de réglages) et
  **Quitter**.

Un double-clic ouvre la fenêtre. Pour que l'icône soit présente dès l'ouverture
de session : `python -m infoshebdo startup enable` (ou l'onglet **Planification**).
La croix de la fenêtre la replie dans la zone de notification au lieu de quitter ;
« Quitter » reste dans le menu de l'icône. Cette interface est indépendante de la
tâche planifiée : l'une ou l'autre peut tourner seule.

### Interface graphique (facultative)

```powershell
python -m infoshebdo ui
```

L'interface sert à régler l'outil et à lancer les traitements à la main. Elle
n'est pas nécessaire au fonctionnement automatique : la tâche planifiée ne la
démarre jamais.

| Onglet | Contenu |
| --- | --- |
| **Tableau de bord** | état de la base, journal de la dernière collecte, boutons collecter / générer et ouvrir le rapport |
| **Suivi** | lignes par classement, semaines de rattrapage, marchés box-office et Steam, zones Box Office Mojo, activation de chaque source |
| **Flux presse** | flux RSS surveillés, mots-clés, bouton de test qui montre ce qui serait retenu sans rien stocker |
| **Rapport** | titre, sections vides, ouverture automatique chaque semaine |
| **Planification** | heure de déclenchement, installation et suppression de la tâche, état réel |
| **Import manuel** | dépôt des CSV, ouverture du dossier, modèle, import immédiat |

Les traitements longs tournent en tâche de fond : la fenêtre reste utilisable et
le journal défile en direct. Un seul traitement à la fois, les collecteurs
écrivant tous dans la même base. Tout lancement enregistre d'abord les réglages
affichés, pour qu'une modification ne soit jamais ignorée.

### Fichiers de configuration

`config.yaml` — ce qui est suivi :

```yaml
top_n: 10                 # lignes par classement dans le rapport
backfill_weeks: 8         # semaines re-interrogées à chaque collecte
box_office:
  markets: [FR, US, JP, WW]
games:
  steam_markets: [WW, FR, US, JP, GB, DE]
report:
  auto_open: true         # ouvrir le rapport une fois par semaine
```

`.env` — facultatif. Il ne contient plus de secret : seulement des réglages
techniques (`INFOSHEBDO_DB`, `INFOSHEBDO_HTTP_DELAY`, `INFOSHEBDO_HTTP_CACHE`),
voir `.env.example`.

Un `config.yaml` ou un `.env` issu d'une version qui envoyait le rapport par
courriel reste lisible : les clés `email`, `attach_html`, `open_after_send` et
`open_on_ui_start` sont ignorées, et les lignes `SMTP_*` / `MAIL_*` du `.env`
n'ont plus d'effet (vous pouvez les supprimer).

`python -m infoshebdo config` affiche la configuration effective (fusion des
valeurs par défaut et du fichier).

---

## Ligne de commande

| Commande | Rôle |
| --- | --- |
| `python -m infoshebdo auto` | **la commande planifiée** : collecte du jour (si pas déjà faite), puis ouvre le rapport de la semaine (s'il n'a pas déjà été ouvert) |
| `python -m infoshebdo auto --force` | idem, mais rouvre le rapport même s'il l'a déjà été cette semaine |
| `python -m infoshebdo auto --no-window` | idem, sans la fenêtre d'erreur |
| `python -m infoshebdo collect` | collecte, sans rapport |
| `python -m infoshebdo report` | générer le rapport dans `reports/` |
| `python -m infoshebdo report --open` | générer **et** ouvrir dans le navigateur |
| `python -m infoshebdo schedule install` | déclarer la tâche automatique |
| `python -m infoshebdo schedule` | état de la tâche |
| `python -m infoshebdo schedule remove` | retirer la tâche |
| `python -m infoshebdo status` | état de la base, dernière collecte, rapport de la semaine |
| `python -m infoshebdo sources` | sources, ce qu'elles fournissent, leur fiabilité |
| `python -m infoshebdo config` | configuration effective |
| `python -m infoshebdo ui` | ouvrir l'interface (`--minimized` : démarrer replié dans la zone de notification) |
| `python -m infoshebdo startup enable` | lancer l'interface, avec son icône près de l'horloge, à l'ouverture de session (`status`, `disable`) |
| `python -m infoshebdo selftest` | vérifier qu'une installation est complète (modules, gabarit, icône) |

```powershell
# rattraper 12 semaines d'historique (première installation)
python -m infoshebdo collect --backfill 12

# ne relancer qu'une source
python -m infoshebdo collect --only allocine_france

# déclenchement quotidien à 07:30
python -m infoshebdo schedule install --time 07:30
```

`run.py` est un point d'entrée équivalent, utilisable depuis n'importe quel
répertoire : c'est celui qu'inscrit la tâche planifiée.

---

## Automatisation

`schedule install` (ou l'onglet **Planification**) inscrit **une seule tâche**
dans le Planificateur de tâches Windows, `InfosHebdo`, qui lance
`pythonw.exe run.py auto`. Elle se déclenche :

* **chaque jour** à l'heure choisie (09:00 par défaut) ;
* **deux minutes après chaque ouverture de session**.

Le second déclencheur est ce qui donne « lundi, sinon mardi » : un poste éteint
à l'heure prévue manque le déclencheur quotidien, mais la première ouverture de
session suivante lance la tâche. Comme la tâche peut ainsi tourner plusieurs
fois par jour, chaque étape vérifie d'abord si elle a déjà été faite :

| Étape | Fréquence | Mémorisée par |
| --- | --- | --- |
| Collecte | une fois par jour, et seulement si au moins une source a répondu (un poste hors ligne réessaie au déclenchement suivant) | journal des collectes en base |
| Ouverture du rapport | une fois par semaine ISO, au premier passage | clé `last_report_week` en base (ex. `2026-W41`) |

La collecte passe toujours avant le rapport, pour que la page ouverte contienne
les chiffres du jour. Pour ne plus ouvrir de rapport tout en gardant la
collecte, mettre `report.auto_open: false`.

### Quand la collecte échoue

La tâche tourne sans console : une erreur ne doit jamais rester cachée dans un
journal. Dès qu'un problème survient, **une fenêtre s'ouvre au premier plan**
avec le message exact de chaque source en erreur. Elle propose d'ouvrir le
dernier rapport, le journal, l'interface (pour relancer une collecte) et de
copier le détail.

| Situation | Fenêtre | Rapport de la semaine |
| --- | --- | --- |
| Aucune source n'a répondu (poste hors ligne, par exemple) | oui | ouvert avec un **avertissement jaune en tête de page** (« les chiffres datent de la dernière collecte réussie, le JJ/MM/AAAA »). Semaine **non** marquée comme vue : le rapport à jour s'ouvrira dès qu'une collecte aboutira. Rouvert au plus une fois par jour tant que le réseau est coupé |
| Certaines sources seulement en erreur | oui | ouvert, avec un avertissement qui nomme les sources concernées |
| Étape d'agrégation du box-office monde en erreur | oui | ouvert |
| Le navigateur ne s'ouvre pas | oui, avec le chemin du rapport | semaine non marquée comme vue, retenté |
| Erreur imprévue (base verrouillée, bug) | oui, avec le détail technique | — |

**Comment une panne est détectée.** Les collecteurs tolèrent les pages manquantes
(le box-office France et Japon arrive avec des semaines de retard) et les notent
sans échouer : sans réseau, toutes les sources auraient donc l'air « vides ». Le
client HTTP compte donc les requêtes : une source dont **aucune** requête n'a
abouti est en erreur, une source jointe mais sans donnée reste « vide ». Hors
ligne, un serveur injoignable est abandonné pour le reste de la collecte, et
après trois serveurs distincts sans aucune réponse les suivants ne sont plus
essayés : l'erreur apparaît en une trentaine de secondes, pas en plusieurs
minutes.

Une collecte qui a réussi au moins en partie compte pour la journée : le
déclenchement suivant ne relance rien et n'ouvre donc pas une seconde fenêtre.
Le code de retour de la tâche (`1`, ou `2` pour une erreur imprévue) reste
visible dans le Planificateur de tâches. `auto --no-window` supprime la
fenêtre ; sans écran (cron), l'erreur reste dans `logs/infoshebdo.log`.

Le programme écrit son journal dans `logs/infoshebdo.log`. Une erreur survenant
avant la mise en place du journal atterrit dans `logs/crash.log` : une tâche
sans console ne doit jamais échouer en silence.

Deux points à connaître :

* La tâche est créée en **mode interactif** : elle s'exécute quand la session
  Windows est ouverte, même verrouillée, mais pas si le poste est éteint ou
  l'utilisateur déconnecté. C'est aussi ce qui permet d'ouvrir une fenêtre de
  navigateur.
* `StartWhenAvailable` est actif : une exécution manquée est rattrapée au
  démarrage suivant. Sans cela, une journée d'arrêt serait perdue
  définitivement, les relevés instantanés (joueurs simultanés Steam) ne se
  rattrapant pas.

La tâche remplace les deux tâches des versions précédentes
(`InfosHebdo-Collecte`, `InfosHebdo-Rapport`) : `schedule install` et
`schedule remove` les suppriment si elles existent.

### Sous Linux ou macOS

La planification intégrée est propre à Windows. Ailleurs, une ligne cron
suffit, toujours une fois par jour (la commande décide seule de ce qu'il y a à
faire) :

```
0 9 * * *  /chemin/vers/python /chemin/vers/InfosHebdo/run.py auto
```

L'ouverture dans le navigateur demande une session graphique ; sans elle,
`auto` collecte et laisse le rapport dans `reports/`.

---

## Sources : ce qui est réellement disponible

Le point dur de ce projet n'est pas le code, c'est l'accès aux données. Toutes
les sources ci-dessous ont été vérifiées en direct. Chaque chiffre stocké porte
une étiquette de fiabilité, reprise dans le rapport.

### Box-office cinéma

| Marché | Source | Donnée | Fiabilité | Fréquence |
| --- | --- | --- | --- | --- |
| 🇫🇷 France | Allociné `/boxoffice/france/sem-AAAA-MM-JJ/` | **entrées** hebdo + cumul + semaine d'exploitation | officiel | semaine mer.→mar. |
| 🇺🇸 États-Unis | Box Office Mojo (zone par défaut) | recettes USD, salles, moyenne par salle, cumul | officiel | week-end ven.→dim. |
| 🇯🇵 Japon | Box Office Mojo `?area=JP` | recettes USD | **partiel** | week-end, retard 2–5 sem. |
| 🇫🇷 France (bis) | Box Office Mojo `?area=FR` | recettes USD | **partiel** | week-end, retard 2–5 sem. |
| 🌍 Monde | *calcul InfosHebdo* | somme des zones collectées | **extrapolé** | week-end |

Trois points à connaître :

* **Le box-office mondial hebdomadaire n'existe pas en accès libre.** Box Office
  Mojo publie un cumul annuel monde, mais sa zone `XWW` ne rend aucun tableau
  hebdomadaire. Le « Monde » du rapport est donc la somme des zones
  effectivement remontées, et le rapport affiche systématiquement lesquelles.
  Ce n'est pas un chiffre mondial et il est étiqueté comme tel.
* **Hors Amérique du Nord, Mojo n'est pas exhaustif** : seuls les distributeurs
  qui déclarent leurs chiffres à IMDbPro y figurent, avec plusieurs semaines de
  retard et des révisions. D'où le rattrapage sur 8 semaines à chaque collecte :
  une semaine vide aujourd'hui se remplira plus tard.
* **Pour la France, préférer les entrées d'Allociné** aux recettes converties en
  dollars par Mojo. Attention, une partie des pages d'archive Allociné rend un
  tableau bien formé mais tronqué à quatre ou cinq films confidentiels ; le
  collecteur les rejette (`MIN_ROWS`) plutôt que de polluer l'historique.

### Jeu vidéo — Steam

| Marché | Source | Donnée | Fiabilité |
| --- | --- | --- | --- |
| 🇫🇷 🇺🇸 🇯🇵 🇬🇧 🇩🇪 🌍 | `IStoreTopSellersService/GetWeeklyTopSellers` | top 100 hebdo **par pays** : rang, rang S-1, semaines consécutives | officiel |
| 🌍 Monde | `ISteamChartsService/GetGamesByConcurrentPlayers` | joueurs simultanés, pic | officiel |
| 🌍 Monde | SteamSpy | propriétaires estimés (milieu de fourchette) | **estimé** |

**Valve ne publie jamais d'unités ni de chiffre d'affaires.** Le classement par
pays est officiel, mais c'est un *ordre*, pas un volume : les rangs ne
s'additionnent pas et ne se comparent pas d'un pays à l'autre. Les seuls
indicateurs de volume disponibles (joueurs simultanés, propriétaires estimés)
sont mondiaux.

Détail technique : ces endpoints n'acceptent pas les paramètres à plat
(`?country_code=FR`), il faut passer la requête entière en JSON dans
`input_json`, et c'est le `country_code` **racine** qui change le classement —
celui de `context` ne fait que choisir la devise d'affichage.

### Jeu vidéo — toutes plateformes

**Aucune source libre ne publie d'unités hebdomadaires toutes plateformes.**

* VGChartz avait des classements hebdomadaires par pays (Global, USA, Japon,
  Europe, Royaume-Uni, Allemagne, France) : ils **s'arrêtent à la semaine du
  29 décembre 2018**.
* Les panels de référence sont payants : **GSD/GfK** pour l'Europe (France,
  Allemagne, Royaume-Uni), **Circana** (ex-NPD) pour les États-Unis,
  **Famitsu** pour le Japon.

L'outil fait donc deux choses, sans jamais inventer de chiffre :

1. **Veille éditoriale** — surveillance des flux RSS (Gematsu, GamesIndustry.biz)
   pour repérer les articles qui publient ces classements. Le rapport donne les
   titres et les liens, étiquetés « tiers ».
2. **Import manuel** — déposer un CSV dans `data/import/`, il est intégré à la
   collecte suivante puis déplacé dans `data/import/traites/`.

```csv
market,period_start,title,platform,metric,value,rank,reliability,source
FR,2026-08-10,EA SPORTS FC 26,PS5,units,18500,1,officiel,GSD
```

Colonnes obligatoires : `market`, `period_start`, `title`, `metric`, `value`.
Optionnelles : `period_end`, `platform`, `rank`, `prev_rank`, `distributor`,
`currency`, `reliability`, `period_type`, `source`, `note`. Un modèle complet se
trouve dans `data/import/exemple-gsd-france.csv.modele`.

### Étiquettes de fiabilité

| Étiquette | Signification |
| --- | --- |
| `officiel` | chiffre publié par la source de référence du marché |
| `partiel` | officiel, mais couverture incomplète du marché |
| `estimé` | estimation d'un tiers, méthodologie non vérifiable |
| `extrapolé` | calculé par InfosHebdo à partir d'autres marchés |
| `tiers` | repris d'un média, non vérifié à la source |
| `manuel` | saisi à la main depuis un rapport payant ou un communiqué |

---

## Architecture

```
run.py              point d'entrée indépendant du répertoire courant
build_exe.py        fabrique et installe l'exécutable Windows (voir plus haut)
infoshebdo.spec     spécification PyInstaller : deux exécutables, un seul dossier
infoshebdo/
  cli.py            commandes en ligne
  config.py         lecture ET écriture de config.yaml et .env
  paths.py          emplacements de fichiers
  http.py           client HTTP : en-tête, réessais, délai de politesse, cache, bilan des requêtes
  weeks.py          calendriers de semaines (Mojo, France, ISO, Steam)
  db.py             schéma SQLite et accès
  pipeline.py       orchestration de la collecte, journalisation, isolation
  derive.py         données calculées (agrégat monde, résolution de titres)
  analysis.py       construction du rapport, calcul des évolutions
  report.py         rendu HTML et texte
  auto.py           commande planifiée : collecte du jour, rapport de la semaine
  viewer.py         ouverture dans le navigateur, mémoire « une fois par semaine »
  scheduler.py      tâche planifiée Windows, définie en XML
  collectors/
    base.py           contrat commun, vocabulaire, registre
    boxofficemojo.py  recettes de week-end USD, zones US/FR/JP
    allocine.py       entrées France
    steam.py          classements de ventes par pays, joueurs simultanés
    steamspy.py       propriétaires estimés
    editorial.py      veille RSS des publications de classements
    manual.py         import CSV
  ui/
    app.py            fenêtre principale, enregistrement, lancement
    settings.py       onglets Suivi, Flux presse, Rapport
    error_window.py   fenêtre d'erreur de la commande planifiée
    panels.py         onglets Tableau de bord, Planification, Import
    runner.py         exécution en tâche de fond, journal par file d'attente
    widgets.py        briques Tk réutilisées
```

Quatre principes de conception :

* **Tout est en Python.** Aucun script PowerShell ni `.cmd` : les tâches
  planifiées sont déclarées par `scheduler.py`, qui génère la définition XML et
  la passe à `schtasks.exe` (l'outil livré avec Windows). Un fichier XML donne
  un contrôle exact sur l'exécutable, ses arguments, le répertoire de travail et
  le rattrapage — ce que l'option `/TR` ne permet pas.
* **Aucun appel Tk depuis un fil de travail.** Une collecte dure une à deux
  minutes ; elle tourne dans un thread qui ne fait que déposer des lignes dans
  une file, vidée par la fenêtre sur son propre fil.
* **Une source qui tombe ne fait jamais échouer la collecte.** Chaque collecteur
  est isolé ; son résultat et son erreur éventuelle sont journalisés et repris en
  bas du rapport. Une section vide indique donc toujours *pourquoi* elle est
  vide.
* **Chaque tableau porte sa propre période.** Les sources n'ont pas la même
  semaine (week-end vendredi→dimanche chez Mojo, mercredi→mardi pour le cinéma
  français, mardi→lundi chez Steam). Les aligner de force ferait mentir les
  chiffres. Une évolution « vs S-1 » n'est calculée que si la semaine précédente
  est réellement adjacente en base.

### Modèle de données

Tout est rangé au format long dans `observations` : une ligne = une mesure
(un titre, un marché, une période, une métrique). Ajouter une source ne demande
aucune migration.

```sql
observations(source, domain, market, period_type, period_start, period_end,
             title, platform, rank, prev_rank, metric, value, currency,
             reliability, extra JSON, ...)
UNIQUE(source, domain, market, period_type, period_start, metric, title, platform)
```

Cette contrainte rend la collecte idempotente : relancer trois fois dans la
journée ne crée pas de doublon, et une donnée révisée par la source écrase
l'ancienne valeur.

Tables annexes : `runs` et `collector_runs` (journal d'exécution), `news`
(veille éditoriale, dédoublonnée par URL).

---

## Tests

```powershell
python -m unittest discover -s tests -t .
```

160 tests, aucun accès réseau ni navigateur. Ce qu'ils verrouillent :

* **parsers** — extraits HTML reproduisant la structure réelle des pages
  observées (classes CSS, `data-heading`, espaces insécables dans les nombres) ;
* **calendriers** — les trois définitions de semaine, vérifiées contre les pages
  réelles ;
* **stockage** — idempotence de la collecte, révision d'une valeur, agrégat
  monde et signalement de couverture partielle ;
* **écriture de la configuration** — aller-retour fidèle de `config.yaml`, y
  compris accents et zone Mojo vide, et lecture d'un ancien fichier qui
  contenait les réglages de courriel. Un rendu qui perdrait une valeur
  effacerait silencieusement des réglages ;
* **planification** — la définition XML de la tâche (déclencheur quotidien et
  d'ouverture de session, guillemets, échappement). Une erreur y est
  invisible : Windows annonce un succès et rien ne s'exécute ;
* **ouverture hebdomadaire** — le rapport s'ouvre une fois par semaine ISO
  (lundi, ou mardi si le lundi est manqué), la collecte une fois par jour, et
  un navigateur qui échoue ne fait pas perdre la semaine ;
* **échecs de collecte** — la fenêtre d'erreur s'ouvre quand il le faut et
  seulement alors, montre le message exact de chaque source, y compris pour une
  erreur imprévue, et une fenêtre impossible à afficher ne masque jamais
  l'erreur d'origine ; le rapport de secours s'ouvre avec son avertissement sans
  consommer la semaine ;
* **détection d'une panne réseau** — hors ligne, toutes les sources sont en
  erreur (et non « vides »), une source jointe mais sans donnée ne l'est pas, un
  serveur injoignable est abandonné, et un Internet qui marche n'est jamais pris
  pour une coupure ;
* **exécutable** — la tâche planifiée vise toujours l'exécutable sans console,
  même si on l'installe depuis la version console ;
* **page du rapport** — document HTML autonome en UTF-8, sans dépendance
  externe : ouvert depuis le disque, un fichier sans `charset` afficherait
  « OdyssÃ©e ».

Pour mettre au point un parser sans marteler les sites :

```powershell
$env:INFOSHEBDO_HTTP_CACHE = "1"   # met en cache les pages dans data/cache/
```

---

## Évolutions possibles

* Graphiques et historique dans l'interface : la base contient déjà les séries,
  il ne manque qu'un onglet de visualisation.
* Sources payantes si le besoin le justifie : GSD, Circana, Famitsu, CBO.
* Extension du modèle à d'autres marchés (Italie, Espagne, Corée) : il suffit
  d'ajouter le code marché dans `config.yaml` pour Mojo et Steam.

---

## Licence

GPL-3.0 — voir `LICENSE`.
