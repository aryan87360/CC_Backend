import os
import requests
import json
from fastapi import FastAPI, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from dotenv import load_dotenv
import firebase_admin
from firebase_admin import credentials, auth

# Load environment variables from .env file
load_dotenv()

app = FastAPI(title="Palantir Foundry Proxy Backend", version="1.0.0")

from pydantic import BaseModel

# --- Configuration ---
FOUNDRY_URL = os.getenv("FOUNDRY_URL")
FOUNDRY_TOKEN = os.getenv("FOUNDRY_TOKEN")
ONTOLOGY_RID = os.getenv("ONTOLOGY_RID")
OBJECT_TYPE = os.getenv("OBJECT_TYPE", "CleanCreditCardTransactions")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@example.com")
FIREBASE_CREDENTIALS_JSON = os.getenv("FIREBASE_CREDENTIALS_JSON")
FIREBASE_CREDENTIALS_PATH = os.getenv("FIREBASE_CREDENTIALS_PATH")
FIREBASE_WEB_API_KEY = os.getenv("FIREBASE_WEB_API_KEY")

# --- Firebase Initialization ---
if not firebase_admin._apps:
    try:
        if FIREBASE_CREDENTIALS_PATH and os.path.exists(FIREBASE_CREDENTIALS_PATH):
            cred = credentials.Certificate(FIREBASE_CREDENTIALS_PATH)
            firebase_admin.initialize_app(cred)
        elif FIREBASE_CREDENTIALS_JSON:
            cred_dict = json.loads(FIREBASE_CREDENTIALS_JSON)
            cred = credentials.Certificate(cred_dict)
            firebase_admin.initialize_app(cred)
    except Exception as e:
        print(f"Failed to initialize Firebase Admin SDK: {e}")

security = HTTPBearer()

def get_current_user(creds: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """
    Validates the Firebase ID token and returns the user object.
    """
    token = creds.credentials
    try:
        decoded_token = auth.verify_id_token(token)
        return decoded_token
    except Exception as e:
        raise HTTPException(
            status_code=401,
            detail=f"Invalid authentication credentials: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )

# --- Token Management ---
def get_foundry_token() -> str:
    """
    Returns the configured long-lived token.
    """
    if not FOUNDRY_TOKEN:
        raise HTTPException(status_code=500, detail="FOUNDRY_TOKEN is not configured in .env")
    return FOUNDRY_TOKEN

# --- Models ---
class LoginRequest(BaseModel):
    email: str
    password: str

# --- Routes ---

@app.post("/api/login")
def login(request: LoginRequest):
    """
    Authenticates a user using Firebase REST API and returns an ID token.
    """
    if not FIREBASE_WEB_API_KEY:
        raise HTTPException(status_code=500, detail="FIREBASE_WEB_API_KEY is not configured.")
        
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={FIREBASE_WEB_API_KEY}"
    payload = {
        "email": request.email,
        "password": request.password,
        "returnSecureToken": True
    }
    
    response = requests.post(url, json=payload)
    data = response.json()
    
    if response.status_code == 200:
        return data
    else:
        error_msg = data.get("error", {}).get("message", "Authentication failed")
        raise HTTPException(status_code=401, detail=error_msg)

@app.get("/")
def read_root():
    return {"message": "Palantir Foundry Proxy Backend is running with Firebase Auth."}

@app.get("/api/transactions")
def get_transactions(limit: int = 100, user: dict = Depends(get_current_user)):
    """
    Fetches records from the CleanCreditCardTransactions object type in Foundry.
    Requires authentication.
    """
    if not ONTOLOGY_RID:
        raise HTTPException(status_code=500, detail="ONTOLOGY_RID is not configured.")

    token = get_foundry_token()
    objects_url = f"{FOUNDRY_URL}/api/v1/ontologies/{ONTOLOGY_RID}/objects/{OBJECT_TYPE}"
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    params = {
        "pageSize": limit
    }
    
    response = requests.get(objects_url, headers=headers, params=params)
    
    if response.status_code != 200:
        raise HTTPException(
            status_code=response.status_code, 
            detail=f"Failed to fetch data from Palantir: {response.text}"
        )
        
    return response.json()

@app.post("/api/transactions/edit")
def edit_transaction(payload: dict, user: dict = Depends(get_current_user)):
    """
    Proxies an action call to Foundry to edit a transaction.
    Role-based access:
    - Admins can edit all fields.
    - Regular users can only edit the description.
    """
    user_email = user.get("email")
    is_admin = user_email == ADMIN_EMAIL

    if not is_admin:
        # Regular users can only send 'transactionRid' and 'description'
        allowed_keys = {"transactionRid", "description"}
        extra_keys = set(payload.keys()) - allowed_keys
        if extra_keys:
            raise HTTPException(
                status_code=403,
                detail=f"Forbidden: Regular users can only edit the description. Invalid fields: {', '.join(extra_keys)}"
            )

    if not ONTOLOGY_RID:
        raise HTTPException(status_code=500, detail="ONTOLOGY_RID is not configured.")

    token = get_foundry_token()
    
    action_api_name = "edit-clean-credit-card-transactions"
    action_url = f"{FOUNDRY_URL}/api/v1/ontologies/{ONTOLOGY_RID}/actions/{action_api_name}/apply"
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    # Transform payload for Foundry
    foundry_payload = {
        "parameters": {
            "CleanCreditCardTransactions": payload.get("transactionRid"),
            "description": payload.get("description"),
            "amount": payload.get("amount"),
            "city": payload.get("city"),
            "type": payload.get("type")
        }
    }
    
    response = requests.post(action_url, headers=headers, json=foundry_payload)
    
    if response.status_code not in [200, 204]:
        raise HTTPException(
            status_code=response.status_code, 
            detail=f"Failed to apply action in Palantir: {response.text}"
        )
        
    return {"status": "success"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
