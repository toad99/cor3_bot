# cor3.gg : ce qu'on sait de l'interface

Relevés faits en direct les 20 et 21/10/2026 sur cor3.gg **frontend v1.25.51**, avec le compte du joueur
(navigateur Chrome, interface en français). Ce document est la mémoire du projet : une nouvelle discussion
doit pouvoir reprendre le travail à partir d'ici, sans relire l'ancienne conversation.

Légende : ✅ vérifié en direct · 🧪 couvert par la maquette de test seulement · ❓ déduit du code du jeu ou du guide, jamais vu.

**21/10/2026 au soir : frontend v1.25.71** (premier `--dry-run` sur le PC du joueur, captures `logs/debug/`).
Ont changé : la carte du Game Center (§ 4), l'envoi de l'état des salles à la connexion (§ 3) et donc la liste
des expéditions (§ 11), de nouveaux types de jobs (§ 7). Après correction, passage complet en `--dry-run` sans
erreur : daily jusqu'à son état (« déjà faite »), 4 marchés, contrats et minuteurs, signature d'un job jusqu'au
point d'arrêt, fenêtre Expéditions vide. Relevé `ws_dump` à 23:44 pendant que le joueur jouait (carte, contrats,
terminal SAI, signature, suppression de log, ajout d'IP) : § 3, 6, 7 et 8.

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
| Renommée | `[data-component-name="LevelIndicatorTop"]` : « 17.119 RENOMMÉE » puis « [107/900] » |

Le bureau suit la langue choisie (FR) ; **les mini-jeux restent en anglais**. Le bot accepte les deux (table `TXT`).

**Renommée** ✅ (v1.25.71, relevé dans 40 captures du 22–23/10) : bandeau `LevelIndicator` du bureau,
`LevelIndicatorTop` = `<span>17.119 RENOMMÉE</span><span>[107<b>/900</b>]</span>` (niveau + points/palier, la
décimale = la fraction du palier). **Chaque job terminé rapporte 3 points**, quels que soient ses CR et sa REP
(la REP du job est la réputation auprès du marché) : 4 jobs = +12, 11 jobs = +33, passage du niveau 16 au 17
à 769/800 + 12 jobs = 5/900. La daily du 23/10 n'a rien donné. Paliers vus : 800 (niveau 16), 900 (niveau 17).
À découvrir ❓ : ce que rapportent quêtes, expéditions et autres (le bot note la Renommée à l'ouverture et après
chaque partie, `--bilan` range les gains par source ; « hors du bot » = gagné entre deux passages).

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

v1.25.71 ✅ (noms des trames, journal du 21/10) : le client envoie `join-room` pour chaque salle qu'il suit et le
serveur répond par l'état de la salle. **Une vingtaine de salles sont rejointes dès la connexion**, dont
`expeditions` (→ `get.active`), `drone-missions` (→ `get.active`, sans doute l'onglet « Drone personnel »),
`minigames` (→ `get.state`), `profile` (→ `get.credits`, `get.levels`, `get.profile`), `mail`, `chat`, `forum`,
`news-portal`, `achievements`, `ranks`, `updater`. La carte et les marchés sont rejoints à l'ouverture de leur fenêtre
(`network-map:get.map` ; `market:connected`, `get.lots`, `get.options`, puis `get.jobs` à l'onglet Contrats) ; fermer
envoie `leave-room`. Les requêtes du client partent dans des trames `event` (journal : `→ event:get.jobs`).
Conséquence : l'état d'une salle rejointe à la connexion n'est pas renvoyé quand on ouvre sa fenêtre (§ 11).

Champs d'un serveur (`state.servers[]`) : `id`, `serverName`, `serverTypeName` (ex. « CEDRT public »,
« SOYUZ public »), `marketId`, `isReachable`, `isAccessible`, `accessType`, `isEndpoint` (point de sortie
actuel), `isInMaintenance`, `maintenanceEndsAt`, `timeUntilMaintenance`, `serverDefenceRate`.
Les deltas ne contiennent que les champs modifiés : le bot les fusionne par `id` (`GameEvents._merge_map`).

v1.25.71 ✅ (relevé `ws_dump` du 21/10 à 23:44) :
- `get.map` = `{protocol, streamId, revision, baseRevision, serverTime, kind: snapshot|delta|unchanged,
  state: {currentEndpointId, hackTools[], servers[]}, topology: {servers[{id, serverPlace}], connections[{serverA,
  serverB, isHidden}]}}`. `network-map:update` arrive vide (`{}`) ; le client redemande alors `get.map`.
- Nouveaux champs de serveur : `canSetEndpoint`, `hasAdminAccess`, `isDiscovered`, `isNew`, `transitType`
  (public / private / restricted), `serverOwner`, `faction`, `serverCluster`, `serverIp`.
- **`isReachable` dépend du point de sortie actuel** : RM7-S4L3 est joignable depuis URM7-M, plus depuis SRM7-M.
  Il ne dit donc pas si l'on peut aller sur un serveur. **`canSetEndpoint`** (« Connecter » possible) ne change pas
  avec le point de sortie (faux pour 4 serveurs D4RK privés, sans accès) : c'est lui que le bot regarde.
- **`accessType`** : `none` (aucun accès), `regular` (accès piraté, conservé) ou `admin` ; `isAccessible` vaut
  accessType ≠ none. Le 21/10 : 31 serveurs en `regular`, dont presque tous ceux des jobs.
- **`state.hackTools[]`** : outils de piratage du loadout actuel (`Porter-lite r4`, puissance 17,
  `accessTypeOnHack: regular`) ; liste vide quand le loadout n'en a pas (préréglage de déchiffrement).
- `timeUntilMaintenance` et `maintenanceEndsAt` étaient `null` partout : unité de `timeUntilMaintenance` inconnue ❓.

## 4. Daily tasks (Game Center → OPÉRATIONS QUOTIDIENNES → Open Module) ✅

- `GameCenterApplication` → carte « OPÉRATIONS QUOTIDIENNES » → « Open Module » → `DailyOpsMainScreen`,
  bouton `DailyOpsStartButton` (« Commencer la tâche »).
- v1.25.71 : chaque carte du Game Center est un `<button class="game-center-card">` (titre
  `.game-center-card-title`, « Open Module » = `.game-center-card-cta`). Le bot prend le plus petit ancêtre du
  titre **de n'importe quelle balise** qui contient « Open Module » : `ancestor::div` sautait le `<button>` et
  remontait jusqu'à la grille, qui contient les deux cartes. Vérifié en direct le 21/10 au soir jusqu'à l'état de
  la daily (« déjà faite »), puis Signal Decode réussi le 22/10 à 02:15 (code 155, easy, série 3). System Log
  Integrity n'a pas encore été revu en v1.25.71.
- `currentGameId` du 21/10 UTC : `019aea65-b0c2-7695-8bed-7b66cd57545c`, sans doute System Log Integrity (la daily
  validée ce jour-là) ❓. Le bot reconnaît Signal Decode à son id (`APP_SIGNAL_DECODE`) et joue System Log Integrity sinon.
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
  Le bot mémorise chaque minuteur dans `state.json` et le mode `--auto` rouvre chaque marché à l'échéance.
- v1.25.71 ✅ (22/10) : `nav[data-component-name="MarketNav"]`, onglets `MarketNavItem` avec
  `data-onboarding-300-id` `MarketNavItemBuy` (Marché, ouvert par défaut), `MarketNavItemJob` (Contrats),
  `MarketNavItemInfo` (Infos) ; « Engager » n'a pas d'id. **Recliquer l'onglet Contrats déjà ouvert ne redemande
  plus rien** (en v1.25.51 ça rafraîchissait la liste, astuce du guide) : passer par un autre onglet et revenir.
  Après `market:job.take`, le client redemande de lui-même `get.credits` et `get.jobs` : le bot reprend cette
  liste (`load_jobs(since=…)`). Sans ça, le passage de 02:15 du 22/10 a signé un Log Download sans le faire.
- Si `get.options` contient un `marketId`, le bot vérifie qu'il correspond à celui du serveur sur la carte ❓.
- v1.25.71 ✅ : `get.options` = `{market: {id, marketName, marketCorp, marketServer (id du serveur), jobSection,
  hireSection, buySection, sellSection}, reputation: {level, progress, requiredReputation, faction}, userCredits}` ;
  « CORE Market » est celui de Home Server, puis « SOYUZ RM7 Market », « USOL RM7 Market ». L'id est dans
  `market.id` : le contrôle du `marketId` ci-dessus ne s'applique plus.

- **Matériel et logiciels** ✅ (`market:get.lots`, catégories `HARDWARE` et `SOFTWARE`) : `price`, `accessLevel`
  (niveau de réputation du marché demandé), `unavailableReason`, `details.specs`. Logiciel : `[{type: DECRYPT |
  HACK | SEARCH, fileTypes | serverTypes, power: [min, max]}]` ; matériel : `cpuFrequency/cpuCores/cpuConsuming`,
  `gpuPower/gpuMemory/gpuConsuming`, `ramFrequency/ramMemory`, `psuPower/psuProtection`. Le PSU semble borner la
  consommation CPU + GPU (préréglages du joueur : 0,8 + 2,15 et 1,4 + 1,6 avec un PSU de 3,0).
  Réputation des marchés le 23/10 (`get.options.reputation`) : CORE 5, USOL 3, SOYUZ 3 (tous trois `isLevelLocked`,
  bloqués à 1 point du palier), D4RK = « BMI-ZEN Market » 4 (3 184/9 000).

## 7. Jobs ✅

- Job (`get.jobs.jobs[]`) : `id`, `name`, `jobType`, `corporation`, `relatedServers[{serverName}]`, `deposit`,
  `reputationPenalty`, `rewardCredits`, `rewardReputation`, `conditions.items[].details`.
  Les **cibles** (IP, fichier, `logSeqs`) n'apparaissent qu'une fois le job signé (`recentJobs`,
  `status: "TAKEN"`, `canComplete`). `recentJobs` contient aussi les jobs finis/échoués (`status` ≠ TAKEN).
- Types : `DecryptFile` (File Decryption), `DownloadLog` (Log Download), `DownloadFile` (File Download),
  `DeleteLog` (Log Deletion), `DeleteFile` (File Elimination), `DeleteIps` (IP Cleanup), `AddIps` (IP Injection).
- Nouveaux types vus dans `get.jobs` en v1.25.71 : `DecryptDownloadedFile` (« Data Download »,
  « Decrypt & Extract ») et `UploadFile` (« Data Upload »). D'après le joueur (22/10) : Decrypt & Extract =
  télécharger le fichier sur le nœud (FICHIERS), puis le déchiffrer dans Downloads ; Data Upload = FICHIERS →
  icône d'envoi → naviguer dans Downloads → cliquer le fichier cible. Codés ainsi (`do_upload_job` cherche
  `UploadIcon`), jamais vus par le bot ❓ : les détails du job signé et la fenêtre d'envoi restent à relever.
- Decrypt & Extract ✅ (22/10, 04:20, URM7-W4L2) : une fois signé, `details = {extensions: [{ext: ".ab52p",
  encryptionLevel: [14, 26]}], files: [{id: "j_01a16168", name: "db_6531.txt"}]}`, mais **sur le nœud le fichier
  s'appelle `db_6531.bak`** : même nom, extension inconnue d'avance (dit le joueur : mp3, png…). L'`id` « j_… »
  n'est pas celui du fichier (le même revenait sur un Data Download) : le bot l'ignore et cherche le nom sans
  extension, seulement s'il est unique. Le préréglage se choisit d'après `extensions[].ext`, pas d'après le nom.
- 22/10 de 04:10 à 04:20 ✅ : 5 déchiffrements ICE WALL réussis (4 sur Home Server, `.eb52x [4, 10]` et `.ab52p [7, 15]` ;
  1 sur D4RK, `.eb52x [14, 25]`), 2 IP Injection, 2 Data Download, 2 Log Deletion, puis plus tôt 2 File
  Elimination. Échec « module trop faible » : `.xvct [22, 27]` (SRM7-M) avec « big decrypt » → pas de bouton
  Déchiffrer. Le bot classe alors le job perdu (`Hopeless`, `lost_jobs` dans `state.json`) et n'y retouche plus.
  Revu le 23/10 à 11:56 (même cas, 2e job perdu) : le bot retient l'extension (`unreadable` : `{"xvct": "big decrypt"}`)
  et refuse ensuite ces contrats avant de les signer, l'extension étant déjà dans `details.extensions` de l'offre
  (`decrypt_problem`). Après un achat de module : nouveau préréglage dans `decrypt_presets`, ou retirer la ligne. Le nom ne
  suffit pas à reconnaître un job : « Data Download » existe aussi avec un type géré (URM7-M), seul `jobType` compte.
- v1.25.71 ✅ (relevé `ws_dump` du 21/10) : `conditions = {hasDeposit, hasRepFine, serverConfigId, items[]}`,
  chaque élément `{type, label, isPrimary, subsystem, details}`. La tâche principale a `isPrimary: true` et
  `type` = `jobType` ; ses `details` (cibles) n'apparaissent qu'une fois signé. Le job a aussi `features[]`
  (les `label`), `description`, `timeToComplete` (ms, de 2 à 7 h) et, une fois signé, `endTime` (échéance).
  En cas d'échec, chaque job coûte **soit son dépôt** (`hasDeposit`, `deposit`), **soit une amende de réputation**
  (`hasRepFine`, `reputationPenalty`).
- **Conditions d'accès** ✅ : `GrantServerAccess` (« Access Grant ») = l'accès vient avec le job (`SaiAccessItem`
  une fois signé) ; `GetServerAccess` (« Access Acquisition ») = il faut un accès au serveur (déjà piraté, ou
  pirater). Le 21/10, la plupart des jobs serveur étaient en Acquisition. Avant de signer, le bot accepte Grant,
  ou Acquisition si la carte donne `accessType` regular / admin ; sinon il refuse (`Bot.access_problem`,
  réglage `sign_without_access`).
- **Déchiffrement** : le contrat annonce l'extension et une plage de chiffrement **avant la signature**
  (`details.extensions[{ext, encryptionLevel: [min, max]}]`) : `.eb52x [4, 10]` et `.ab52p [7, 15]` sur Home Server,
  `.eb52x [18, 25]`, `.ab52p [14, 26]`, `.xvct [22, 27]` sur les marchés distants. Une fois signé :
  `details.files[{id, name, isDecrypted}]`. D'après le joueur, hors de Home Server c'est un pari (une fois sur deux,
  le fichier tiré demande un meilleur module), et ce matériel n'est pas rentable à son stade. Le bot ne s'en sert
  pas encore : il faudrait connaître le niveau maximal du module du joueur.
- Gains relevés le 21/10 (CR, REP, dépôt ou amende) : Log Download 856–1 445 ; Data Download 1 445 (dépôt 506) ;
  File Decryption 838 (dépôt 126), 1 260 et 1 740 (amendes 137 et 231 REP) ; Decrypt & Extract 1 442 (amende
  157) ; File Elimination 721–1 460 (dépôts 109–219) ; Log Deletion 1 221–1 274 (dépôts 184–446) ; IP
  Injection 1 274–1 708. Conditions : toujours l'accès (Grant ou Acquisition) + la tâche principale. Un job à
  dépôt est rentable dès une réussite sur quatre ; un job à amende ne coûte aucun CR s'il échoue. Le 21/10,
  13 des 14 serveurs des jobs étaient déjà piratés (`regular`).
- `recentJobs` contient aussi les jobs `FAILED` ; `market:job.dismiss` `{status}` les retire. Après `job.complete`,
  le serveur pousse `market:job.completed {jobId, marketId, name, rewardCredits, rewardReputation, faction}`.
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
- v1.25.71 ✅ : `activeAccess` vaut `"none"` ou `"regular"`, et `activeAccesses[{id, accessType, sourceType: "hack",
  expiresAt: null, usedAt}]` : un accès piraté n'expire pas (il se perd à la maintenance). `remainingAttempts` : 5.
  `sai:get.summary` donne aussi `accessType`, `isInMaintenance`, `timeUntilMaintenance`, `hasLogs`, `hasFiles`,
  `hasTransitAccess`. Signer un job pousse `sai:update {reason: "job.taken", jobId, serverId, subsystems}`.
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
- v1.25.71 ✅ (capture du 22/10 à 02:36, RM7-S4L3) : `SaiLogs` est un tableau Nom / .type / .taille / Action ;
  chaque ligne de la `ScrollArea` a quatre cellules : `LogIcon` + message, « -- », taille (« 2.21Mb »), puis les
  boutons `DownloadIcon` et `TrashIcon` **dans leur propre cellule**. 19 lignes, la cible `seq` 21 en 5e position
  comme dans `get.logs` ; plusieurs messages en double (« Certificate renewal scheduled » × 4). Le bot vérifie le
  texte de la ligne entière (plus grand bloc qui ne contient que ce bouton) : l'ancien sélecteur ne lisait que la
  cellule Action, vide, et refusait de cliquer. Nouveau panneau à droite, `SaiSearchTools` : « Rechercher des
  outils (0) », « Faire correspondre à la liste », « Rechercher des objets de valeur », fonctions de
  correspondance et de recherche « Indisponible » (sans doute un module à équiper) ❓.
- v1.25.71 ✅ (captures du 22/10 à 03:25–03:27) :
  - onglets du terminal : `SaiTabStyled` avec `data-onboarding-300-id` `SaiTabs-TRANSIT`, `SaiTabs-FILES`,
    `SaiTabs-LOGS` (« Résumé » sans id) ;
  - **FICHIERS** (`SaiFiles`) : même tableau Nom / .type / .taille / Action, mais **les icônes des boutons n'ont
    plus de nom** : deux boutons par ligne, télécharger puis supprimer (le rouge, `rgba(254, 73, 73)`). Bouton
    d'envoi en haut : `SaiAddButtonStyled` avec l'icône `UploadIcon` (`data-sentry-element`). Le bot prend la
    paire de boutons de la ligne visée et vérifie le nom du fichier sur la ligne avant de cliquer ;
  - **ACCÈS DE TRANSIT** (`SaiTransit`) : titre « Liste noire », bouton d'ajout
    `[data-component-name="SaiAddButton"]` (icône `AddUserIcon`), tableau Adresse IP / Durée d'accès / Action.
    Ajout d'IP ✅ (22/10, 3 IP Injection réussies). Lignes : icône + IP, durée, **un bouton poubelle sans
    icône nommée** (rouge) : le bot prend le plus grand bloc autour de l'IP qui ne contient qu'un bouton.
  - Bouton d'envoi de FICHIERS : l'icône est nommée en `data-sentry-element="UploadIcon"` (pas `-component`).
  - Data Upload (22/10, 04:47) : le bouton ouvre « Sélectionner un fichier à envoyer », déjà sur
    `./Bureau/Downloads` (`FilePickerBreadcrumbs`) ; fichiers `[data-component-name="FilePickerItem"]`, nom choisi
    dans `FilePickerFileName`, bouton « Envoyer » `FilePickerAttachButton`, croix `FilePickerCloseButton`.
    **Sans le clic sur Envoyer, la fenêtre reste ouverte devant tout** (tous les clics suivants échouent) :
    le bot la referme toujours. Trame de réponse à l'envoi pas encore relevée ❓.
  - 22/10 à 05:00 : un clic sur « Accès de transit » juste après la connexion n'a envoyé aucun `get.transit`.
    `Bot.sai_tab` clique l'onglet par son id (`SaiTabs-*`), attend 5 s, sinon passe par « Résumé » et revient.
  - Bilan au 22/10 05:05 : les 9 types de jobs ont été réussis par le bot en v1.25.71 (Log/Data Download, File
    Decryption Home et distants jusqu'à [18, 25], Decrypt & Extract, Data Upload, Log Deletion, File Elimination,
    IP Cleanup, IP Injection). Seul échec définitif : `.xvct [22, 27]`, trop fort pour « big decrypt ».
  - Déchiffrement (22/10, 12:45) ✅ : `FileAnalysisProtocolApplication` affiche « NIVEAU DE CHIFFREMENT 25.0 » et
    « PUISSANCE DE DÉCHIFFREMENT 21.0 » (en rouge si plus faible) ; le bouton Déchiffrer est alors **présent mais
    grisé** (un clic attend 20 s pour rien). Puissance du préréglage « decrypt » : 21. Le bot lit les deux
    nombres, les écrit au journal, et classe le job perdu si le niveau dépasse la puissance.
  - Piratage impossible (22/10, 22:47) ✅ : SRM7-M, défense 24, meilleur outil « hack soyuz » A/Bver 410 puissance
    19 : le clic sur l'outil ne lance rien. Le bot mémorise la puissance maximale vue (`hack_power` dans
    `state.json`) et refuse d'avance un contrat Acquisition sur un serveur sans accès plus défendu ; si ça arrive
    quand même, job perdu tout de suite.
  - Bilan du 22/10 (journal) : 62 jobs terminés, 75 598 CR, 10 385 REP ; 3 déchiffrements perdus (fichiers trop
    chiffrés) et 1 piratage impossible (expire le 23/10), tous à amende de réputation, aucun dépôt perdu.
  - Decrypt & Extract : après l'ICE WALL réussi, `desktop:update.file` arrive avec l'id du fichier téléchargé,
    pas l'id « j_… » du job : le bot accepte alors n'importe quel `update.file`.
  - Le raccourci Downloads du bureau peut être caché par une fenêtre : double-clic DOM en secours.
- Dock ✅ (22/10) : `LoadoutTabBarItem` existe toujours, mais le dock peut être caché (fenêtre Marché ouverte en
  grand) : le clic normal échoue, le bot fait un clic DOM (`safe_click`).
- Suppression ❓ : fenêtre `[data-component-name="SaiDeleteModal"]`, boutons `SaiDeleteButtons` (dernier = confirmer).
  Réponse du serveur ✅ (Log Deletion faite à la main le 21/10) : `sai:log.delete {deletedSeq, sizeMb}`.
- Ajout d'IP ❓ : `[data-sentry-component="SaiAddButton"]` → `SaiAddIpModal` (`SaiAddIpModalIpInput`,
  `SaiAddIpModalSaveButton`). Le panneau d'infos du job a un bouton copier par IP (guide).
  Réponse du serveur ✅ (IP Injection faite à la main le 21/10) : `sai:transit.add {ip: {ip, description, source: "job"}}`.

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
- v1.25.71 (22/10) : `PresetRow` → nom dans `[data-sentry-element="PresetRowNameStyled"]`, composants (CPU, GPU,
  RAM, PSU, logiciel : « Cypher+ v1.4 » pour decrypt, « Porter-lite r4 » pour hack, « Porter-triX lr8 » pour
  hack t4) ; boutons Appliquer / Actif (désactivé) / Écraser / Renommer / Supprimer ; compteur « 5/10 ». Les
  lignes s'affichent après la fenêtre : attendre la première. Si le bot échoue au milieu, il referme la
  fenêtre, **rallume le Loadout** et quitte l'écran (sinon il restait éteint et bloquait la suite du passage).
- **Le jeu se rouvre sur le dernier écran** (22/10, 03:45) : après un passage interrompu dans le Loadout, la page
  s'ouvre sur `LoadoutHeader` sans dock. `open_game` attend le dock **ou** le Loadout, rallume et fait « Retour ».

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
- v1.25.71 (21/10, sans expédition en cours) : `ExpeditionsApplication` a trois onglets
  (`ExpeditionsTabs` / `ExpeditionsTabItem` : « Expéditions actives », « Drone personnel », « Archives des raids ») ;
  sans expédition, l'onglet actif affiche `ExpeditionsActiveEmpty` (« Il n'y a aucune expédition active »).
  ✅ **`expeditions:get.active` n'arrive qu'à la connexion** (réponse au `join-room`, § 3) : rien n'est envoyé à
  l'ouverture de la fenêtre. Le bot prend la dernière trame reçue (`Bot.active_expeditions`) ; sans aucune trame,
  il se fie à l'état vide de l'écran. ❓ Avec une expédition en cours : la liste est-elle renvoyée à chaque
  changement, et après un engagement (`launch`) ? Le suivi (`watch_expeditions`) lit toujours la dernière trame.
- Événement : bouton `ChamferedButton` (libellé de l'option) dans `ExpeditionsEventCard` → `expeditions:respond.event`.
  Sans réponse en 5 min, le jeu choisit (`isAutoResolved`).
- Retour : « Payer et ouvrir » (`ExpeditionsEventContainerButton`, `open.container`) puis « Tout récupérer »
  (`ExpeditionsClaimFooter`, `collect.all`) : **c'est à « Tout récupérer » que le solde est débité**. Butin à
  récupérer sous 24 h.
- **Renommée** (guide de la communauté, v1.18.80 du 24/04) : **une expédition réussie rapporte +10** (un job : +3,
  confirmé en direct, § 2). Mercenaire ou drone ❓ : à vérifier avec `--bilan` (source « mercs »).
- **Drone personnel** (onglet « Drone personnel » des Expéditions) : construit par la quête Wings of Mars. D'après le
  guide, il ne peut pas être détruit pour de bon ; il faut au moins 1 de durabilité et 1 de batterie pour partir.
- Trames ✅ (`ws_dump` du 23/10, 04:20–04:44, mission lancée par le joueur) :
  - `drone-missions:get.options` (à chaque ouverture de l'onglet) = `{drone: {id, name, durability, maxDurability,
    stealth, payloadGrams, isRepairing, repairSecondsRemaining, isDestroyed, renameCost}, battery: {current, max,
    restoreSeconds, restorePerHour: 8.33}, repairRate: {creditsPerPoint: 200, secondsPerPoint: 200},
    activeMissionId, locations[{id, name, baseRisk, missions[{id, name, batteryCost: 30, durationSeconds: 1200}]}]}`.
    Seul lieu : « The Surface of Mars » (risque de base 25), mission « Surface Exploration ».
  - Départ : `event:launch {locationConfigId, missionConfigId}` → `drone-missions:launch` (mission RUNNING avec
    `finalRisk`, `batteryCost` et **tous ses événements d'avance** : `channelEvents[{eventId, type, spawnAtSeconds,
    message, options[{id, label, effect: {riskDelta, lootDelta}, requiredModuleId}]}]`). Pas de CR dépensés.
  - Pendant : `drone-missions:event` à l'heure dite. `INFO_DISCOVERY` = information ; `DECISION` et `HAZARD`
    (`isResolvable: true`) = choix, réponse `event:resolve.event {missionId, eventId, optionId}`. Une option à
    `requiredModuleId` demande un module du drone.
  - Fin : `drone-missions:completed {outcome: FULL_SUCCESS | PARTIAL_SUCCESS | FAIL, damageDealt, durabilityAfter,
    destroyed, loot[{name, quantity}]}`. **Le butin arrive dans le courrier** (`mail:update.state`, expéditeur
    « Drone operations », « Drone mission loot ») : `event:claim.item {attachmentId, placement: {x, y, rotated}}`
    le range dans le stash.
  - Historique : `drone-missions:get.archived` (sur 6 missions à risque 25 : 1 réussite, 3 partielles, 2 échecs ;
    dégâts 5, 12 et 25).
- **Le drone ne donne pas de Renommée** ✅ : 107/900 avant et après un PARTIAL_SUCCESS (23/10) ; la Renommée exacte
  arrive aussi dans `profile:get.levels {level, progress, nextLevelRequiredXp}` à la connexion. FULL_SUCCESS ❓.
- La durabilité ne change pas le risque affiché : `finalRisk` 25 au départ avec 100 comme avec 88 de durabilité.
- Mercenaire du 23/10 (Apex, risque 40, mort 3 %) : `outcome: DEATH` au retour, acompte de 1 900 CR perdu.
- **FULL_SUCCESS sans Renommée non plus** ✅ (23/10, 06:15 : 107/900 avant et après).
- Écran ✅ (`ws_dump --dom` du 23/10, 05:53–06:16) : fenêtre Expéditions, onglet `ExpeditionsTabItem` « Drone
  personnel » (envoie `drone-missions:get.options`) → `ExpeditionsPidlDroneTab`. Sans mission :
  `ExpeditionsPidlDroneLaunch` avec deux `Button` : « Démarrer la sortie » et « Réparation » (écran jamais relevé).
  ✅ (23/10, 07:09, premier essai du bot) **« Démarrer la sortie » ouvre un panneau** `ExpeditionsPidlDroneSideout`
  (fermeture `PidlDroneSideoutHeaderBtnStyled`) : durabilité, batterie, `CustomSelectTrigger` Lieu puis Objectif,
  « Infos sortie » (risque, durée, consommation) et un `<button>` « Envoyer » sans attribut : c'est lui qui envoie
  `event:launch`. Sans trame à l'ouverture du panneau ; état dans
  `PidlDroneConditionDurability` / `PidlDroneConditionBattery` (`data-onboarding-300-id` aussi), modules
  `PidlDroneModuleSlot`. En mission : `ExpeditionsPidlDroneActiveMission`, minuteur `ExpeditionsProgressTimer`,
  événements `ExpeditionsPidlDroneActiveEvents` → `ExpeditionsEventCard` (`data-onboarding-300-id`
  `ExpeditionsEventCard-CAN_ANSWER` ou `-FOUND`), boutons `ChamferedButton` au libellé de l'option, comme les
  mercenaires.
- Courrier ✅ : dock `[data-onboarding-300-id="StashTabBarItem"]` → page `StashPage` (sans dock, « Retour » dans
  `StashHeader`) → `MailboxPanel` : « Tout récupérer » (`StashButton`, `event:claim.all` → `mail:claim.all
  {results[{attachmentId, status: "claimed"}]}`) ou « Récupérer l'objet » par `MailCard` (`claim.item`, avec la
  case du stash). ⚠️ 23/10 à 09:21 (bot) : « Tout récupérer » n'a envoyé aucune trame et a remplacé le stash par l'écran de transfert `StashLayoutDesktop` : `TransferPanel` (« Objets 2 », `LootGrid`), `InventoryPanel`, et à côté (pas dedans, revu à 12:05) `TransferActions` : `StashButton` « Tout prendre » et « Vers l'inventaire » (primary), plus de « Retour »). À 05:53 (joueur), le même bouton avait envoyé `claim.all` directement. Trame de « Tout prendre » ❓ : le bot accepte `claim.all`, `claim.item` ou `update.state` `pendingCount: 0`, puis quitte par Échap. Le butin se vend dans le stash (`sell.item` → `profile:receive.credits`) : le bot ne vend rien.
- Bot (`run_drone`, partie `drone`, maquette `&drone=<batterie>&dronedur=<durabilité>`) : courrier, départ si
  batterie ≥ coût et durabilité ≥ `min_durability`, surveillance jusqu'au retour, prochain passage dans `state.json`
  (`drone.next` : retour de mission ou batterie rechargée).

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

- Suppression (`SaiDeleteModal`) et ajout d'IP (`SaiAddIpModal`) faits par le bot : les réponses du serveur sont
  relevées (§ 8), pas les fenêtres. Jobs désactivés par défaut.
- Fin d'un piratage par ICE WALL jusqu'à l'ouverture du terminal (`hack.finished`, puis accès réutilisé).
- Unité de `timeUntilMaintenance`, et valeur de `canSetEndpoint` quand un nœud de la route est en maintenance.
- Minuteurs de Home Server et D4RK RM7MI ; comportement exact d'un marché en maintenance.
- Engager depuis un autre marché que Home Server (`hire_market`).
- v1.25.71 : System Log Integrity, le terminal SAI et la fin d'un job par le bot, et les Expéditions avec une
  expédition en cours (mise à jour de la liste pendant le suivi et après un engagement, § 11).

## 14. Ce qui a été fait sur le compte pendant l'inspection (avec l'accord du joueur)

Deux dailies validées (Signal Decode, code 321, puis System Log Integrity le 21/10 UTC) : +300 CR chacune ;
1 job Log Download (+1 389 CR), 1 job File Decryption (+838 CR), Crimson engagé (−5 466 CR, butin récupéré).
Piratages d'essai : W3L3 (accès existant réutilisé), un serveur piraté sans mini-jeu, W3L1 (ICE WALL perdu au temps).
Solde 12 747 → 10 108 CR. Préréglage « hack » et point de sortie RM7-S4L1 remis ; page rechargée à la fin.
