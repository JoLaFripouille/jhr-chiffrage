# Serveur local JHR Chiffrage

Mode prévu pour un petit réseau local de confiance. L'application reste native. Le serveur conserve la base SQLite sur son propre disque et expose les opérations métier via HTTPS. Ne pas partager directement le fichier SQLite sur un lecteur réseau.

## Préparer un hôte

Installer le projet Python et ses dépendances sur le PC serveur. Générer une configuration privée, hors du dépôt et hors des dossiers partagés publiquement :

```sh
python scripts/setup_server.py --host ADRESSE_IP_LOCALE --port 8765 --directory DOSSIER_PRIVE --database CHEMIN_ABSOLU_BASE
python -m jhr_chiffrage.server --config DOSSIER_PRIVE/server.json
```

La clé d'accès et la clé TLS privée ne doivent jamais être envoyées sur GitHub. Le certificat public `server.pem` peut être copié sur les ordinateurs clients. Le fichier de connexion contenant le jeton doit être transporté de façon privée. Le serveur refuse de fonctionner sans HTTPS ; les clients vérifient le certificat et le nom/adresse du serveur.

Créer une sauvegarde avant la première mise en service. Le serveur réalise ensuite une sauvegarde SQLite cohérente au démarrage et toutes les 24 heures de fonctionnement. Les sauvegardes restent sur l'hôte ; prévoir une copie externe pour protéger contre une panne de disque. Elles ne sont pas supprimées automatiquement.

## Connecter une application

Dans **Paramètres → Configurer la connexion**, sélectionner **Serveur partagé**, saisir l'URL HTTPS et la clé d'accès, puis choisir le certificat public copié localement. Tester la connexion, enregistrer, enregistrer les travaux en cours et relancer l'application. Le basculement ne fusionne ni ne copie les anciennes affaires locales.

L'application et le MCP local utilisent la même configuration de connexion. Pour un processus spécifique, `JHR_SERVER_URL`, `JHR_SERVER_TOKEN` et `JHR_SERVER_CA` définissent un profil serveur indépendant. `JHR_CHIFFRAGE_DB` force une base locale si aucun profil serveur n'est défini dans l'environnement.

En cas de coupure, il n'y a pas de repli silencieux sur une autre base. Les saisies restent dans l'application. Si un enregistrement a été interrompu, recharger pour vérifier s'il a été reçu avant de le refaire. Les conflits de révision empêchent d'écraser les changements d'un autre client.

## Réseau et disponibilité

Autoriser le port choisi dans le pare-feu, uniquement depuis le sous-réseau local et sur l'interface concernée. Ne pas ouvrir ce port sur la box vers Internet. Réserver l'adresse locale du serveur dans le routeur pour éviter qu'elle change ; sinon l'URL et le certificat devront être adaptés.

Le PC hôte doit rester allumé, connecté et hors veille. Un démarrage avec la session utilisateur exige l'ouverture de cette session. Le premier jet n'est pas un service système installé avant connexion. Pour un accès extérieur ultérieur, prévoir un VPN ou une architecture d'hébergement adaptée.

La clé actuelle donne les droits complets aux clients autorisés. Il n'y a pas encore de comptes individuels ni de droits par personne. Pour révoquer un client, changer la clé serveur et la mettre à jour sur les clients conservés. Les profils MCP continuent de limiter les opérations exposées à l'agent.
