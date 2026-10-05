# Analyse des performances Community Management (exports Meta Business Suite)

Système d'analyse qui transforme des exports CSV Meta Business Suite — de structures variables — en objectifs de performance chiffrés, benchmarks sectoriels et indicateurs de fiabilité, destinés à fonder des décisions de prime de façon objective.

---

## 1. Installation

```bash
pip install -r requirements.txt
```

Python 3.11+ recommandé (testé sur 3.13).

## 2. Utilisation

Déposez vos exports CSV dans `data/raw/` puis lancez :

```bash
python -m socialstats.main            # depuis la racine, avec src/ sur le PYTHONPATH
```

Sous PowerShell :

```powershell
$env:PYTHONPATH = "src"
python -m socialstats.main
```

Aucun nombre de fichiers, de comptes ou de clients n'est codé en dur : ajouter un CSV dans `data/raw/` suffit.

### Options

| Option | Effet |
|---|---|
| `--accounts "Compte A" "Compte B"` | Restreint l'analyse à certains comptes |
| `--sectors Optique Mode` | Restreint l'analyse à certains secteurs |
| `--period last_3_months` | Surcharge la période (`all`, `last_3_months`, `last_6_months`, `last_12_months`) |
| `--min-observations 15` | Surcharge le seuil d'échantillon minimum |
| `--no-charts`, `--no-excel` | Désactive une sortie |
| `--verbose` | Journalisation détaillée |

### Interface de contrôle mensuel

Pour vérifier les résultats d'un mois, compte par compte et catégorie par catégorie :

```powershell
.\lancer_interface.bat
```

ou, sans le lanceur :

```powershell
$env:PYTHONPATH = "src"
python -m socialstats.server          # options : --lan, --port 8060, --no-browser, --verbose
```

Le navigateur s'ouvre sur `http://127.0.0.1:8050/`. L'interface est en HTML, CSS et JavaScript (dossier `web/`), sans framework ni ressource externe : elle fonctionne hors ligne. Le serveur n'écoute que sur la machine locale et n'ajoute aucune dépendance ; tous les chiffres viennent du pipeline Python, il n'en existe pas de seconde version côté navigateur.

| Onglet | Contenu |
|---|---|
| Vue d'ensemble | Résultats du mois par compte × format, et grille des niveaux atteints mois par mois |
| Par compte | Les comptes d'un client, l'historique mensuel d'un format face à ses paliers, les publications du mois |
| Par catégorie | La catégorie face à ses paliers communs, et le nombre de comptes qui atteignent leur propre Niveau 1 |
| Données | Exports chargés, bilan du nettoyage, contrôle du comptage des vues |

**Partager sur le réseau local** : par défaut, seul le poste qui lance le serveur peut ouvrir l'interface. Pour que les autres postes du même réseau la consultent :

```powershell
.\lancer_interface.bat --lan
```

Le serveur affiche alors l'adresse à leur donner, de la forme `http://192.168.1.147:8050/` (l'adresse `127.0.0.1` ne désigne jamais que le poste lui-même). Trois choses à savoir :

- **Pas de mot de passe.** Toute personne connectée au même réseau et connaissant l'adresse voit les résultats. À réserver à un réseau de confiance.
- **Consultation seule pour les autres postes.** L'ajout d'exports reste réservé au poste qui héberge le serveur : la déduplication retenant la valeur la plus haute de chaque compteur, un export retouché déposé depuis un autre poste gonflerait des résultats.
- **Pare-feu Windows.** Il bloque les connexions entrantes tant qu'une règle ne les autorise pas. À créer une fois, dans un PowerShell lancé en administrateur (ici limitée au Wi-Fi et aux postes du même sous-réseau) :

```powershell
New-NetFirewallRule -DisplayName "Controle mensuel CM" -Direction Inbound -Protocol TCP -LocalPort 8050 -InterfaceAlias "Wi-Fi" -RemoteAddress LocalSubnet -Action Allow
```

Pour la retirer : `Remove-NetFirewallRule -DisplayName "Controle mensuel CM"`.

**Ajouter un mois** : bouton « Ajouter des exports ». Un fichier n'est rangé dans `data/raw/` qu'après avoir été lu et reconnu ; un fichier existant n'est jamais écrasé, et la déduplication absorbe les chevauchements.

**Période de référence** : les objectifs sont calculés sur une période, puis le mois choisi leur est comparé. Par défaut la référence s'arrête *avant* le mois contrôlé, pour que ses publications ne fixent pas les seuils auxquels elles sont jugées. On peut la restreindre aux 3 ou 6 mois précédents, ou la choisir librement.

**Lecture des niveaux** : le Niveau 1 étant la médiane du compte, un mois « sous le Niveau 1 » arrive par construction une fois sur deux. Il est donc affiché en neutre, pas en alerte ; ce qui doit attirer l'œil est une série de mois sous le Niveau 1, visible dans la grille.

**Contrôle du comptage des vues** : l'onglet « Données » trace le nombre de vues par personne touchée. Ce ratio ne dépend pas de la taille de l'audience ; s'il change d'échelle pendant la période de référence, les seuils en vues mélangent deux façons de compter, et les séries concernées sont mises en couleur sur le graphique. C'est le cas d'Instagram photo et carrousel sur les exports actuels (environ 4 vues par personne jusqu'en novembre 2025, environ 1,7 ensuite) : pour ces formats, préférer une référence postérieure à janvier 2026.

### Mise en ligne (Vercel + Supabase)

L'interface peut aussi être consultée en ligne. Les calculs restent faits sur ce poste, par le pipeline ; Supabase ne stocke que des résultats prêts à afficher, et le site hébergé sur Vercel se contente de les lire. Il n'existe donc toujours qu'une seule version des objectifs.

```
exports CSV  ->  pipeline Python (ce poste)  ->  Supabase  ->  interface sur Vercel
```

**Mise en place, une seule fois**

1. Dans Supabase, ouvrir *SQL Editor*, coller le contenu de `supabase/schema.sql` et l'exécuter. Il crée trois tables (`controles`, `publications`, `etat`) et un dépôt privé `exports`.
2. Copier `.env.example` sous le nom `.env` et y renseigner les deux clés (*Project Settings > API Keys*). `.env` n'est pas versionné.

**Chaque mois**

```powershell
.\publier.bat
```

La commande nettoie les exports de `data/raw/`, calcule un contrôle par mois et par période de référence (environ deux minutes pour une année), envoie le tout, puis écrit `web/config.js`. Il reste à pousser le dossier `web/` vers son dépôt pour que Vercel redéploie, uniquement quand un fichier de `web/` a changé. Pour tout calculer sans rien envoyer : `.\publier.bat --dry-run`.

**Ce que contient Supabase**

| Emplacement | Contenu | Lisible avec la clé publique |
|---|---|---|
| `controles` | Une réponse précalculée par mois contrôlé et par période de référence | Oui |
| `publications` | Les publications organiques retenues, avec leurs mesures | Oui |
| `etat` | Mois disponibles, références publiées, date de publication | Oui |
| Dépôt `exports` | Les exports Meta bruts, en archive | Non |

**Deux clés, deux rôles.** La clé secrète écrit dans la base : elle ne se trouve que dans `.env`. La clé publique ne permet que de lire : c'est la seule recopiée dans `web/config.js`. La commande refuse de publier si les deux sont interverties.

**Accès public.** Aucune connexion n'est demandée : toute personne qui a l'adresse du site voit les résultats, et la clé publique permet de lire directement les trois tables. Le site demande aux moteurs de recherche de ne pas l'indexer, ce qui n'empêche pas un lien de circuler.

**Différences avec l'interface locale.** En ligne, on consulte seulement : pas d'ajout d'exports, quatre périodes de référence (tout l'historique précédent, 6 mois, 3 mois, toute la période) et un minimum de publications fixé à la publication (`--min-posts`, 3 par défaut). La période personnalisée reste propre à l'interface locale.

### Tests

```bash
python -m pytest
```

181 tests, dont une suite d'intégration qui s'exécute sur les vrais exports présents dans `data/raw/` (ignorée automatiquement si le dossier est vide).

---

## 3. Configuration

Tout est pilotable depuis `config/`, sans toucher au code.

**`config/settings.yaml`** (extrait) :

```yaml
analysis_period: "all"
exclude_sponsored: true
min_observations: 10

reliability:
  insufficient_below: 5
  low_below: 10
  medium_below: 30

sector:
  min_accounts: 3
  min_publications: 20
  min_observations_per_account: 5

objectives:
  level_1_quantile: 0.50
  level_2_quantile: 0.70
  level_3_quantile: 0.80
  rounding: 5

outliers:
  method: "iqr"
  multiplier: 1.5

trend:
  min_observations: 8
  change_threshold_pct: 15
```

**`config/clients.csv`** — rattachement des comptes aux clients et aux secteurs.

```csv
account_name,client,sector,cm_name
SODIGAZ TOGO,Sodigaz,Carburant,
Sodigaztg,Sodigaz,Carburant,
𝗖𝗟𝗔𝗜𝗥 𝗢𝗣𝗧𝗜𝗖 𝗧𝗢𝗚𝗢,Clair Optic,Mode Lifestyle et Bien-etre,
```

- `account_name` : le nom **tel qu'il apparaît dans l'export**. Le rapprochement se fait sur le nom normalisé, donc la casse, les accents, les emojis et l'Unicode stylisé n'ont pas d'importance.
- `client` : la marque. Deux comptes (page Facebook + profil Instagram, ou deux pays) peuvent pointer vers le même client.
- `cm_name` : facultatif. S'il est renseigné, une synthèse par Community Manager est produite en plus, chaque compte pesant pour 1.

Un compte absent de ce fichier est traité comme « Non catégorisé » et signalé dans les logs, jamais silencieusement ignoré. Si deux lignes désignent le même compte après normalisation, le chargement échoue avec un message explicite plutôt que d'en écraser une.

**`config/column_mappings.yaml`** — dictionnaire des variantes de noms de colonnes. C'est ici qu'on ajoute le support d'un nouveau format d'export.

---

## 4. Lecture des tendances : brute vs relative

Sur les données réelles, **13 comptes Instagram sur 17 reculent simultanément** (jusqu'à -83 %), alors que la médiane globale du parc reste stable (428 → 430 vues). Un recul aussi généralisé traduit une évolution de la plateforme — algorithme, saisonnalité, concurrence — et non treize échecs individuels.

Le système produit donc deux lectures :

- **Variation brute** : l'évolution du compte sur la période.
- **Tendance relative** : l'écart entre cette variation et la médiane des variations des comptes de la même plateforme.

Exemple concret : la référence Instagram est **-61,2 %**. Un compte à -64 % est donc *conforme à son marché*, pas défaillant ; un compte à -83 % décroche réellement.

**C'est la tendance relative qui doit fonder une décision de prime**, jamais la variation brute — sinon on sanctionne un CM pour une baisse subie par tout le marché. Quand la référence repose sur moins de `sector.min_accounts` comptes, elle est marquée « Référence plateforme insuffisante » plutôt que présentée comme un fait.

---

## 5. Objectifs par catégorie

Deux niveaux d'objectifs coexistent, et ils ne servent pas à la même chose :

| Table | Grain | Usage |
|---|---|---|
| `objectifs_performance.csv` | compte × plateforme × format | **Évaluer un CM** sur l'historique de son propre compte |
| `objectifs_par_categorie.csv` | secteur × plateforme × format | Situer une catégorie, cadrer un nouveau compte sans historique |

### Pourquoi pas les quantiles poolés du benchmark

Mettre toutes les publications d'un secteur dans un seul sac donne à chaque compte un poids proportionnel à son volume de publication. Sur les données réelles, Mode/Lifestyle · Instagram · Photo ressort à **383 vues en poolé contre 109 en médiane par compte** : l'écart vient d'un seul compte qui publie 342 photos très exposées. Fixer 383 comme objectif de catégorie condamnerait les six autres comptes à l'échec pour une raison étrangère à leur travail.

**Méthode retenue** : chaque compte qualifié calcule ses propres paliers, puis on prend la **médiane de ces paliers entre comptes**. Chaque compte pèse 1. C'est la transposition directe de la « médiane des médianes » du benchmark.

### Garde-fou d'hétérogénéité

Si le rapport entre le compte le plus fort et le plus faible dépasse `objectives.max_level_spread`, la catégorie est signalée comme trop hétérogène pour porter une cible commune. C'est le cas de Mode/Lifestyle (rapport **30×** entre Woodin et Pressing du Soleil) : la catégorie mélange des réalités trop différentes, et seuls les objectifs par compte y ont du sens.

### Lecture des taux d'atteinte

Attention : au niveau catégorie, « Atteinte N1 = 79 % » ne signifie pas que l'objectif est trop facile. La règle « ~50 % des publications » vaut **par compte** ; agrégée sur la catégorie, elle est mécaniquement dépassée par les comptes à fort volume qui publient au-dessus du compte type.

---

## 6. Ce que l'analyse des vrais exports a révélé

Le système a été conçu à partir d'exports réels (de 18 à 302 colonnes, Facebook et Instagram), et plusieurs choix découlent directement de ce qu'ils contenaient :

1. **Un fichier ≠ un compte.** Chaque export mélange plusieurs pages ou profils. Le regroupement se fait par colonne, pas par fichier.
2. **La colonne `Date` vaut littéralement `"Global"`** sur 100 % des lignes : ce sont des exports « lifetime ». La vraie date est dans `Heure de publication` (format `MM/DD/YYYY HH:MM`).
3. **`Statut du contenu financé` est vide sur 100 % des lignes**, alors que des publications ont des vues boostées non nulles. Une détection du sponsorisé fondée sur cette seule colonne aurait manqué la totalité des cas.
4. **Les Reels sont étiquetés « Vidéos » dans les exports Facebook récents.** Seul le permalien `/reel/` les distingue — 240 Reels étaient concernés. Les confondre avec des vidéos classiques aurait faussé les objectifs, un Reel ne générant pas le même ordre de grandeur de vues.
5. **Chevauchement réel entre fichiers** : des publications apparaissent dans plusieurs exports. Sans déduplication, elles pèsent double.
6. **Exports bilingues.** Un fichier contient à la fois `Type de publication`/`Post type` et `Permalien`/`Permalink`, chacune ne couvrant qu'une partie des lignes. Le mapper **fusionne les colonnes par ordre de priorité** au lieu d'en choisir une seule : 81 publications auraient sinon perdu leur format et leur lien.
7. **Noms de comptes en Unicode stylisé.** Instagram accepte les lettres cerclées et le gras mathématique : `Ⓐ Ⓑ Ⓘ 'Ⓢ Ⓒ Ⓡ Ⓔ Ⓐ Ⓜ` (= Abi's Cream), `𝗖𝗟𝗔𝗜𝗥 𝗢𝗣𝗧𝗜𝗖 𝗧𝗢𝗚𝗢`. Ces noms sont normalisés (NFKD + recollage des lettres isolées) pour le rapprochement avec `clients.csv`, et transcrits en ASCII pour l'affichage — sans quoi les graphiques affichent des carrés vides.
8. **Un même client porte des noms différents selon la plateforme** : `SODIGAZ TOGO` (IG) vs `Sodigaztg` (FB), `DAGAN MAGAZINE` vs `Dagan Magazine`. La normalisation résout les variantes de casse ; les cas irréductibles sont rattachés explicitement via la colonne `client` de `clients.csv`.
9. **Instagram n'expose ni « Réactions » ni ventilation organique/payant** : il fournit `Mentions J'aime`, `Enregistrements` (inclus dans l'engagement, signal fort sur IG) et `Followers en plus` (acquisition, à ne pas confondre avec le nombre d'abonnés).
10. **Aucune colonne « nombre d'abonnés »** n'existe dans ces exports par publication : la normalisation par taille d'audience est donc impossible à ce stade, et le système ne la simule pas.

---

## 7. Choix statistiques et justifications

### Métrique principale : vues organiques par publication

Résolution en cascade, volontairement prudente :

1. colonne de vues organiques dédiée si le fichier la fournit ;
2. sinon, si la publication n'est pas sponsorisée, les vues totales (organiques par définition) ;
3. sinon `NaN` — une publication sponsorisée sans détail n'est jamais requalifiée en organique.

L'étape 2 est indispensable : les exports Instagram et certains exports Facebook ne contiennent aucune ventilation organique/payant. Sur le jeu réel, elle récupère 1935 publications sur 2685 — les ignorer aurait vidé l'analyse.

### Taux d'engagement

`(réactions + commentaires + partages [+ enregistrements sur Instagram]) / base × 100`

La base suit une cascade — couverture organique, puis couverture totale si la publication est non sponsorisée, puis vues en dernier recours. **La base utilisée est tracée ligne par ligne** : un taux calculé sur la couverture et un taux calculé sur les vues n'ont pas la même sémantique, et les mélanger sans le signaler produirait une comparaison trompeuse. Division par zéro et données manquantes donnent `NaN`, jamais l'infini ni un zéro artificiel.

### Outliers : détecter n'est pas supprimer

Une publication virale est une performance réelle, pas une erreur de mesure. Le système :

- **signale** les publications hors bornes IQR (facteur configurable) comme « exceptionnelles », **sans jamais les supprimer** ;
- calcule les bornes **au sein de chaque groupe** de comparaison : une photo n'est pas atypique parce qu'elle fait moins de vues qu'un Reel ;
- fournit côte à côte moyenne, médiane, moyenne tronquée 10 %, écart-type, Q1/Q3/IQR et MAD, pour rendre visible l'écart entre estimateurs.

Une table `comparaison_methodes_outliers` compare explicitement les cinq approches (moyenne, médiane, moyenne tronquée, IQR, MAD) plutôt que d'imposer un choix en silence. Le MAD dégénère quand plus de la moitié des valeurs sont identiques ; ce cas est détecté et neutralisé au lieu de déclarer aberrante toute valeur différente de la médiane.

### Objectifs à trois niveaux : quantiles empiriques

Niveau 1 = P50, Niveau 2 = P70, Niveau 3 = P80 (configurables).

Pourquoi les quantiles plutôt qu'une moyenne ± k·écart-type :

1. **Ils traduisent directement la règle métier.** « Atteignable par environ 50 % des publications » *est*, par définition, le 50e percentile. Une approche moyenne/écart-type n'offrirait aucune garantie de ce type sur des distributions asymétriques comme les vues.
2. **Ils sont robustes par construction.** Une publication virale déplace la moyenne mais quasiment pas la médiane — aucune suppression d'outlier n'est donc nécessaire, ce qui évite de devoir décider arbitrairement qu'un vrai succès est une « erreur ». Un test vérifie explicitement qu'ajouter une publication à 500 000 vues ne change pas le Niveau 1.
3. **Ils restent interprétables** par la direction : « ce seuil a été atteint par X % des publications de ce compte ».

Détails d'implémentation : interpolation `lower` (un seuil doit correspondre à une performance réellement observée, pas à une interpolation entre deux publications), arrondi vers le haut au multiple configuré (ne jamais annoncer un seuil plus facile que celui calculé), et progression stricte garantie entre paliers même après arrondi.

**Vérification empirique sur les données réelles** : les taux d'atteinte obtenus sont **48,6 % / 32,0 % / 21,6 %**, contre une cible de 50 / 30-35 / 20. La méthode tient sa promesse.

### Benchmark sectoriel : médiane des médianes

Le risque principal est qu'un gros compte dicte le benchmark. Dans les données réelles, un compte pèse 133 photos quand un autre en pèse 7 : un simple regroupement de toutes les publications ne serait que le portrait du plus gros compte.

Méthode retenue : **chaque compte pèse pour 1**. On calcule la médiane de chaque compte, puis la médiane de ces médianes. Les quantiles « poolés » sont conservés **en parallèle**, explicitement étiquetés « pondéré par le volume ». Les deux lectures sont utiles — l'une décrit le compte typique, l'autre la publication typique — et les afficher côte à côte évite de faire passer l'une pour l'autre.

Un compte doit atteindre `min_observations_per_account` pour être « qualifié » : un compte à 2 publications ne doit pas peser autant qu'un compte établi.

### Fiabilité statistique

| Effectif | Niveau | Conséquence |
|---|---|---|
| n < 5 | Insuffisante | Aucun seuil produit |
| 5 ≤ n < 10 | Faible | Seuils marqués « indicatifs » |
| 10 ≤ n < 30 | Moyenne | Exploitable |
| n ≥ 30 | Élevée | Exploitable |

En dessous de `min_observations` (défaut 10), **aucun seuil chiffré n'est produit** — seulement des statistiques descriptives et un avertissement. Sur 8 publications, un P80 reposerait sur 1 ou 2 points et donnerait une précision illusoire.

Un benchmark sectoriel est marqué `INSUFFISANT` s'il repose sur moins de `min_accounts` comptes qualifiés **ou** moins de `min_publications` publications, avec le motif précis. Un secteur à un seul compte est toujours insuffisant, par construction.

### Tendances

Deux méthodes indépendantes : comparaison médiane début / médiane fin (segments par tiers), et régression linéaire sur le rang chronologique (pente, R², p-value). Les médianes limitent l'effet d'une publication virale isolée en début ou fin de période.

Si les deux méthodes se contredisent, le verdict est « Tendance incertaine » avec les deux chiffres : mieux vaut afficher un doute qu'une conclusion fausse qui pourrait coûter une prime à quelqu'un.

---

## 8. Sorties produites

```
output/
  csv/       13 tables (statistiques, objectifs, benchmark, tendances, formats, qualité…)
  excel/     analyse_performances.xlsx (toutes les tables en onglets)
  charts/    5 graphiques PNG
  reports/   rapport_recommandations.txt + execution.log
data/processed/cleaned_social_data.csv
```

**Table des objectifs** — `Compte | Secteur | Plateforme | Format | Niveau 1 | Niveau 2 | Niveau 3 | Engagement cible | Fiabilité`, avec en plus les taux d'atteinte réels de chaque palier (contrôle a posteriori).

**Table de qualité** — traçabilité complète : colonnes mappées par fichier, publications sponsorisées exclues et signal déclencheur, doublons fusionnés et chevauchements entre fichiers, lignes en quarantaine avec motif.

### Exemple de résultat (données réelles, 434 publications organiques, 6 comptes)

```
Compte                Format  Publi.  N1     N2     N3     Atteinte N1/N2/N3
Abi's Cream           photo    77     145    175    200    46.8 / 29.9 / 20.8
Bonici Africa         photo    38     240    270    300    47.4 / 31.6 / 21.1
Clair Optic Togo      photo    28     130    145    155    50.0 / 35.7 / 25.0
DOM'S Restaurant      reel     17     430    510    530    47.1 / 35.3 / 23.5
Woodin Fashion Togo   photo   133     925   1130   1285    48.9 / 30.1 / 20.3
```

Extrait du rapport de recommandations :

```
- 33 publication(s) sponsorisee(s) exclue(s) (6.5 % du total)
- 44 publication(s) presente(s) dans plusieurs fichiers ont ete fusionnees
- Reel : 402 vues medianes ; Photo : 182 ; Video : 168
- DOM'S Restaurant : performance en progression (196 -> 338 vues, +73.1 %)
- Clair Optic Togo : performance en baisse (153 -> 101 vues, -34.0 %)
- Pressing du Soleil / photo : donnees insuffisantes (7 publications)
```

---

## 9. Limites assumées

- **Instagram hors périmètre.** Aucun export IG réel n'était disponible. La dimension `platform` existe dans tout le schéma (l'ajouter ne demandera pas de refonte), mais aucune heuristique de détection Instagram non testable n'a été écrite : un fichier de signature inconnue est **rejeté avec un message explicite** plutôt que classé au hasard.
- **Pas de normalisation par audience.** Les exports par publication ne contiennent pas le nombre d'abonnés. Un compte qui gagne massivement des abonnés verra ses vues croître pour une raison qui n'est pas la qualité éditoriale — le système ne peut pas corriger cet effet et ne prétend pas le faire.
- **Comparabilité dans le temps.** L'algorithme Meta et la saisonnalité évoluent ; les seuils doivent être **recalculés trimestriellement** plutôt que figés.
- **Benchmark sectoriel structurellement bloqué sur le jeu actuel** : 6 comptes répartis sur 5 secteurs, aucun secteur n'atteint 3 comptes. Le rapport le dit explicitement et propose les deux leviers (regrouper les secteurs, ou abaisser `min_accounts` en acceptant une référence moins solide).
- **Les vues organiques seules ne suffisent pas à juger un CM.** Elles ne capturent ni la charge de production, ni la qualité du service client en commentaires, ni les contraintes imposées par le client. Le système fournit une base objective de discussion, pas un verdict automatique.

---

## 10. Architecture

```
config/          settings.yaml, clients.csv, column_mappings.yaml
data/raw/        exports CSV bruts
data/processed/  cleaned_social_data.csv
src/socialstats/
    config.py            chargement + validation typée de la configuration
    loader.py            découverte et lecture robuste (encodages, séparateurs)
    schema_mapper.py     normalisation des noms, mapping canonique, format, plateforme
    sponsored.py         détection du sponsorisé, résolution des vues organiques
    dedup.py             fusion des publications présentes dans plusieurs exports
    validator.py         quarantaine des lignes inexploitables, avec motif
    cleaner.py           orchestration du pipeline de nettoyage
    engagement.py        taux d'engagement et gestion des cas dégénérés
    outliers.py          statistiques robustes, détection non destructive
    descriptive.py       statistiques par compte × plateforme × format
    objectives.py        seuils à 3 niveaux + fiabilité
    sector_analysis.py   benchmark sectoriel + positionnement des comptes
    trends.py            évolution temporelle (segments + régression)
    format_analysis.py   comparaison des formats, ratios
    cm_rollup.py         agrégation optionnelle par Community Manager
    reporting.py         tables et rapport de recommandations
    visualization.py     graphiques
    main.py              CLI
    monthly.py           contrôle mensuel : référence, niveaux atteints, catégories, comptage des vues
    ingest.py            ajout contrôlé d'un export à data/raw
    server.py            serveur local de l'interface (bibliothèque standard uniquement)
    publish.py           publication des résultats vers Supabase pour l'interface en ligne
supabase/        schema.sql : tables et règles d'accès à créer une fois
web/             index.html, app.css, app.js : interface de contrôle mensuel
output/          csv/ excel/ charts/ reports/
tests/           181 tests, dont intégration sur les exports réels
```

Le module de statistiques descriptives s'appelle `descriptive.py` et non `statistics.py` afin de ne pas masquer le module `statistics` de la bibliothèque standard.
