# JHR Chiffrage sur Ubuntu

Le dépôt et l'archive contiennent l'application native Qt et un installateur par utilisateur.
Ils ne contiennent aucune affaire ni donnée personnelle. Internet est nécessaire lors
de l'installation pour télécharger les dépendances Python/Qt. L'application travaille
ensuite avec sa base locale, sans navigateur.

## Installation depuis GitHub

Avec Git disponible, ouvrez un terminal et lancez :

```bash
git clone https://github.com/JoLaFripouille/jhr-chiffrage.git
cd jhr-chiffrage
bash installer.sh
```

Si le dépôt est privé, GitHub demande un compte autorisé et une authentification
Git configurée sur ce PC. Après confirmation du succès, cherchez **JHR Chiffrage**
dans les applications, ou lancez `~/.local/bin/jhr-chiffrage`.

## Mise à jour depuis GitHub

Enregistrez votre travail puis fermez l'application et son agent MCP. Dans le
dossier `jhr-chiffrage` cloné lors de l'installation, lancez simplement :

```bash
bash mettre-a-jour.sh
```

La commande vérifie le dépôt, la branche `main` et l'absence de modifications
locales des fichiers suivis, puis télécharge et installe la dernière version.
En cas de conflit Git, elle s'arrête sans effacer vos modifications. Si le
téléchargement ou la vérification des dépendances échoue, les lanceurs de
l'installation précédente restent utilisables. Rouvrez l'application après le succès.
La mise à jour du logiciel ne transfère pas les affaires entre les deux PC.

## Installation depuis une archive ZIP

1. Copiez le ZIP sur le PC Ubuntu puis extrayez-le entièrement.
2. Ouvrez un terminal dans le dossier extrait (celui contenant `installer.sh`).
3. Lancez `bash installer.sh`.
4. Après confirmation du succès, cherchez **JHR Chiffrage** dans les applications.
   Si le menu ne s'est pas rafraîchi, lancez `~/.local/bin/jhr-chiffrage`.

L'installateur ne demande pas sudo et n'installe aucun paquet système. Il crée un
environnement Python privé dans `~/.local/share/jhr-chiffrage/versions/`, puis les
lanceurs dans `~/.local/bin/`. Une nouvelle installation conserve les anciennes
versions et actualise les lanceurs seulement après vérification des dépendances Qt.

## Prérequis et erreurs possibles

Python **3.11 minimum** est obligatoire. Ubuntu **24.04** fournit Python 3.12.
Le Python 3.10 fourni par défaut dans Ubuntu 22.04 est trop ancien : utiliser un
Python compatible déjà installé, par exemple `JHR_PYTHON=python3.12 bash installer.sh`,
ou préparer d'abord une version Ubuntu/Python compatible.

Le module `venv` doit être disponible. Sur Ubuntu 24.04, le paquet système concerné
est généralement `python3-venv`. Les dépendances Qt courantes comprennent
`libxcb-cursor0`, `libxkbcommon-x11-0`, `libegl1` et `libgl1`.
Si elles manquent, l'installateur affiche les bibliothèques concernées et s'arrête.
Faites installer les paquets manquants via le gestionnaire de paquets Ubuntu,
puis relancez `bash installer.sh`. Leur installation n'est jamais automatique.
Un bureau graphique et une architecture pour laquelle les dépendances publient
des paquets Python sont nécessaires. Ce ZIP n'est pas un exécutable autonome.

## Retrouver ses affaires du PC Windows

La base locale se trouve dans `~/.local/share/JHRChiffrage/chiffrage.sqlite3` sur
Ubuntu (ou sous `$XDG_DATA_HOME/JHRChiffrage` si cette variable est configurée).
**Ne placez pas une base ouverte dans OneDrive.** Pour transporter une sauvegarde,
utilisez une sauvegarde cohérente, puis fermez l'application
et de l'agent MCP, transfert, conservation d'une copie de la base remplacée.
La synchronisation n'est pas automatique. Ne travaillez pas en parallèle sur deux
copies sans les avoir réconciliées. L'installation ne modifie pas cette base.

Le lanceur MCP est `~/.local/bin/jhr-chiffrage-mcp` ; sa configuration est décrite
dans le README principal. Il n'est pas ajouté automatiquement à votre agent IA.

## Validation

La syntaxe de l'installateur est vérifiée sous Ubuntu 24.04 (WSL).
Le fonctionnement graphique sur votre PC Ubuntu doit encore être vérifié après
installation ; la vérification de syntaxe n'est pas une validation native Qt.
