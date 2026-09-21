# JHR Chiffrage

Application de bureau native pour chiffrer les études et dessins : affaires, ouvrages par onglets, postes et sous-postes, gabarits, heures/minutes, taux horaire HT, TVA et prélèvements estimés. Interface Qt pour Windows et Ubuntu, sans navigateur.

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

## Serveur partagé (version 0.2)

Un PC peut conserver la base commune pour les applications Windows/Ubuntu et leur agent IA. Dans **Paramètres → Configurer la connexion**, choisir le serveur, l'adresse HTTPS, la clé d'accès et le certificat public. Tester puis relancer. Le serveur doit être disponible ; aucun basculement local silencieux n'est effectué en cas de coupure.

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
