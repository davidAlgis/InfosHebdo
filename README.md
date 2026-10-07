# InfosHebdo

Veille hebdomadaire du cinéma et du jeu vidéo, ouverte automatiquement dans le
navigateur.

L'outil collecte chaque jour ce que les sources publient, accumule l'historique
dans une base SQLite, puis ouvre le rapport de la semaine dans votre
navigateur : **le lundi si le poste est allumé, sinon le mardi**, et ainsi de
suite. Aucun compte, aucun serveur, aucun courriel.

```
Sources Internet
      ↓  collecte quotidienne (l'application, toute seule)
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

### Windows : un exécutable, rien à configurer

```powershell
pip install -r requirements.txt -r requirements-build.txt     # une fois
python build_exe.py
```

Le script lance les tests, fabrique l'exécutable avec PyInstaller, **le fait
tourner pour vérifier** que rien n'a été oublié (`selftest`), puis l'installe
dans `%LOCALAPPDATA%\Programs\InfosHebdo`, le dossier que Windows réserve aux
programmes installés sans droits administrateur. Rien n'est écrit dans le dossier
d'installation tant que la vérification n'a pas réussi.

**Il n'y a ensuite qu'à lancer `InfosHebdo.exe` une fois** (double-clic). Au
premier lancement, l'application :

1. s'inscrit au démarrage de Windows (entrée « Run » de votre compte, visible et
   désactivable dans le Gestionnaire des tâches, onglet « Démarrage ») ;
2. considère que c'est la première utilisation de la semaine : elle recherche les
   données, puis **ouvre le rapport dans le navigateur** ;
3. se replie dans la zone de notification, près de l'horloge.

Elle attend ensuite la semaine suivante pour rouvrir le rapport. Il n'y a plus
rien à lancer, ni tâche planifiée à créer.

Contenu du dossier installé :

| Élément | Rôle |
| --- | --- |
| `InfosHebdo.exe` | l'application (sans console : aucune fenêtre noire) |
| `config.yaml` | ce qui est suivi (voir plus bas) |
| `data/`, `reports/`, `logs/` | base SQLite, rapports générés, journal. **Une réinstallation n'y touche jamais** |

Options de `build_exe.py` : `--dest` (autre dossier), `--no-install` (fabriquer
seulement, résultat dans `build/dist`), `--with-data` (reprendre la base et les
imports du dépôt, sans jamais écraser), `--kill` (arrêter InfosHebdo s'il tourne,
pour pouvoir le remplacer), `--skip-tests`, `--clean`. Pour mettre à jour,
relancer le script : si l'application tourne, la quitter d'abord (icône →
Quitter) ou ajouter `--kill`, ses fichiers étant verrouillés tant qu'elle tourne.

### L'application

**La fenêtre** n'a que deux boutons :

* **Rechercher les données** : une collecte complète, à la demande ;
* **Ouvrir le rapport** : reconstruit le rapport depuis la base et l'ouvre dans le
  navigateur (utilisable à tout moment, pas seulement en début de semaine).

Elle affiche aussi la date de la dernière recherche et l'état du rapport de la
semaine. La croix la replie dans la zone de notification au lieu de quitter.

**L'icône près de l'horloge** : double-clic ou *Ouvrir InfosHebdo* pour la
fenêtre ; *Rechercher les données* et *Ouvrir le rapport* en un clic ; case
*Lancer au démarrage de Windows* (décochée, elle est définitivement respectée :
l'application ne se réinscrit plus jamais d'elle-même) ; *Quitter*.

**Ce qu'elle fait seule**, au lancement puis toutes les 30 minutes :

| Étape | Fréquence | Mémorisée par |
| --- | --- | --- |
| Collecte | une fois par jour, et seulement si au moins une source a répondu (hors ligne, elle réessaie 30 minutes plus tard) | journal des collectes en base |
| Ouverture du rapport | une fois par semaine ISO, au premier passage | clé `last_report_week` en base (ex. `2026-W41`) |

Une session laissée ouverte du dimanche au lundi ouvre donc le rapport le lundi ;
un poste éteint le lundi l'ouvre le mardi, à son premier démarrage ; une
installation neuve l'ouvre tout de suite, quel que soit le jour. Ouvrir le rapport
à la main compte comme l'avoir vu : il ne se rouvrira pas derrière. La collecte
passe toujours avant le rapport, pour que la page contienne les chiffres du jour.
Pour garder la collecte sans l'ouverture automatique : `report.auto_open: false`.

Relancer `InfosHebdo.exe` alors qu'elle tourne déjà ne crée pas de seconde copie :
la première affiche simplement sa fenêtre.

### Le rapport

Une page HTML autonome (`reports/infoshebdo-AAAA-MM-JJ.html`, avec sa version
texte) : aucun script, aucune feuille externe, elle s'ouvre hors ligne et se
déplace ou s'archive sans rien perdre.

* **Chaque classement est un panneau pliable.** Tous sont **pliés** à l'ouverture,
  sauf le box-office France, qui est déplié. Un panneau plié montre déjà son titre,
  sa période, sa fiabilité et le premier du classement. Les panneaux sont des
  éléments HTML natifs (`<details>`) : ils fonctionnent dans tous les navigateurs,
  sans JavaScript.
* **Chaque classement renvoie vers sa source**, sous le tableau : Allociné pour le
  box-office France, Box Office Mojo pour les autres marchés, les pages de
  classement de Steam par pays, SteamSpy. Le lien vise **la semaine exacte** du
  classement (par exemple `boxofficemojo.com/weekend/2026W40/?area=JP`), pas la
  page d'accueil de la source ; le « monde » additionnant plusieurs zones, il en
  liste une par zone. Quand il n'y a pas de donnée, le lien mène à la page
  générale de la source, pour vérifier si le problème vient d'elle. Les liens
  s'ouvrent dans un nouvel onglet. Les noms de jeux Steam renvoient à leur fiche,
  et les publications de classements repérées à l'article.
* Les chiffres importés à la main (CSV) n'ont pas de lien : leur source est un
  fichier local.

### Quand la collecte échoue

Une application sans console ne peut pas afficher d'erreur : **une fenêtre
s'ouvre au premier plan**, même si l'application est repliée, avec le message
exact de chaque source en erreur. Elle propose d'ouvrir le dernier rapport, le
journal, de relancer la recherche, et de copier le détail.

| Situation | Fenêtre | Rapport de la semaine |
| --- | --- | --- |
| Aucune source n'a répondu (poste hors ligne, par exemple) | oui | ouvert avec un **avertissement jaune en tête de page** (« les chiffres datent de la dernière collecte réussie, le JJ/MM/AAAA »). Semaine **non** marquée comme vue : le rapport à jour s'ouvrira dès qu'une collecte aboutira. Rouvert au plus une fois par jour tant que le réseau est coupé |
| Certaines sources seulement en erreur | oui | ouvert, avec un avertissement qui nomme les sources concernées |
| Étape d'agrégation du box-office monde en erreur | oui | ouvert |
| Le navigateur ne s'ouvre pas | oui, avec le chemin du rapport | semaine non marquée comme vue, retenté |
| Erreur imprévue (base verrouillée, bug) | oui, avec le détail technique | — |

Pour les vérifications automatiques, la fenêtre s'ouvre **au plus une fois par
jour** (hors ligne, la vérification échoue toutes les 30 minutes) ; pour une
recherche lancée à la main, elle s'ouvre toujours.

**Comment une panne est détectée.** Les collecteurs tolèrent les pages manquantes
(le box-office France et Japon arrive avec des semaines de retard) et les notent
sans échouer : sans réseau, toutes les sources auraient donc l'air « vides ». Le
client HTTP compte donc les requêtes : une source dont **aucune** requête n'a
abouti est en erreur, une source jointe mais sans donnée reste « vide ». Hors
ligne, un serveur injoignable est abandonné pour le reste de la collecte, et
après trois serveurs distincts sans aucune réponse les suivants ne sont plus
essayés : l'erreur apparaît en une trentaine de secondes, pas en plusieurs
minutes.

Le journal est `logs/infoshebdo.log`. Une erreur survenant avant sa mise en place
atterrit dans `logs/crash.log` : une application sans console ne doit jamais
échouer en silence.

### Réglages

Il n'y a pas d'écran de réglages : tout est dans `config.yaml`, à côté de
l'exécutable (`%LOCALAPPDATA%\Programs\InfosHebdo`). Le fichier est **relu à
chaque vérification et à chaque recherche** : une modification est prise en compte
sans relancer l'application.

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

Les chiffres que les sources libres ne publient pas (GSD, Circana, Famitsu)
s'ajoutent en déposant un CSV dans `data/import/` : voir
[Jeu vidéo — toutes plateformes](#jeu-vidéo--toutes-plateformes).

`.env` — facultatif, sans aucun secret : `INFOSHEBDO_DB`, `INFOSHEBDO_HTTP_DELAY`,
`INFOSHEBDO_HTTP_CACHE`, voir `.env.example`.

Un `config.yaml` ou un `.env` issu d'une version précédente (envoi par courriel,
interface à onglets) reste lisible : les clés `email`, `interface`,
`attach_html`, `open_after_send` et `open_on_ui_start` sont ignorées, et les
lignes `SMTP_*` / `MAIL_*` du `.env` n'ont plus d'effet.

### Depuis les sources (Python)

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m infoshebdo            # l'application, comme l'exécutable
```

Les commandes suivantes servent à la mise au point et au diagnostic. L'exécutable
n'ayant pas de console, elles se lancent depuis les sources.

| Commande | Rôle |
| --- | --- |
| `python -m infoshebdo` (ou `ui`) | l'application (`ui --minimized` : démarrer repliée) |
| `python -m infoshebdo auto` | une vérification : collecte du jour (si pas déjà faite), puis ouverture du rapport de la semaine (s'il ne l'a pas été). `--force` le rouvre, `--no-window` supprime la fenêtre d'erreur |
| `python -m infoshebdo collect` | collecte, sans rapport (`--backfill N`, `--only SOURCE`) |
| `python -m infoshebdo report` | générer le rapport dans `reports/` (`--open` pour l'ouvrir) |
| `python -m infoshebdo status` | état de la base, dernière collecte, rapport de la semaine |
| `python -m infoshebdo sources` | sources, ce qu'elles fournissent, leur fiabilité |
| `python -m infoshebdo config` | configuration effective |
| `python -m infoshebdo selftest` | vérifier qu'une installation est complète |

```powershell
# rattraper 12 semaines d'historique
python -m infoshebdo collect --backfill 12

# ne relancer qu'une source
python -m infoshebdo collect --only allocine_france
```

`run.py` est un point d'entrée équivalent, utilisable depuis n'importe quel
répertoire.

**Sous Linux ou macOS**, l'application s'ouvre de la même façon
(`python run.py`). Pour une exécution sans interface, une ligne cron par jour
suffit : la commande décide seule de ce qu'il y a à faire.

```
0 9 * * *  /chemin/vers/python /chemin/vers/InfosHebdo/run.py auto
```

L'ouverture dans le navigateur demande une session graphique ; sans elle, `auto`
collecte et laisse le rapport dans `reports/`.

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
infoshebdo.spec     spécification PyInstaller : un exécutable sans console
infoshebdo/
  cli.py            commandes en ligne (sans argument : l'application)
  config.py         lecture et écriture de config.yaml
  paths.py          emplacements de fichiers
  http.py           client HTTP : en-tête, réessais, délai de politesse, cache, bilan des requêtes
  weeks.py          calendriers de semaines (Mojo, France, ISO, Steam)
  db.py             schéma SQLite et accès
  pipeline.py       orchestration de la collecte, journalisation, isolation
  derive.py         données calculées (agrégat monde, résolution de titres)
  analysis.py       construction du rapport, calcul des évolutions
  report.py         rendu HTML et texte
  sources.py        adresses des pages qui publient les chiffres (liens du rapport)
  auto.py           vérification : collecte du jour, rapport de la semaine
  viewer.py         ouverture dans le navigateur, mémoire « une fois par semaine »
  resident.py       logique de l'application : démarrage de session, actions manuelles
  instance.py       instance unique (une seconde copie réveille la première)
  startup.py        lancement à l'ouverture de session (registre Windows)
  collectors/
    base.py           contrat commun, vocabulaire, registre
    boxofficemojo.py  recettes de week-end USD, zones US/FR/JP
    allocine.py       entrées France
    steam.py          classements de ventes par pays, joueurs simultanés
    steamspy.py       propriétaires estimés
    editorial.py      veille RSS des publications de classements
    manual.py         import CSV
  ui/
    app.py            fenêtre principale (deux boutons), minuterie de 30 minutes
    tray.py           icône de la zone de notification et son menu
    error_window.py   fenêtre d'erreur
    runner.py         exécution en tâche de fond, messages par file d'attente
    files.py          ouverture d'un fichier ou d'un dossier
```

Quatre principes de conception :

* **Tout est en Python, et rien n'est à installer à la main.** Aucun script
  PowerShell ni `.cmd`, aucune tâche planifiée : l'application est résidente,
  s'inscrit seule au démarrage de la session, et décide elle-même de ce qu'il y a à
  faire (voir `auto.py`). Une application résidente remplace la tâche planifiée
  parce qu'elle sait aussi se montrer : la fenêtre d'erreur et les deux boutons
  vivent dans le même programme.
* **Aucun appel Tk depuis un fil de travail.** Une collecte dure une à deux
  minutes ; elle tourne dans un thread qui ne fait que déposer son résultat et un
  message dans une file, vidée par la fenêtre sur son propre fil.
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

216 tests, aucun accès réseau ni navigateur (les tests de fenêtres créent de
vraies fenêtres Tk, jamais affichées ; ils sont ignorés sans écran). Ce qu'ils
verrouillent :

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
* **ouverture hebdomadaire** — le rapport s'ouvre une fois par semaine ISO
  (lundi, ou mardi si le lundi est manqué), la collecte une fois par jour, et
  un navigateur qui échoue ne fait pas perdre la semaine. Les dates simulées
  sont dans une semaine lointaine : un test ne doit pas dépendre du jour où on
  le lance ;
* **application résidente** — inscription au démarrage une seule fois et refus de
  l'utilisateur définitif, premier lancement traité comme la première utilisation
  de la semaine (n'importe quel jour), session laissée ouverte du dimanche au
  lundi, une seule fenêtre d'erreur par jour hors ligne, les deux boutons et le
  menu de l'icône, repli dans la zone de notification ;
* **instance unique** — de vrais sockets locaux : une seconde copie réveille la
  première, et un autre programme sur le même port n'est pas pris pour elle ;
* **échecs de collecte** — la fenêtre d'erreur s'ouvre quand il le faut et
  seulement alors, montre le message exact de chaque source, y compris pour une
  erreur imprévue, et une fenêtre impossible à afficher ne masque jamais
  l'erreur d'origine ; le rapport de secours s'ouvre avec son avertissement sans
  consommer la semaine ;
* **détection d'une panne réseau** — hors ligne, toutes les sources sont en
  erreur (et non « vides »), une source jointe mais sans donnée ne l'est pas, un
  serveur injoignable est abandonné, et un Internet qui marche n'est jamais pris
  pour une coupure ;
* **exécutable** — l'entrée de démarrage vise l'exécutable empaqueté, avec son
  chemin entre guillemets s'il contient des espaces ;
* **panneaux et liens de source** — chaque classement est un panneau plié, sauf le
  box-office France ; les liens visent la semaine exacte, sont les mêmes pages que
  celles que la collecte télécharge, s'ouvrent dans un nouvel onglet et échappent
  les libellés ;
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
