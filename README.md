# JHR Chiffrage

Application de bureau native pour chiffrer les études et dessins : affaires, ouvrages par onglets, postes et sous-postes, gabarits, heures/minutes, taux horaire HT, TVA et prélèvements estimés. Interface Qt pour Windows et Ubuntu, sans navigateur.

Pour dupliquer un ouvrage, faire un clic droit sur son onglet puis **Dupliquer cet ouvrage**. Pour un poste et ses sous-postes, faire un clic droit sur sa ligne puis **Dupliquer le poste et ses sous-postes**, ou sélectionner la ligne et utiliser **Ctrl+D**. Les copies sont indépendantes et restent dans le brouillon ; cliquer sur **Enregistrer** pour les conserver et les synchroniser.

Dans la fenêtre d'un poste ou sous-poste, saisir une désignation puis cliquer sur **Mémoriser** pour la retrouver dans les suggestions lors des prochaines saisies. Sélectionner une suggestion au clavier ou à la souris ; seules les lettres de la désignation sont reprises, sans changer les heures ni le tarif. **Paramètres → Désignations fréquentes…** permet d'ajouter ou retirer des suggestions sans modifier les postes existants. La bibliothèque reste disponible hors ligne et se synchronise en mode serveur (serveur et clients 0.4.0 ou ultérieurs pour cette fonction).

Le bas fixe du tableau affiche le total des heures et le total HT de l'ouvrage actif, postes et sous-postes compris, même pendant le défilement.

La colonne **Poste seul HT** conserve le montant propre de chaque ligne. La colonne **Total HT** affiche uniquement, sur les postes principaux, le cumul du poste et de tous ses descendants (sous-postes, petits-enfants et niveaux suivants). Les cases Total HT des descendants restent vides. Replier une branche ne change pas les montants ; aucun poste n’est compté deux fois.

## Installer sur Ubuntu

Python 3.11 minimum, module venv, Git et Internet requis. Ubuntu 24.04 fournit Python 3.12 ; le Python 3.10 d'Ubuntu 22.04 est trop ancien. L'installateur indique les bibliothèques graphiques manquantes et n'installe aucun paquet système automatiquement.

```sh
git clone https://github.com/JoLaFripouille/jhr-chiffrage.git
cd jhr-chiffrage
bash installer.sh
```

Ouvrir ensuite **JHR Chiffrage** depuis les applications, ou lancer `~/.local/bin/jhr-chiffrage`.

## Mettre à jour

Enregistrer puis fermer l'application et son agent MCP. Depuis le dossier cloné :

```sh
bash mettre-a-jour.sh
```

La mise à jour installe une nouvelle version dans un environnement privé. Elle conserve les anciens environnements et ne remplace pas la base locale. Le lanceur est actualisé après vérification des dépendances. Le script refuse de remplacer un code modifié localement.

Sans Git : télécharger [la dernière archive Ubuntu](https://github.com/JoLaFripouille/jhr-chiffrage/releases/latest), l'extraire et exécuter `bash installer.sh`. Les mises à jour se font alors en installant l'archive suivante.

[Instructions Ubuntu détaillées](packaging/linux/LIRE-MOI-UBUNTU.md).

## Données privées

Le dépôt et les archives ne contiennent ni affaires, ni sauvegardes, ni captures personnelles. Les données restent sur l'ordinateur :

- Windows : `%USERPROFILE%\.jhr-chiffrage\chiffrage.sqlite3`.
- Ubuntu : `~/.local/share/JHRChiffrage/chiffrage.sqlite3`, ou le répertoire XDG personnalisé.
- Autre emplacement : variable `JHR_CHIFFRAGE_DB`.

GitHub met à jour le logiciel, pas les affaires. En mode local, utiliser **Paramètres → Créer une sauvegarde des données** pour transporter une copie séparément. Ne pas synchroniser une base ouverte via OneDrive.

## Serveur partagé et travail hors ligne (version 0.3)

Un PC peut conserver la base commune pour les applications Windows/Ubuntu et leur agent IA. Dans **Paramètres → Configurer la connexion**, choisir le serveur, l'adresse HTTPS, la clé d'accès et le certificat public. Tester puis relancer. La première connexion prépare une copie locale des affaires et gabarits. Ensuite, le logiciel permet de chiffrer et d'enregistrer sans réseau, même après redémarrage.

Avant de partir, cliquer sur **Synchroniser** et vérifier **À jour**. Au retour sur le réseau du serveur, les modifications enregistrées se synchronisent automatiquement ou via ce bouton. Les changements concurrents sont signalés : **Conserver les deux versions** garde des copies au lieu d'écraser une affaire. Le serveur et les clients doivent être en version 0.3.0 ou ultérieure. Le mode hors ligne n'ajoute pas d'accès distant : dans le train, les changements restent sur le PC jusqu'à ce que le serveur soit accessible.

[Installer et utiliser le serveur](SERVEUR.md). Les certificats privés, clés d'accès et configurations personnelles restent hors du dépôt. Cette première version utilise une clé partagée, sans comptes individuels, et vise le réseau local.

## Agent IA / MCP

Le serveur local stdio se lance avec `~/.local/bin/jhr-chiffrage-mcp` sur Ubuntu ou `python -m jhr_chiffrage.mcp_server`. Il partage les services métier et la base locale de l'interface. Les profils `JHR_MCP_ACCESS=read`, `draft` ou `full` limitent les opérations disponibles. Aucun raccordement à un agent n'est réalisé automatiquement.

## Développement

```sh
python -m pip install '.[test]'
QT_QPA_PLATFORM=offscreen python -m pytest -q
python scripts/build_linux_bundle.py
```

Les tests couvrent calculs, persistance, conflits, sous-postes, Qt et MCP. Le démarrage graphique doit aussi être vérifié sur chaque système. Le premier jet exporte des fichiers JSON ; pas encore de devis PDF ni de gestion des encaissements. Les taux fiscaux sont renseignés par l'utilisateur.

## Journaux d’erreurs

Dans **Paramètres → Ouvrir les journaux d’erreurs**, retrouver le journal de chaque lancement. Ils sont conservés localement dans `~/.jhr-chiffrage/logs` (dossier utilisateur sous Windows également), y compris sans console. Les traces Python, messages Qt et traces de plantage natif disponibles y sont écrits ; les 20 sessions les plus récentes sont conservées. Après une fermeture inattendue, conserver le dernier fichier `desktop-*.log` pour le diagnostic.

Les affaires et les paramètres en cours de modification sont copiés automatiquement dans un fichier de secours local, avant actualisation du tableau. Les champs de la fenêtre de poste sont également conservés pendant la saisie. Après une fermeture inattendue, l’application propose de restaurer cette copie. Vérifier puis **Enregistrer** pour conserver et synchroniser le travail récupéré. Si l’affaire a changé ailleurs, la récupération crée une affaire distincte aux taux actuels pour préserver l’original. Annuler explicitement une saisie ou abandonner des modifications retire la copie correspondante. Cette protection ne remplace pas les sauvegardes de la base.

Dans **Paramètres → Enregistrement automatique des affaires**, activer indépendamment l’enregistrement après modification d’un poste/ouvrage et l’enregistrement après une pause dans la saisie (5 à 3 600 secondes). Le délai repart après chaque modification ; aucune écriture n’est déclenchée sans changement. La saisie d’un poste doit être validée avant l’enregistrement normal. En cas de conflit ou de valeur invalide, le brouillon et sa copie de secours sont conservés pour correction ; aucune version distante n’est écrasée. Ces réglages sont propres au PC et à la connexion utilisée.

Pour déplacer un poste et tous ses descendants, le glisser **entre deux lignes** (changer l’ordre), **sur une ligne** (devenir son sous-poste), ou **dans l’espace vide du tableau** (revenir au premier niveau, en fin de liste). Le repère bleu indique la destination. Déposer sur **l’onglet d’un autre ouvrage** transfère la branche complète dans cet ouvrage de la même affaire. Les montants propres sont conservés ; les totaux des parents se recalculent. Les déplacements suivent les réglages d’enregistrement automatique et bénéficient de la copie de secours. Les versions figées et les déplacements dans sa propre descendance sont protégés.

## Adapter un chiffrage au CCTP avec un agent

L’organisation prise en charge est **Affaire → Lots (onglets) → Ouvrages (postes principaux) → Vues/postes → Sous-postes**. Les données existantes restent compatibles : `works` désigne les onglets/lots et `items` les ouvrages et leurs descendants.

L’agent lit le CCTP avec ses propres outils documentaires, puis utilise :

1. `get_agent_workflow` et `get_estimate_outline` : conventions, arborescence, montants propres/cumulés et révision actuelle.
2. `search_reusable_ouvrages` : trouver un lot, un ouvrage ou une branche existante à réutiliser.
3. `prepare_estimate_changes` : préparer des ajouts, copies, adaptations, déplacements et suppressions sans enregistrer l’affaire. Le résultat contient un aperçu détaillé, les totaux avant/après et un `plan_id`.
4. `apply_estimate_plan` : appliquer le plan autorisé en une seule révision. Réappliquer le même plan ne duplique rien. Une révision périmée ou du travail humain non enregistré sur le même PC bloque l’application.
5. `synchronize`, puis `get_sync_status` : vérifier le partage effectif avec le serveur.

Les actions disponibles sont `add_lot`, `rename_lot`, `copy_lot`, `add_ouvrage`, `add_post`, `copy_ouvrage`, `update_post`, `move_post`, `remove_post` et `remove_lot`. Une suppression de poste inclut ses descendants et figure dans l’aperçu.

Une création peut recevoir une référence temporaire, par exemple `key: "@lot"` ou `key: "@chassis"`, utilisable dans les actions suivantes. Après une copie portant `key: "@chassis"`, `@chassis/IDENTIFIANT_DU_POSTE_SOURCE` désigne le poste copié correspondant. Les copies exigent l’identifiant et la révision de l’affaire source. `copy_ouvrage` sans `source.item_id` reprend un ancien onglet entier comme ouvrage principal dans le lot cible.

Exemple de préparation (identifiants et révision à remplacer par ceux lus via le MCP) :

```json
{
  "estimate_id": "IDENTIFIANT_AFFAIRE",
  "expected_revision": 1,
  "context": "Préparer le lot serrurerie d’après le CCTP fourni ; temps à vérifier.",
  "changes": [
    {"action": "add_lot", "key": "@lot", "name": "LOT Serrurerie"},
    {"action": "add_ouvrage", "key": "@ouvrage", "lot_id": "@lot", "name": "C1 — Châssis"},
    {"action": "add_post", "key": "@coupe", "lot_id": "@lot", "parent_id": "@ouvrage", "fields": {"label": "Coupe verticale", "duration_minutes": 20, "cctp_reference": "CCTP fourni, chapitre et page à préciser", "time_basis": "Hypothèse de temps de dessin à confirmer"}},
    {"action": "add_post", "lot_id": "@lot", "parent_id": "@coupe", "fields": {"label": "Cotation + labels", "duration_minutes": 10, "time_basis": "Hypothèse de cotation à confirmer"}}
  ]
}
```

Les références au CCTP, hypothèses de temps et origines de copie sont conservées sur les postes ; elles apparaissent au survol de la désignation dans l’application. Les taux de l’affaire cible sont conservés. La copie d’un temps n’établit pas sa pertinence pour le nouveau CCTP : l’agent doit justifier les adaptations, signaler les données manquantes et distinguer plans EXE/FAB et validation structure par un BE.

### Raccorder un agent

Choisir le transport **stdio** et le profil `JHR_MCP_ACCESS=draft` pour préparer et modifier des brouillons sans modifier les paramètres fiscaux ni figer les versions. La connexion au serveur métier est reprise depuis la configuration privée de l’application ; ne pas placer le jeton ni le certificat privé dans le dépôt.

Exemple générique pour les agents utilisant un fichier `mcpServers` :

```json
{
  "mcpServers": {
    "jhr_chiffrage": {
      "command": "CHEMIN_ABSOLU_VERS_JHRChiffrageMCP.exe",
      "env": {"JHR_MCP_ACCESS": "draft"}
    }
  }
}
```

Sur Ubuntu, remplacer la commande par le chemin absolu vers `~/.local/bin/jhr-chiffrage-mcp`. Pour Codex, la même commande et le même environnement se configurent dans la table `[mcp_servers.jhr_chiffrage]` de sa configuration locale. Relancer/recharger le client MCP après changement. L’agent doit avoir accès aux documents que vous souhaitez faire analyser ; le MCP de chiffrage ne parcourt pas les PDF lui-même.
