# CardioVet CR Generator — Railway.app

Serveur Python qui génère les comptes rendus échocardiographiques via Claude Sonnet.

## Déploiement sur Railway

1. Créez un compte sur https://railway.app (gratuit)
2. New Project → Deploy from GitHub repo
   OU : New Project → Empty project → drag-drop ce dossier
3. Variables d'environnement à ajouter :
   - CLAUDE_API_KEY = votre clé API Anthropic
4. L'URL de déploiement (ex: https://cardiovet-cr.up.railway.app) 
   → à mettre dans CardioVet > Paramètres > URL Railway

## Endpoints
- GET  /health       → vérification que le serveur est actif  
- POST /generate-cr  → génère le CR (body: JSON avec xmlData, patient)
