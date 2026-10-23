# cor3_bot — automatisation quotidienne de cor3.gg

Script Python + Playwright qui ouvre cor3.gg dans un **profil Chrome dédié**, fait la daily, relance les
minuteurs des 4 marchés, fait les jobs et suit les expéditions de mercenaires, puis écrit un journal et
t'alerte en cas d'échec. Il joue **par l'interface** (clics, lecture de la page et du WebSocket du jeu),
sans appeler l'API à ta place.

Testé contre cor3.gg frontend **v1.25.71** (22–23/10/2026 : daily, les 9 types de jobs, ICE WALL). Si le jeu change de version, le journal le signale.
Ce que le bot sait de l'interface : `docs/INSPECTION.md` ; comment l'inspecter à nouveau : `docs/METHODE.md`.

## Ce que fait chaque partie

| Partie | Ce qui est automatisé | Par défaut |
|---|---|---|
| `daily` | Signal Decode (impulsions lues dans la page, Morse/binaire) et System Log Integrity (analyse des lignes, corrections exactes) | activée |
| `jobs` | Sur **chacun des 4 marchés** (Home Server, D4RK RM7MI, SRM7-M, URM7-M) : ouvre les Contrats (ce qui relance le minuteur), termine les jobs déjà signés, signe et fait les nouveaux : déchiffrement (ICE WALL, avec ton préréglage), Log Download, File Download | activée, sans dépôt |
| `markets` | Relance seulement les minuteurs échus (utile si `jobs` est désactivée) | activée |
| Serveurs | Connexion par l'accès « par tâche », sinon par ton outil de piratage : **repiratage automatique après une maintenance** (direct ou ICE WALL) ; serveurs en maintenance ou injoignables évités et retentés plus tard | activé (`allow_hack`) |
| Jobs destructifs | IP Cleanup, Log Deletion, File Elimination, IP Injection | **désactivés** (`allow_deletions`, `allow_ip_add`) |
| `mercs` | Suivi d'une expédition en cours (jusqu'à `watch_minutes`), réponse aux événements (prudent / gourmand / auto) | activé (ne dépense rien) |
| Dépenses | Engager un mercenaire, payer le solde au retour ; « force hack » : jamais | **désactivées** (`hire`, `pay_on_return`) |

Chaque action qui valide quelque chose passe par un point de contrôle : en `--dry-run`, le script s'arrête
juste avant et l'écrit dans le journal.

### Les 4 marchés et leurs minuteurs

Chaque marché a ses contrats et son propre minuteur de renouvellement. Une fois échu, il ne repart que si
on rouvre l'onglet Contrats de ce marché : le bot le fait à chaque passage complet, et la tâche horaire
`--auto` le fait dès qu'un minuteur est échu. Les échéances sont gardées dans `state.json`.
Les marchés distants demandent de « Connecter » le serveur : le bot remet ensuite ton point de sortie d'origine.
Un marché en maintenance est retenté à la fin de la maintenance (minuteur rouge de la carte).

### Maintenance et repiratage

Avant de signer un job, le bot vérifie sur la carte que son serveur y figure, n'est pas en maintenance et
qu'on peut s'y rendre (« Connecter »), puis qu'il pourra s'y connecter : contrat « Access Grant » (l'accès
vient avec le job) ou accès déjà acquis. Un contrat « Access Acquisition » sur un serveur jamais piraté est
refusé, sauf si tu mets `sign_without_access = true` (il faudra alors le pirater, ICE WALL compris).
Pour prendre tout ce qui rapporte : `max_deposit_cr = 600`, `max_rep_penalty = 300`, `max_jobs_per_run = 20`,
les suppressions et l'IP Injection autorisées. Sur chaque marché, le bot fait d'abord les jobs sans mini-jeu,
puis les déchiffrements, puis ceux qui demandent un piratage. **Fusible** : un type de job qui échoue après
signature n'est plus signé pendant ce passage (au pire un dépôt par type en jeu si une mise à jour du jeu casse
un enchaînement) ; un ICE WALL bloqué (20 min, ou limite quotidienne) arrête déchiffrements et piratages.
Ce qui reste faisable est repris au passage horaire suivant, avant le renouvellement de la liste.
Un job déjà signé qu'on ne peut pas finir tout de suite (maintenance, échec) est noté dans `state.json` : la
tâche horaire revient sur son marché à la fin de la maintenance, ou au passage suivant après un échec, sans
attendre le minuteur. Après une maintenance, l'accès piraté est perdu : le bot
remet le préréglage de piratage adapté (`hack_presets`), clique ton outil le plus puissant, et si le jeu
lance ICE WALL, le résout. Un ICE WALL raté verrouille le mini-jeu 20 minutes (déchiffrement compris).

## Installation (Windows, une seule fois)

1. Installe Python 3.11 ou plus récent (cocher « Add python.exe to PATH ») et Google Chrome.
2. Dézippe ce dossier, par exemple dans `C:\cor3_bot`, puis dans un terminal :
   ```bat
   cd C:\cor3_bot
   py -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   copy config.example.toml config.toml
   ```
   Pas besoin de `playwright install` : le script utilise ton Chrome (`channel = "chrome"`).
   Pour utiliser le Chromium de Playwright à la place : `channel = ""` puis `playwright install chromium`.
3. Ouvre `config.toml` et vérifie au minimum `profile_dir`, la liste `[markets] names` (retire un marché pas
   encore débloqué), les noms de tes préréglages Loadout (`decrypt_presets`, `hack_presets`, `restore_preset`)
   et ce que tu autorises. **Si tu avais déjà un `config.toml`**, recopie-y les nouvelles sections
   `[markets]`, `[auto]` et les lignes `allow_hack`, `hack_presets`, `sign_without_access`, `hire_market`.
4. Connecte le profil dédié (tu tapes toi-même tes identifiants, le script ne les voit jamais) :
   ```bat
   python cor3_bot.py --login
   ```
   Connecte-toi à cor3.gg dans la fenêtre qui s'ouvre, puis ferme-la.
5. Fais un essai à blanc, fenêtre visible :
   ```bat
   python cor3_bot.py --dry-run
   ```
   puis un vrai passage : `python cor3_bot.py --only daily`, et `python cor3_bot.py --only markets`.
6. Lance les tests si tu modifies le code : `python -m pytest tests -q`
   (solveurs, règles, et tests Playwright contre une maquette locale du jeu).

## Planificateur de tâches

Dans PowerShell, depuis le dossier du bot :
```powershell
powershell -ExecutionPolicy Bypass -File .\install_task.ps1
Start-ScheduledTask -TaskName cor3_bot      # test immédiat
```
Deux tâches sont créées :

| Tâche | Quand | Ce qu'elle fait |
|---|---|---|
| `cor3_bot` | tous les jours à 02:15 (heure de Windows), réveille le PC, rattrape l'heure manquée | passage complet : daily, 4 marchés + jobs, minuteurs, mercenaires |
| `cor3_bot_auto` | tous les quarts d'heure (hh:05, 20, 35, 50) | ne lance Chrome que si quelque chose est dû : daily pas faite, minuteur de marché échu, expédition en cours ; ne fait rien si tu as utilisé le PC dans les 10 dernières minutes (pour ne pas te voler la session) |

Chaque minuteur de marché ne repart que quand le bot ouvre le marché : le prendre dans le quart d'heure
au lieu de l'heure laisse passer plus de listes de contrats, donc plus de jobs (et de Renommée).
Après un `git pull` qui change `install_task.ps1`, relance-le pour mettre la tâche à jour.

Sans la tâche `--auto` : `.\install_task.ps1 -NoAuto`. Réglages de `--auto` dans `[auto]` (heures autorisées,
inactivité, alerte si la daily n'est pas faite 3 h avant le reset).

À la main (taskschd.msc → Créer une tâche), pour `cor3_bot` :
- Général : « N'exécuter que si l'utilisateur est connecté » (nécessaire pour Chrome et les notifications).
- Déclencheurs : Quotidien, 02:15:00 (pour `cor3_bot_auto` : Quotidien 00:05, répéter toutes les 15 minutes pendant 23 h 46).
- Actions : Démarrer un programme → `C:\cor3_bot\run_cor3.bat` (argument `--auto` pour `cor3_bot_auto`),
  « Commencer dans » → `C:\cor3_bot`.
- Conditions : cocher « Sortir l'ordinateur du mode veille pour exécuter cette tâche ».
- Paramètres : « Exécuter la tâche dès que possible si un démarrage planifié est manqué »,
  « Arrêter la tâche si elle s'exécute plus de 2 heures », « Ne pas démarrer une nouvelle instance ».

Pour que le réveil fonctionne : Options d'alimentation → Modifier les paramètres avancés →
Veille → « Autoriser les minuteurs de réveil » = Activer (et sur secteur pour un portable).

Repères horaires : la daily se réinitialise à 00:00 UTC, soit 02:00 à Paris en heure d'été et 01:00 en
heure d'hiver. À 2 h 15 la nouvelle daily est donc toujours disponible. La nuit du passage à l'heure
d'été (2 h 15 n'existe pas), la tâche part à 3 h grâce à « dès que possible ».

## Alertes et journal

- Journal du jour : `logs\cor3_AAAA-MM-JJ.log` ; historique d'une ligne par exécution : `logs\history.jsonl`.
- **Gains par jour** : `python cor3_bot.py --bilan` (sans ouvrir Chrome) affiche les jobs terminés, CR, REP et
  Renommée d'aujourd'hui, d'hier et des 7 derniers jours, les jobs perdus, la répartition par type, le dernier
  solde vu, d'où vient la Renommée (jobs, daily, mercs, ou « hors du bot » entre deux passages) et le temps
  estimé jusqu'au niveau 20 (rang Pillar). La Renommée est lue sur le bandeau du bureau à l'ouverture et après
  chaque partie (`Renommée : niveau 17, 107/900` dans le journal).
- État entre deux passages : `state.json` (daily faite, échéance de chaque marché, jobs signés à reprendre,
  expédition en cours).
  Le supprimer ne casse rien : le prochain passage revisite tout.
- En cas d'échec : capture d'écran + HTML dans `logs\debug\` (utile si le jeu change).
- Code de sortie visible dans le Planificateur (« Résultat de la dernière exécution ») :
  `0` OK, `1` une partie a échoué, `2` connexion/session/navigateur.
- Alerte « la daily a échoué » : notification Windows, et si tu les renseignes, un webhook Discord
  et/ou un sujet ntfy.sh (pratique pour la recevoir sur ton téléphone pendant la nuit).
  Test : `python cor3_bot.py --test-notify`.
- Un marché en maintenance n'est pas un échec : il apparaît en note dans le résumé et sera retenté.

## Points d'attention

- **Une seule session à la fois** : cor3.gg déconnecte l'onglet précédent quand un autre se connecte.
  Ne laisse pas cor3.gg ouvert ailleurs à 2 h 15 ; si ta session reprend la main pendant que le bot tourne,
  il s'arrête proprement et t'alerte au lieu de se battre avec toi. La tâche horaire, elle, attend que tu
  n'utilises plus le PC.
- **ICE WALL Break** : un seul clic par motif, sur la case « ancre ». Un échec verrouille le mini-jeu
  20 minutes et le jeu prévoit une limite quotidienne. En piratage, le temps est court (~90 s quand la défense
  égale la puissance de l'outil) : un essai a été perdu au temps pendant les tests. Si un serveur échoue
  souvent, ajoute-le à `skip_servers` ou mets un préréglage plus puissant dans `hack_presets`.
- **Préréglages de piratage** : le journal écrit le type de chaque serveur piraté (`serverTypeName`) ; sers-t'en
  pour compléter `hack_presets` (par exemple pour ton préréglage « hack t4 »).
- **Écrans jamais testés en direct** : suppression et ajout d'IP (désactivés par défaut), fin d'un piratage par
  ICE WALL. Le premier jour où ça tombe, lance d'abord `python cor3_bot.py --dry-run --only jobs`.
- **Mercenaires** : un raid de Crimson (Skylift Remains) a coûté 5 466 CR pour un butin d'environ 2 100 CR
  (valeurs du guide) + réputation. C'est pour ça que l'engagement est désactivé par défaut, et jamais fait
  par la tâche horaire.

## Versionner le projet avec git

Le dépôt est déjà initialisé (dossier `.git`). `.gitignore` exclut `config.toml`, `chrome-profile/`, `logs/`,
`state.json` et `.venv/` : ta session de jeu, tes réglages et tes journaux ne partent jamais en ligne.
Pour le mettre sur GitHub en privé :
1. Crée un dépôt **privé** vide sur github.com (sans README).
2. Dans le dossier du bot :
   ```bat
   git remote add origin https://github.com/<ton-pseudo>/cor3_bot.git
   git push -u origin main
   ```
   (ou ouvre le dossier dans GitHub Desktop : « Add an existing repository », puis « Publish repository » en privé).
3. Ensuite, après chaque modification : `git add -A`, `git commit -m "…"`, `git push`.

`CLAUDE.md` et `docs/` résument tout ce qui a été découvert : une nouvelle discussion avec Claude (Claude Code
ou Cowork, dossier du projet ouvert) les lit et peut reprendre le travail.
