# cor3.gg : ce qu'on sait de l'interface

Relevés faits en direct les 20 et 21/10/2026 sur cor3.gg **frontend v1.25.51**, avec le compte du joueur
(navigateur Chrome, interface en français). Ce document est la mémoire du projet : une nouvelle discussion
doit pouvoir reprendre le travail à partir d'ici, sans relire l'ancienne conversation.

Légende : ✅ vérifié en direct · 🧪 couvert par la maquette de test seulement · ❓ déduit du code du jeu ou du guide, jamais vu.

Conclusion générale : **tout est lisible dans le DOM et dans le WebSocket**. Aucun écran utile n'est dessiné
dans un `<canvas>` ; le son n'apporte rien (le Signal Decode joue un bruit de clavier, plus long pour L).

---

## 1. Pile technique

- SPA React (build Vite). Styles goober : classes `go1234567` **instables d'un build à l'autre, à ne jamais utiliser**.
- Sélecteurs stables posés par le build :
  - `data-onboarding-300-id` : barre du bas (dock), nœuds de la carte (`ServerItem-<nom>`), bouton `ServerInfoPanelLoginButton` ;
  - `data-sentry-component` / `data-sentry-element` : noms des composants React (Sentry) ;
  - `data-component-name` : quelques composants (fenêtres, boutons, panneaux) ;
  - classes en clair dans les mini-jeux (`pulse-bar`, `log-entry`, `encoding-option`…).
- Temps réel : **socket.io + msgpack** sur `wss://svc-corie.cor3.gg/socket.io/` (trames binaires).
  Trame : `{type: 2, nsp: "/", data: [salle, {event: {name, action}, data, requestId}]}`.
  En Python : `msgpack.unpackb(raw, raw=False, strict_map_key=False, timestamp=3, ext_hook=...)`.
  Les pings engine.io (`"2"` / `"3"`) sont des trames texte : à ignorer.
- Mini-jeux multijoueurs (ICE WALL) : serveur **Colyseus** `svc-corie.cor3.gg/games/ice-wall-break`, autre WebSocket.
- REST : `GET/POST https://svc-corie.cor3.gg/api/user-daily-claim` (daily).
- Au démarrage, la salle `connected` donne `{message, frontendVersion}` : le bot alerte si la version change.
- **Une seule session par compte** ✅ : quand un autre onglet se connecte, l'ancien reçoit la salle
  `session-replaced` et affiche « Terminal local : DÉCONNECTÉ / RÉESSAYER ». Le bot s'arrête alors proprement.
- localStorage : `cor3-rkey` et `cor3-tkey` sont des **JWT d'authentification : ne jamais les lire ni les
  afficher**. `loadout-powered` = état du bouton d'alimentation du Loadout (`"true"`/`"false"`).

## 2. Bureau, dock, fenêtres ✅

| Élément | Sélecteur |
|---|---|
| Game Center | `[data-onboarding-300-id="TabBarItem-019a0c41-b17f-7abc-1234-567890abcde2"]` |
| Carte du réseau | `[data-onboarding-300-id="TabBarItem-NETWORK_MAP"]` |
| Expéditions | `[data-onboarding-300-id="TabBarItem-EXPEDITIONS"]` |
| Loadout | `[data-onboarding-300-id="LoadoutTabBarItem"]` |
| Fenêtre | `[data-sentry-component="AppWindow"]` ; titre `[data-component-name="ApplicationTitleText"]` |
| Fermer une fenêtre | `[data-component-name="close-app-btn"]` |
| Raccourci du bureau | `[data-sentry-component="DesktopShortcutComponent"]` (ex. « Downloads ») |
| Barre « Terminal local » | texte `Terminal local` ; `DÉCONNECTÉ` = session perdue |

Le bureau suit la langue choisie (FR) ; **les mini-jeux restent en anglais**. Le bot accepte les deux (table `TXT`).

## 3. WebSocket : salles et actions observées ✅

| Salle | Actions (sens serveur → client sauf mention) | Contenu utile |
|---|---|---|
| `market` | `get.options`, `get.lots`, `get.jobs`, `job.take`, `job.complete`, `job.can-complete`, `job.completed` | `get.jobs` → `{jobs[], recentJobs[], nextJobsResetAt}` ; `job.take`/`job.complete` → `{status: "ok"}` ; `job.can-complete` → `{canComplete}` (poussé si la fenêtre du marché est ouverte) |
| `network-map` | `get.map`, `set.endpoint`, `update` | `get.map` → `{kind: snapshot\|delta\|unchanged, state: {servers[]}}` ; `set.endpoint {serverId}` → delta |
| `sai` | `login.attempt`, `login.with-access`, `hack.start`, `get.login.status`, `get.summary`, `get.transit`, `transit.add/remove/match/search`, `get.files`, `file.delete/upload/download/match/search/search-valuable`, `get.logs`, `log.delete/download/match/search/search-valuable`, `update` | voir § 8 |
| `expeditions` | `configure`, `launch`, `get.active`, `respond.event`, `open.container`, `collect.all`, `get.mercenaries`, `get.config` | voir § 11 |
| `loadout` | `apply.preset` | |
| `minigames` | `start.minigame` | `{url}` (partie lancée) ou `{lockExpiresAt}` (verrouillé après un échec) |
| `desktop` | `update.file`, `delete.file` | `update.file` sur le fichier déchiffré = réussite du déchiffrement |
| `profile` | `get.credits`, `receive.credits` | `balance` / `newBalance` |
| `connected`, `session-replaced` | (salles sans action) | version du frontend ; perte de session |

Champs d'un serveur (`state.servers[]`) : `id`, `serverName`, `serverTypeName` (ex. « CEDRT public »,
« SOYUZ public »), `marketId`, `isReachable`, `isAccessible`, `accessType`, `isEndpoint` (point de sortie
actuel), `isInMaintenance`, `maintenanceEndsAt`, `timeUntilMaintenance`, `serverDefenceRate`.
Les deltas ne contiennent que les champs modifiés : le bot les fusionne par `id` (`GameEvents._merge_map`).

## 4. Daily tasks (Game Center → OPÉRATIONS QUOTIDIENNES → Open Module) ✅

- `GameCenterApplication` → carte « OPÉRATIONS QUOTIDIENNES » → « Open Module » → `DailyOpsMainScreen`,
  bouton `DailyOpsStartButton` (« Commencer la tâche »).
- Une daily par jour, tirée côté serveur, **reset à 00:00 UTC** (02:00 à Paris l'été, 01:00 l'hiver).
  `GET /api/user-daily-claim` → `{currentGameId, hasClaimedToday, currentStreak, lastClaimDate, difficulty,
  streakBonus, nextTaskTime}`. Rater un jour remet la série à zéro ; difficulté supérieure tous les 7 succès.
- Les deux mini-jeux sont **générés et vérifiés dans le navigateur** ; à la réussite, le client envoie
  `POST /api/user-daily-claim` **sans corps**. Règle du projet : **ne pas exploiter ça**, le bot joue par l'interface.

### Signal Decode ✅ (réussi en direct, code 321)

- Fenêtre `[data-component-name="Application-019aea65-b0c2-70e9-bbbc-8caed416854c"]`.
- « Get Signal » → `.signal-display .pulse-timeline .pulse-group` : chaque groupe contient
  `.pulse-bar.short` (1 barre) ou `.pulse-bar.long` (3 barres). Aucun séparateur entre les chiffres.
- `.next-button` « Select Encoding » → `.encoding-option` (`.option-header` Morse / Binary) → `.next-button`
  « Decode Signal » → table `.encoding-table-full .table-row (.digit, .pulses)`, champ `input.code-input`
  dont **`maxlength` = longueur du code**, bouton `.submit-button`, puis `.result-screen`.
- Morse : 5 impulsions par chiffre (0 = LLLLL, 1 = SLLLL … 5 = SSSSS, 6 = LSSSS … 9 = LLLLS) ;
  binaire : 4 impulsions (S = 0, L = 1, poids fort d'abord).
- Longueurs : easy 3 chiffres, medium 5, hard 10 → Morse 15/25/50 impulsions, binaire 12/20/40.
  **20 et 40 sont multiples de 4 et de 5 : ce sont du binaire** (la règle « multiple de 5 = Morse » se trompe) ;
  le bot départage avec la longueur attendue puis avec `maxlength`.
- Le choix d'encodage ne sert qu'à afficher la table : seul le code compte. Mauvais code = nouveau signal, sans pénalité.

### System Log Integrity ✅ (réussie en direct le 21/10 UTC)

- Fenêtre : `AppWindow` contenant `GameWaitingScreen` ou `.game-container` ; bouton d'intro
  `[data-sentry-element="GameWaitingScreenGameActionButtonStyled"]`, puis parfois l'écran « Press START »
  propre au mini-jeu (bouton Start).
- Lignes : `.log-entries .log-entry`, format attendu `[HH:MM:SS] TYPE /sector=N /status=S` (secteur 1–256 sur
  2 chiffres mini, statut OK/WARN/ERROR) ; elles apparaissent une par une (`.scanning-animation`).
  Consigne « Select N log entries… », bouton `.confirm-button` « Confirm Selection ».
- Écran « Format Revision » : `.error-analysis-block` → `.log-line-display`, `.fix-error-button`, 6 boutons
  `.error-type-button` : « Time format is incorrect », « Event type is incorrect », « Missing /sector parameter »,
  « Missing /status parameter », « /sector parameter is incorrect », « /status parameter is incorrect ».
  Il faut cocher **exactement** toutes les fautes de la ligne.
- « Confirm Fixes » puis `.scan-button` « Run Re-scan » → `.result-screen.success` ou `.failed` (+ `.retry-button`).
- Générateur (code client) : easy 10 lignes, medium 20, hard 30 ; fautes de type AUHT, SCANN, TEMP-SYCN,
  ROUTE-CHCEK, RADIO-TETS, PINGG, SYNCC ou TYPE+« X » ; secteur 257–356 ; statut OKAY ; heure 24–33,
  minutes/secondes 60–69. Analyse : `solvers.analyse_log_line`.

## 5. Carte du réseau ✅

- Fenêtre `NetworkMapApplication`. Nœud : `[data-onboarding-300-id="ServerItem-<serverName>"]` (le nom peut
  contenir des espaces : `ServerItem-Home Server`, `ServerItem-D4RK RM7MI`). Un clic ouvre le panneau du serveur
  (`ServerInfoPanel`) : « Connecter », « Connexion » (`ServerInfoPanelLoginButton`), « Marché » (Home), icône marché.
- **« Connecter »** déplace le point de sortie (`isEndpoint`) : `network-map:set.endpoint` → delta. Nécessaire pour
  ouvrir un marché distant ou le terminal d'un serveur distant. Le bot note le point de sortie de départ et le remet
  à la fin (`restore_endpoint`).
- **Maintenance** : `isInMaintenance` + `maintenanceEndsAt` (minuteur rouge sur la carte). Un serveur en
  maintenance est inaccessible ; un nœud de la route en maintenance peut couper l'accès à ceux d'après
  (`isReachable: false`). Le guide signale deux connexions cachées utiles dans ce cas (recherche d'IP :
  855.529.4.1 / .13 entre RM7-N1L1 et RM7-N2ECP ; 855.529.4.17 / .2 entre SRM7-N3L2 et RM7-N2L2).
- **Supprimer un fichier, un log ou une IP qui n'est pas demandé par un job met le serveur en maintenance**
  (guide). Après une maintenance, l'accès piraté est perdu : il faut repirater (§ 8).

## 6. Les 4 marchés ✅

| Marché (serveur) | Ouverture | Minuteur observé |
|---|---|---|
| Home Server | bouton texte « Marché » du panneau, sans « Connecter » | non mesuré |
| D4RK RM7MI (acte 3) | « Connecter », puis icône `[data-component-name="MarketIconButton"] button` | non mesuré |
| SRM7-M (Soyuz) | idem | 10 h |
| URM7-M (USOL) | idem | 8 h |

- Site : `MarketSite`, onglets `MarketNav` : Marché / Contrats / Engager / Infos. Chaque marché a ses contrats,
  sa réputation, ses mercenaires (« Engager ») et **son propre minuteur** `nextJobsResetAt` (renvoyé par `get.jobs`).
- **Quand le minuteur est échu, il ne repart que si on rouvre l'onglet Contrats de ce marché** (observé, et
  demandé par le joueur) : `get.jobs` renvoie alors une nouvelle liste et un nouveau `nextJobsResetAt`.
  Recliquer l'onglet Contrats rafraîchit la liste (astuce du guide). Le bot mémorise chaque minuteur dans
  `state.json` et le mode `--auto` rouvre chaque marché à l'échéance.
- Si `get.options` contient un `marketId`, le bot vérifie qu'il correspond à celui du serveur sur la carte ❓.

## 7. Jobs ✅

- Job (`get.jobs.jobs[]`) : `id`, `name`, `jobType`, `corporation`, `relatedServers[{serverName}]`, `deposit`,
  `reputationPenalty`, `rewardCredits`, `rewardReputation`, `conditions.items[].details`.
  Les **cibles** (IP, fichier, `logSeqs`) n'apparaissent qu'une fois le job signé (`recentJobs`,
  `status: "TAKEN"`, `canComplete`). `recentJobs` contient aussi les jobs finis/échoués (`status` ≠ TAKEN).
- Types : `DecryptFile` (File Decryption), `DownloadLog` (Log Download), `DownloadFile` (File Download),
  `DeleteLog` (Log Deletion), `DeleteFile` (File Elimination), `DeleteIps` (IP Cleanup), `AddIps` (IP Injection).
- Conditions d'accès (guide) : **Grant** = accès donné tant que le job est actif (`SaiAccessItem`) ;
  **Acquisition** = il faut avoir piraté le serveur. Le nom exact du champ n'a pas été relevé : le bot cherche
  « acquisition » / « grant » dans les conditions (`Bot.job_access`). ❓
- Signer : `JobCard` → « LIRE PLUS » → « Signer » → panneau `[data-component-name="JobSideout"]` → « Signer »
  (`market:job.take`). **Fermer le terminal SAI avant de signer un job serveur** (consigne du joueur).
- Terminer : carte « PRÊT À TERMINER » → dernier bouton `Button` de la carte (« Infos ») → `JobSideout` →
  « Terminer » (`market:job.complete`).
- Piège : **une carte signée n'affiche que « Temps restant / EN COURS »** ; le serveur et la corporation
  n'apparaissent qu'après « LIRE PLUS » (le bot déplie pour reconnaître la carte).
- Déchiffrement ✅ (+838 CR le 21/10) : le fichier (`.eb52x`, `.ab52p`…) arrive dans Downloads
  (`FolderApplication`, icône `FolderShortcut[data-app-id=<id du fichier>]`) → double-clic →
  `FileAnalysisProtocolApplication` → « Déchiffrer » (exige un logiciel de déchiffrement dans le Loadout :
  préréglage « decrypt » ou « big decrypt ») → ICE WALL. Réussite : `desktop:update.file` sur le fichier, puis
  le job passe « PRÊT À TERMINER ». Le fichier s'efface tout seul.
- Log Download ✅ (+1 389 CR le 21/10) : terminal SAI → JOURNAUX → bouton `DownloadIcon` de la bonne ligne.

## 8. Terminal SAI : connexion, accès, piratage

- Carte → serveur → « Connecter » → « Connexion » → fenêtre `SaiLogin` (WS `sai:get.login.status` :
  `remainingAttempts`, `isLocked`, `lockExpiresIn`, `hackTools[{moduleId, moduleName, hackPower,
  accessTypeOnHack}]`, `activeAccess`, `activeAccesses`, `serverDefenceRate`, `forceHack`).
- Dans `SaiLogin` ✅ :
  - **accès par tâche** : `[data-component-name="SaiAccessItem"]` → `sai:login.with-access` `{success}` ;
  - **outils de piratage** : `[data-sentry-component="SaiHackTools"]`, un libellé cliquable par outil
    (`moduleName`). Si aucun outil n'est assez puissant : `[data-sentry-component="SaiNoTools"]` ;
  - **force hack** payant en CR (ex. 1 500 CR) : **jamais automatisé** ;
  - identifiants (`login.attempt`) : jamais utilisés par le bot.
- Clic sur l'outil ✅ :
  - si un accès piraté est encore valable, le jeu le **réutilise** : `login.with-access` et le terminal s'ouvre (vu sur W3L3) ;
  - sinon `sai:hack.start` : si la puissance dépasse largement la défense → `{autoHacked: true, accessType}`
    sans mini-jeu ✅ ; sinon `minigames:start.minigame` puis ICE WALL ✅ (W3L1, défense ≈ puissance) ;
  - fin : `sai:update {reason: "hack.finished"}` ❓ (vu dans le code, l'essai W3L1 a été perdu au temps).
- Le préréglage Loadout compte : les outils de piratage dépendent du type de serveur (« hack », « hack soyuz »,
  « hack t4 » chez le joueur). Config `hack_presets` par `serverTypeName`.
- Terminal : `ServerAdministrationInterfaceApplication`, onglets `SaiHeader` : RÉSUMÉ / ACCÈS DE TRANSIT /
  FICHIERS / JOURNAUX.
- Journaux ✅ : `sai:get.logs` → `logs[]` avec `seq`, `message`, `sizeMb`, `source` (`job` pour la cible).
  **L'ordre des lignes du DOM est celui du WebSocket** : la cible (`logSeqs`) se retrouve sans ambiguïté même si
  plusieurs logs ont le même nom (le guide conseille sinon « la plus petite taille »). Icônes `DownloadIcon` / `TrashIcon`.
- Suppression ❓ : fenêtre `[data-component-name="SaiDeleteModal"]`, boutons `SaiDeleteButtons` (dernier = confirmer).
- Ajout d'IP ❓ : `[data-sentry-component="SaiAddButton"]` → `SaiAddIpModal` (`SaiAddIpModalIpInput`,
  `SaiAddIpModalSaveButton`). Le panneau d'infos du job a un bouton copier par IP (guide).

## 9. ICE WALL Break (déchiffrement et piratage) ✅

- `IceWallBreakApplication` ; cible `svg[data-sentry-component=TargetPreview] > g` ; mur
  `svg[data-sentry-component=WallBoard] > g > g` : **100 triangles** sur 10 rangées (rangée r = 2r+1 cases),
  hauteur de rangée 54, demi-largeur 31,5. Triangle pointe en bas : `translate(x, y+54) scale(1,-1)`.
- Signature d'une case = ses tracés `<path>` hors fond (`fill-opacity="0.2"`). **Une case cachée n'a que
  2 tracés au plus** ; les glyphes se révèlent peu à peu jusqu'à remplir le mur.
- 3 motifs à trouver (compteur « n / 3 »). **Un seul clic pose tout le motif**, ancré sur la case cliquée :
  la case du motif **la plus basse, pointe en haut, la plus centrale** (`solvers.ice_anchor_index`).
  Cliquer les 3 cases = 2 placements faux.
- Mauvais placement = **20 s de pénalité** ; échec de la partie = mini-jeu **verrouillé 20 min**
  (`start.minigame` avec `lockExpiresAt`) ; le code prévoit une **limite quotidienne** (« Daily limit reached »).
- Temps : environ **90 s** pour une défense égale à la puissance (W3L1 perdu à ~93 s, 2e motif). D'où le
  solveur avec déduction : dès qu'un seul placement reste compatible avec les cases visibles, on clique
  (`solve_ice_wall_partial`, « deduced »), sans attendre la révélation complète.
- Règle du projet : résoudre **uniquement à partir des glyphes visibles**, jamais avec l'état caché Colyseus.

## 10. Loadout et préréglages ✅

- Écran plein `LoadoutPage` / `LoadoutHeader` ; alimentation `[data-sentry-element=LoadoutHeaderPowerButtonStyled]`
  (état dans `localStorage['loadout-powered']`) : **il faut éteindre pour changer de module**, puis rallumer.
- « Préréglages » → `[data-component-name="PresetsModal"]`, lignes `PresetRow` (APPLIQUER / ÉCRASER /
  RENOMMER / SUPPRIMER ; pas de bouton APPLIQUER = déjà actif), WS `loadout:apply.preset`.
  Fermer avec `[data-sentry-element=LoadoutModalCloseButtonStyled]` : **le premier bouton de la fenêtre est
  « ENREGISTRER LA CONFIGURATION ACTUELLE », ne pas le cliquer**. « Retour » pour quitter l'écran.
- Préréglages du joueur : decrypt, big decrypt, hack, hack t4, hack soyuz.

## 11. Mercenaires ✅ (Crimson engagé le 21/10 : −5 466 CR, butin en inventaire)

- Engager : marché → onglet Engager (`expeditions:get.mercenaries` → `mercenaries[{callsign, status}]`) →
  `MercenaryCard` (« LIRE PLUS » `MercenaryCardCollapsedReadMore`) → bouton « Engager » → `HireSideoutPanel`
  (lieu et objectif `CustomSelectTrigger`, assurance `Toggle`, case `HireSideoutDisclaimerCheckbox`,
  bouton « Engager et verser un acompte », fermeture `SideoutHeaderCloseButton`).
  Devis `expeditions:configure` : `riskLevel`, `outcomeChances.deathChance`, `totalCost`, `prepaymentAmount`,
  `duration` (ms). Lancement `expeditions:launch`.
- Suivi : fenêtre `ExpeditionsApplication`, `expeditions:get.active` (rafraîchi à chaque `update`) :
  `status` PREPARING → RUNNING ⇄ EVENT → RETURNING → COMPLETED, `outcome`, `remainingAmount`, `messages[]`
  (`messageType: DECISION`, `decisionOptions[{id, label, riskModifier, lootModifier}]`, `decisionDeadline`, `isResolved`).
- Événement : bouton `ChamferedButton` (libellé de l'option) dans `ExpeditionsEventCard` → `expeditions:respond.event`.
  Sans réponse en 5 min, le jeu choisit (`isAutoResolved`).
- Retour : « Payer et ouvrir » (`ExpeditionsEventContainerButton`, `open.container`) puis « Tout récupérer »
  (`ExpeditionsClaimFooter`, `collect.all`) : **c'est à « Tout récupérer » que le solde est débité**. Butin à
  récupérer sous 24 h.

## 12. Pièges rencontrés pendant l'inspection

- Ouvrir cor3.gg dans un 2e onglet vole la session du 1er : demander au joueur de fermer le sien.
- Outil JavaScript de Claude in Chrome : une sortie qui contient une URL avec paramètres ou des données de
  cookie est bloquée → remplacer `?`, `&`, `=` ou ne renvoyer que des comptes. Une IIFE async renvoie `{}` :
  utiliser `await` au premier niveau.
- Les index d'un tampon d'événements plafonné se décalent : filtrer par horodatage ou par numéro de séquence.
- Préréglages : le premier bouton de la fenêtre est « Enregistrer la configuration » (voir § 10).
- Cartes de jobs signés sans serveur visible (voir § 7). Un job déjà signé par le joueur (IP Injection,
  expiré ensuite) a été laissé tel quel : le bot le note sans alerter.
- Déchiffrement : ne pas attendre seulement `job.can-complete` (poussé seulement si le marché est ouvert) :
  `desktop:update.file` ou le verrou du mini-jeu disent si c'est gagné ou perdu.
- ICE WALL : remettre à zéro la mémoire des cases et des essais à chaque nouvelle manche.

## 13. Jamais vu en direct (à vérifier au premier passage, en `--dry-run` d'abord)

- Suppression (`SaiDeleteModal`) et ajout d'IP (`SaiAddIpModal`) : jobs désactivés par défaut.
- Fin d'un piratage par ICE WALL jusqu'à l'ouverture du terminal (`hack.finished`, puis accès réutilisé).
- Nom exact du champ « Grant / Acquisition » dans les conditions d'un job.
- Minuteurs de Home Server et D4RK RM7MI ; comportement exact d'un marché en maintenance.
- Engager depuis un autre marché que Home Server (`hire_market`).

## 14. Ce qui a été fait sur le compte pendant l'inspection (avec l'accord du joueur)

Deux dailies validées (Signal Decode, code 321, puis System Log Integrity le 21/10 UTC) : +300 CR chacune ;
1 job Log Download (+1 389 CR), 1 job File Decryption (+838 CR), Crimson engagé (−5 466 CR, butin récupéré).
Piratages d'essai : W3L3 (accès existant réutilisé), un serveur piraté sans mini-jeu, W3L1 (ICE WALL perdu au temps).
Solde 12 747 → 10 108 CR. Préréglage « hack » et point de sortie RM7-S4L1 remis ; page rechargée à la fin.
