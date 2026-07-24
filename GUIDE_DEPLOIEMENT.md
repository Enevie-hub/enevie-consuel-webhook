# Guide de déploiement — Webhook CONSUEL ENEVIE

Objectif : quand tu passes "Consuel ST" à "A faire" sur un dossier Monday, le DT se génère
et se dépose tout seul dans "DT & Schéma générés".

**Important** : cette première version gère uniquement les dossiers "micro-onduleurs sans
batterie" (100% Bourgeois Global, HMS-1000). Les dossiers avec batterie Aura ou d'autres
marques seront ignorés proprement (statut "skipped") — à traiter manuellement comme avant.

---

## Étape 1 — Créer un compte GitHub (si tu n'en as pas) — 5 min
1. Va sur https://github.com/signup
2. Crée un compte gratuit

## Étape 2 — Créer un dépôt et y mettre les fichiers — 10 min
1. Sur GitHub, clique "New repository" (bouton vert)
2. Nom : `enevie-consuel-webhook` → Create repository
3. Clique "uploading an existing file"
4. Glisse-dépose TOUS les fichiers de ce dossier : `app.py`, `requirements.txt`, `Procfile`,
   `DT_MO_template.pdf`, `signature_transparent.png`, `tampon_enevie_transparent.png`
5. Commit changes

## Étape 3 — Récupérer ton token API Monday — 5 min
1. Dans Monday, clique sur ton avatar (en bas à gauche) → **Administration**
2. Menu de gauche → **API**
3. Copie le token qui s'affiche (une longue chaîne de caractères) — garde-le, tu en auras besoin à l'étape 5

## Étape 4 — Créer un compte Render et déployer — 15 min
1. Va sur https://render.com et crée un compte (tu peux te connecter directement avec GitHub)
2. Clique **New +** → **Web Service**
3. Connecte ton dépôt GitHub `enevie-consuel-webhook`
4. Renseigne :
   - **Name** : enevie-consuel-webhook
   - **Region** : Frankfurt (le plus proche de la France)
   - **Branch** : main
   - **Runtime** : Python 3
   - **Build Command** : `pip install -r requirements.txt`
   - **Start Command** : `gunicorn app:app`
   - **Instance Type** : Free
5. **Avant de cliquer "Create Web Service"**, descends à "Environment Variables" et ajoute :
   - `MONDAY_API_TOKEN` = (colle le token récupéré à l'étape 3)
6. Clique **Create Web Service**
7. Attends que le déploiement se termine (2-3 minutes, statut passe à "Live")
8. Note l'adresse de ton service, en haut de la page, du style :
   `https://enevie-consuel-webhook.onrender.com`

## Étape 5 — Vérifier les identifiants de colonnes Monday — 10 min
Le script utilise des identifiants de colonnes qui doivent être vérifiés/ajustés :
- `COL_PROJETS = "texte1"` — colonne "PROJETS"
- `COL_ADRESSE = "lieu"` — colonne adresse
- `COL_TELEPHONE = "numero"` — **à vérifier** : l'identifiant réel peut être différent
- `COL_DATE_SIGNATURE = "timeline"` — **à vérifier**
- `COL_CONSUEL_STATUS = "statut1__1"` — colonne "Consuel ST"
- `COL_FILES = "file_mm5hhfhc"` — colonne "DT & Schéma générés"

Pour vérifier un identifiant de colonne : dans Monday, ouvre le board "SUIVI DOSSIERS 2026",
clique sur les 3 points en haut de la colonne concernée → "..." → parfois affiché directement,
sinon le plus fiable est de me redemander de vérifier via l'API la prochaine fois qu'on se parle.

Si un identifiant est faux, modifie-le directement dans `app.py` sur GitHub (bouton crayon
"Edit" sur le fichier) puis Render redéploiera automatiquement.

## Étape 6 — Connecter le webhook à Monday — 10 min
Monday doit envoyer un signal à ton adresse Render quand "Consuel ST" passe à "A faire".

**Option simple (automatisation native Monday)** :
1. Sur le board "SUIVI DOSSIERS 2026", clique "Automatiser" (en haut à droite)
2. "+ Ajouter une automatisation" → cherche un modèle "Quand le statut change → Envoyer un webhook"
   (si cette action n'existe pas nativement dans ta version de Monday, utilise l'option ci-dessous)
3. Configure : Quand "Consuel ST" devient "A faire" → Envoyer un webhook vers
   `https://enevie-consuel-webhook.onrender.com/webhook/consuel`

**Option technique (si l'automatisation native n'a pas d'action "webhook")** :
Il faut créer l'abonnement webhook directement via l'API Monday. Dis-le moi la prochaine fois
qu'on se parle, je peux le faire pour toi en une requête (je peux appeler l'API Monday
directement, contrairement à l'upload de fichiers qui était bloqué).

## Étape 7 — Tester — reste du temps
1. Choisis un dossier de test réel (config micro-onduleurs BG sans batterie)
2. Passe "Consuel ST" à "A faire"
3. Attends 30-60 secondes (le service gratuit doit se "réveiller" s'il était en veille)
4. Vérifie la colonne "DT & Schéma générés" — le PDF devrait apparaître
5. Vérifie que "Consuel ST" est repassé à "Créé"

### En cas de problème
- Sur Render, onglet **Logs** : tu verras les erreurs éventuelles en temps réel
- Erreur la plus probable : un identifiant de colonne incorrect (étape 5) → le message d'erreur
  dans les logs te dira quelle colonne pose problème

---

## Ce qui n'est PAS encore géré (pour une prochaine session)
- Dossiers avec batterie Aura (gabarit SC144C-5)
- Autres marques (Sofar, Fox, Thaleos)
- Génération/sélection automatique du schéma unifilaire correspondant
- Vérification humaine avant envoi définitif au CONSUEL (recommandé de garder un œil sur les
  premiers dossiers générés automatiquement avant de faire confiance à 100%)
