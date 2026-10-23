# Méthode : inspecter cor3.gg et faire évoluer le bot sans risque

Ce document explique **comment** les relevés de `docs/INSPECTION.md` ont été obtenus, pour pouvoir les refaire
après une mise à jour du jeu (nouvelle version du frontend, nouvel écran, sélecteur cassé).

## 1. Règles (demandées par le joueur, non négociables)

1. **Aucune action irréversible sans accord explicite du joueur dans la conversation** : achat, suppression
   (fichier, log, IP), dépôt, engagement ou paiement de mercenaire. Lire, ouvrir une fenêtre, se connecter à un
   serveur (« Connecter ») ou rouvrir l'onglet Contrats ne coûte rien.
2. **Jamais d'identifiants** : le joueur se connecte lui-même. **Ne jamais lire ni afficher les jetons**
   (localStorage `cor3-rkey`, `cor3-tkey`, en-têtes d'authentification). Les outils de `tools/` masquent tout ce
   qui y ressemble avant de l'écrire.
3. Pas de « force hack » (CR), pas de CAPTCHA, pas d'appel direct à l'API à la place de l'interface
   (la daily se valide côté client par un POST sans corps : on ne s'en sert pas), pas d'état caché du serveur
   Colyseus pour ICE WALL.
4. **Une seule session par compte** : ouvrir le jeu dans un nouvel onglet déconnecte celui du joueur. En
   inspection en direct, lui demander d'abord s'il ferme son onglet ou si on travaille dans le sien.

## 2. Inspection en direct avec Claude in Chrome

Dans l'onglet cor3.gg du joueur (connecté), avec l'outil JavaScript :

- **Inventaire des sélecteurs stables** de l'écran affiché :
  ```js
  const pick = a => [...new Set([...document.querySelectorAll(`[${a}]`)].map(e => e.getAttribute(a)))];
  ({sentry: pick('data-sentry-component'), el: pick('data-sentry-element'),
    name: pick('data-component-name'), ids: pick('data-onboarding-300-id')})
  ```
- **Structure d'une fenêtre** : `document.querySelector('[data-sentry-component="MarketSite"]').outerHTML.slice(0, 3000)`
  (les classes `go…` sont à ignorer).
- **WebSocket** : coller `tools/ws_tap.js` (crochet sur `WebSocket.prototype.send` + décodeur msgpack +
  masquage), jouer l'action à observer, puis lire `__cor3ws.summary()` et
  `__cor3ws.last('market', 'get.jobs')`. Recharger la page à la fin pour retirer le crochet.
- **Code du jeu** : la logique des mini-jeux est dans le JavaScript chargé par la page. Lister les morceaux :
  `performance.getEntriesByType('resource').map(e => e.name).filter(n => n.endsWith('.js'))` ; le script
  principal contient le registre `__vite__mapDeps` avec tous les morceaux chargés à la demande
  (`assets/<Nom>-<hash>.js`). Récupérer un morceau avec `fetch(url).then(r => r.text())` et y chercher une
  chaîne visible à l'écran (ex. « Daily limit reached », « Select », nom d'un composant). C'est ainsi qu'ont été
  trouvés la règle de l'ancre d'ICE WALL, le générateur de System Log Integrity et la longueur des codes.
- Pièges de l'outil : une sortie contenant une URL avec paramètres ou des cookies est bloquée → nettoyer la
  sortie (remplacer `?`, `&`, `=`) ou ne renvoyer que des comptes ; une IIFE `async` renvoie `{}` → mettre
  `await` au premier niveau.

## 3. Inspection depuis le PC du joueur (sans Claude in Chrome)

`python tools/ws_dump.py --minutes 10 --dom` ouvre le jeu dans le profil dédié du bot ; le joueur joue
l'écran à documenter, le script écrit les trames décodées (secrets masqués) dans `logs/ws_dump_<date>.jsonl`
l'inventaire des sélecteurs dans `logs/dom_<date>.json` et le HTML de l'écran final (JWT masqués) dans
`logs/dom_<date>.html`. Il prend le verrou du bot : il refuse de démarrer pendant un passage, et les tâches
planifiées attendent ou passent leur tour pendant l'enregistrement. Ces fichiers peuvent être donnés à une nouvelle
conversation.

## 4. Faire évoluer le bot

- Sélecteurs : uniquement `data-onboarding-300-id`, `data-sentry-component/element`, `data-component-name`,
  textes de boutons (FR | EN dans `TXT`) et classes en clair des mini-jeux.
- Attendre la réponse du serveur plutôt qu'une pause fixe : `mark = self.events.mark()`, action, puis
  `self.wait_event(salle, action, mark)`.
- Toute action qui valide quelque chose passe par `self.allowed("…")` (mode `--dry-run`) ; toute dépense ou
  suppression est désactivée par défaut et s'active dans `config.toml`.
- `Unavailable` (maintenance, route coupée, serveur absent) = note, on réessaie plus tard ;
  `StepFailed` = vrai problème, alerte.
- Nouvel écran = nouveau morceau de maquette dans `tests/mock_game/index.html` + un test dans
  `tests/test_mock_game.py`. La maquette reprend les attributs du vrai jeu ; la page envoie `{room, action, data}`
  en JSON au faux serveur WebSocket, qui le renvoie en msgpack comme le vrai.
- Lancer `python -m pytest tests -q` (tests des solveurs, des règles et Playwright sur la maquette).

## 5. Après une mise à jour du jeu

1. Le journal signale une version de frontend différente de `TESTED_FRONTEND`. Le journal du jour contient
   aussi, au niveau DEBUG, le nom de chaque trame WebSocket (`WS ← salle:action` ; côté client `WS → join-room`
   ou `WS → event:action` ; jamais le contenu) : une action renommée ou déplacée (par exemple envoyée à la
   connexion plutôt qu'à l'ouverture d'une fenêtre) se repère en le comparant au § 3 d'`INSPECTION.md`.
2. `python cor3_bot.py --dry-run` : le bot lit et résout tout mais ne valide rien ; en cas d'échec, capture
   d'écran + HTML dans `logs/debug/`.
3. Comparer le HTML de `logs/debug/` (ou un `ws_dump`) avec `docs/INSPECTION.md`, corriger le sélecteur,
   ajouter le cas à la maquette, relancer les tests, mettre à jour `docs/INSPECTION.md` et `TESTED_FRONTEND`.
