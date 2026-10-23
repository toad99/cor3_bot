# cor3_bot : contexte pour Claude

Bot Python + Playwright qui fait la routine quotidienne du joueur sur **cor3.gg** (ARG « Fragmentary Order ») : daily,
jobs des 4 marchés, minuteurs des marchés, mercenaires. Il tourne sur son PC Windows, dans un **profil Chrome
dédié**, lancé par le Planificateur de tâches (02:15 heure de Paris + passage `--auto` tous les quarts d'heure).
Le joueur indique que les CM du Discord officiel autorisent l'automatisation. Tout le projet est en français
(code, journaux, docs) : garder cette langue.

## À lire avant de toucher au code

1. `docs/INSPECTION.md` : tout ce qu'on sait de l'interface (sélecteurs, WebSocket, enchaînements, pièges,
   ce qui est vérifié en direct ou non). C'est la mémoire du projet : la mettre à jour à chaque découverte.
2. `docs/METHODE.md` : comment inspecter le site (snippets, `tools/`) et faire évoluer le bot sans risque.
3. `README.md` : installation, configuration et planification côté joueur.

## Règles non négociables

- **Aucune action irréversible sans accord explicite du joueur dans la conversation** : achat, suppression
  (fichier, log, IP), dépôt, engagement/paiement de mercenaire. Dans le code : désactivé par défaut, activable
  dans `config.toml`, et toujours via `Bot.allowed()` (point d'arrêt du `--dry-run`).
- **Jamais d'identifiants ni de jetons** : le joueur se connecte lui-même (`--login`) ; ne jamais lire, afficher
  ou journaliser `cor3-rkey` / `cor3-tkey` (JWT du localStorage) ni un en-tête d'authentification.
- Jamais de « force hack » (payant), jamais de CAPTCHA.
- Jouer **par l'interface** : ne pas exploiter la validation côté client de la daily (POST sans corps), ne pas
  lire l'état caché Colyseus pour ICE WALL (résoudre avec les glyphes visibles).
- **Une seule session par compte** : ouvrir cor3.gg ailleurs déconnecte le joueur. En inspection en direct,
  lui demander d'abord.
- Le mode `--auto` n'engage jamais de mercenaire ; il ne prend pas la session si le PC est utilisé.

## Carte du code

- `cor3_bot.py`
  - configuration, journal, alertes (`Notifier` : toast Windows, Discord, ntfy) ;
  - `State` (`state.json` : daily faite, minuteur de chaque marché, jobs signés à reprendre, expédition en cours) ;
  - `GameEvents` : écoute du WebSocket socket.io/msgpack et de l'API daily, état des serveurs de la carte ;
  - `Bot`, en quatre parties : `run_daily`, `run_jobs` / `run_markets` (carte, 4 marchés, maintenance, SAI,
    piratage, ICE WALL, Loadout), `run_mercs`, `run_drone` (drone personnel, courrier du stash) ;
  - `main()` : options, verrou, mode `--auto`, historique `logs/history.jsonl`.
- `solvers.py` : logique pure et testée (lignes de logs, Morse/binaire, ICE WALL avec déduction).
- `tests/` : unitaires + Playwright contre la maquette `tests/mock_game/index.html`
  (faux serveur WebSocket qui renvoie en msgpack ce que la page envoie en JSON).
- `tools/ws_tap.js` (console du navigateur) et `tools/ws_dump.py` (enregistreur Playwright) : inspection.
- `install_task.ps1`, `run_cor3.bat` : Planificateur de tâches Windows (UTF-8 avec BOM, fins de ligne CRLF).

## Commandes

```bash
python -m pytest tests -q                 # tous les tests (≈ 5 min, Chromium headless)
python -m pyflakes cor3_bot.py solvers.py tests tools
python cor3_bot.py --dry-run              # sur le PC du joueur : tout lire et résoudre, ne rien valider
python cor3_bot.py --only jobs --dry-run
python cor3_bot.py --auto                 # ce que fait la tâche du quart d'heure
```

## Conventions

- Sélecteurs stables seulement (`data-onboarding-300-id`, `data-sentry-component/element`,
  `data-component-name`, textes FR | EN de `TXT`, classes en clair des mini-jeux) ; jamais les classes `go…`.
- Attendre la réponse du serveur (`mark = events.mark()` puis `wait_event(salle, action, mark)`), pas de
  pause fixe quand une trame existe.
- `Unavailable` = maintenance / route coupée / serveur absent : note dans le résumé, on réessaie
  (`state.json`). `StepFailed` = vrai problème : alerte. `Simulated` = arrêt propre du `--dry-run`.
- Chaque nouvel écran : un morceau de maquette + un test, et une mise à jour de `docs/INSPECTION.md`.

## État au 23/10/2026 (frontend v1.25.71, version testée)

- Vérifié en direct : Signal Decode, System Log Integrity, ICE WALL (déchiffrement), accès par tâche, Log
  Download, signature et fin de job, Loadout/préréglages, ouverture des 4 marchés et leurs minuteurs,
  piratage sans mini-jeu, accès piraté réutilisé, mercenaire (engagement, événement, conteneur, butin).
- Codé et testé sur la maquette seulement : boucle sur les 4 marchés, `state.json` et `--auto`,
  repiratage après maintenance jusqu'à l'ouverture du terminal, refus de signer si le serveur est absent de la
  carte, en maintenance, sans « Connecter » possible (`canSetEndpoint`) ou sans accès (`access_problem` :
  Grant, ou accès déjà acquis ; sinon `sign_without_access`), reprise des jobs signés non terminés
  (`pending_jobs`). Champs relevés en v1.25.71 par `ws_dump` (voir `docs/INSPECTION.md` § 3, 7, 8).
- Jamais vu en direct : suppression (`SaiDeleteModal`), ajout d'IP (`SaiAddIpModal`), fin d'un piratage
  par ICE WALL (`hack.finished`), champ exact « Grant / Acquisition » des jobs.
- Limite connue : ICE WALL en piratage laisse ~90 s quand la défense égale la puissance (un essai perdu
  au temps sur RM7-W3L1).
- v1.25.71 vue le soir du 21/10 (dry-run du joueur, passage complet OK après correction) : la carte du Game Center
  est devenue un `<button>`, et l'état des salles n'est envoyé qu'à la connexion (`join-room`), dont
  `expeditions:get.active`. Les deux sont gérés. Voir `docs/INSPECTION.md` (§ 3, 4, 11).
- 22/10 à 02:15 (premier vrai passage en v1.25.71) : Signal Decode réussi. Recliquer l'onglet Contrats déjà ouvert
  ne recharge plus la liste ; après une signature, le jeu la recharge de lui-même (§ 6). Corrigé après un Log
  Download signé puis abandonné. Les journaux SAI sont devenus un tableau, boutons dans une cellule à part (§ 8).
  Log Download repris et terminé par le bot à 02:49 (+1 445 CR, +261 REP).
- Stratégie des jobs (accord du joueur le 22/10) : tout ce qui est faisable et rapporte, dépôts, amendes,
  suppressions et IP Injection compris (config du joueur). Ordre sûrs → déchiffrements → piratages, fusible par
  type et par passage, ICE WALL bloqué = arrêt des mini-jeux, retour sur le marché au passage suivant
  (`run_jobs`, `tests/test_strategy.py`).
- 23/10 : `TESTED_FRONTEND` = v1.25.71. Vérifié en direct dans cette version : Signal Decode et System Log
  Integrity, les 9 types de jobs (Log/Data Download, File Decryption, Decrypt & Extract, Data Upload, Log Deletion,
  File Elimination, IP Cleanup, IP Injection), ICE WALL de déchiffrement. Jobs perdus d'avance détectés
  (`Hopeless` : fichier trop chiffré, serveur trop défendu). `--bilan` : gains par jour.
- Objectif du joueur (23/10) : le rang **Pillar** (quête Wings of Mars et événement Echoes of Planetside faits,
  Renommée 20, total de crédits gagnés). **La Renommée compte plus que les CR** : chaque job terminé rapporte
  3 points, quel que soit son gain (relevé sur le bandeau `LevelIndicator`, § 2). Viser le nombre de jobs réussis.
  Le bot lit la Renommée à chaque passage (`note_renown`, `--bilan`), et `--auto` tourne tous les quarts d'heure.
- Drone personnel (23/10) : missions gratuites (batterie), butin au courrier, **aucune Renommée** (succès partiel
  ou complet). Le joueur veut les objets : `run_drone` part dès que possible, mode gourmand, ne répare jamais.
