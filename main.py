import os
import uuid
import requests
import json
from collections import deque
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Depends, Request
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
FOUNDRY_WEBHOOK_SECRET = os.getenv("FOUNDRY_WEBHOOK_SECRET", "")  # Optional shared secret for webhook validation

# --- In-Memory Notification Store ---
# Rolling window of the last 50 events sent by Foundry via webhook.
# Resets on server restart (acceptable for testing).
MAX_NOTIFICATIONS = 50
notifications: deque = deque(maxlen=MAX_NOTIFICATIONS)


def _build_notification_message(payload: dict) -> str:
    """Build a human-readable message from the Foundry webhook payload."""
    action = payload.get("action") or payload.get("actionType") or "unknown action"
    obj_rid = payload.get("objectRid") or payload.get("primaryKey") or "unknown object"
    modified = payload.get("modifiedProperties") or payload.get("changedFields") or []
    if isinstance(modified, list) and modified:
        fields = ", ".join(str(f) for f in modified)
        return f"Foundry: '{action}' — fields changed: {fields} (object: {obj_rid})"
    return f"Foundry: '{action}' applied on object {obj_rid}"

# --- Firebase Initialization ---
def initialize_firebase():
    if not firebase_admin._apps:
        try:
            if FIREBASE_CREDENTIALS_PATH and os.path.exists(FIREBASE_CREDENTIALS_PATH):
                cred = credentials.Certificate(FIREBASE_CREDENTIALS_PATH)
                firebase_admin.initialize_app(cred)
                return "Initialized from PATH"
            elif FIREBASE_CREDENTIALS_JSON:
                try:
                    # Strip surrounding single or double quotes added by Vercel dashboard
                    json_str = FIREBASE_CREDENTIALS_JSON.strip().strip("'\"")
                    
                    # If it still contains escaped newlines as literal string "\\n", fix them
                    # This is a common issue when pasting into Vercel env vars
                    if "\\n" in json_str and "\n" not in json_str:
                        # But be careful not to break valid JSON. 
                        # Only fix it if it's a double-escaped private key issue.
                        pass # json.loads actually handles \\n fine if it's inside a string

                    cred_dict = json.loads(json_str)
                    
                    # CRITICAL: Firebase private key MUST have actual newlines.
                    # If it has literal "\n" characters, replace them with real newlines.
                    if "private_key" in cred_dict:
                        cred_dict["private_key"] = cred_dict["private_key"].replace("\\n", "\n")
                    
                    cred = credentials.Certificate(cred_dict)
                    firebase_admin.initialize_app(cred)
                    return "Initialized from JSON (fixed newlines)"
                except json.JSONDecodeError as e:
                    return f"JSON Decode Error: {str(e)}"
                except Exception as e:
                    return f"JSON Credential Error: {str(e)}"
            else:
                return "Missing Credentials: Both FIREBASE_CREDENTIALS_PATH and FIREBASE_CREDENTIALS_JSON are empty or invalid."
        except Exception as e:
            return f"General Initialization Error: {str(e)}"
    return "Already Initialized"

# Call initialization at module level
init_status = initialize_firebase()
print(f"Firebase Init Status: {init_status}")

security = HTTPBearer()

def get_current_user(creds: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """
    Validates the Firebase ID token and returns the user object.
    """
    # Ensure initialized (backup for serverless environments)
    status = initialize_firebase()
    
    if not firebase_admin._apps:
        raise HTTPException(
            status_code=500,
            detail=f"Backend Error: Firebase Admin SDK not initialized. Status: {status}"
        )

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

@app.post("/api/signup")
def signup(request: LoginRequest):
    """
    Creates a new user using Firebase REST API and returns an ID token.
    """
    if not FIREBASE_WEB_API_KEY:
        raise HTTPException(status_code=500, detail="FIREBASE_WEB_API_KEY is not configured.")
        
    url = f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={FIREBASE_WEB_API_KEY}"
    payload = {
        "email": request.email,
        "password": request.password,
        "returnSecureToken": True
    }
    
    response = requests.post(url, json=payload)
    data = response.json()
    
    if response.status_code == 200:
        data["isAdmin"] = data.get("email") == ADMIN_EMAIL
        return data
    else:
        error_msg = data.get("error", {}).get("message", "Signup failed")
        raise HTTPException(status_code=400, detail=error_msg)

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
        data["isAdmin"] = data.get("email") == ADMIN_EMAIL
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
    
    print(f"RBAC Check: User={user_email}, IsAdmin={is_admin}")

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
    
    # Compute which properties are being modified (for webhook side-effect)
    modified_properties = []
    if payload.get("description") is not None:
        modified_properties.append("description")
    if payload.get("amount") is not None:
        modified_properties.append("amount")
    if payload.get("city") is not None:
        modified_properties.append("city")
    if payload.get("type") is not None:
        modified_properties.append("type")
    if not modified_properties:
        modified_properties = ["description"]  # fallback so Required constraint is satisfied

    # Transform payload for Foundry
    foundry_payload = {
        "parameters": {
            "CleanCreditCardTransactions": payload.get("transactionRid"),
            "description": payload.get("description"),
            "amount": payload.get("amount"),
            "city": payload.get("city"),
            "type": payload.get("type"),
            # Webhook-required parameters
            "actionName": "edit-clean-credit-card-transactions",
            "objectRid": payload.get("transactionRid"),
            "modifiedProperties": modified_properties,
        }
    }
    
    response = requests.post(action_url, headers=headers, json=foundry_payload)
    
    if response.status_code not in [200, 204]:
        raise HTTPException(
            status_code=response.status_code, 
            detail=f"Failed to apply action in Palantir: {response.text}"
        )
        
    return {"status": "success"}


# ---------------------------------------------------------------------------
# Foundry → Backend communication
# ---------------------------------------------------------------------------

@app.post("/api/foundry-webhook")
async def foundry_webhook(request: Request):
    """
    Webhook receiver called by Foundry when an ontology object changes.
    Foundry must be configured to POST to this URL.

    Optional: set FOUNDRY_WEBHOOK_SECRET in .env and add the same value
    as a custom header (X-Foundry-Secret) in the Foundry webhook config
    for basic validation.
    """
    # --- Optional shared-secret validation ---
    if FOUNDRY_WEBHOOK_SECRET:
        incoming_secret = request.headers.get("X-Foundry-Secret", "")
        if incoming_secret != FOUNDRY_WEBHOOK_SECRET:
            raise HTTPException(status_code=403, detail="Invalid webhook secret")

    # Parse body (Foundry may send JSON or an empty body on some events)
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    entry = {
        "id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "foundry",
        "message": _build_notification_message(payload),
        "raw": payload,
    }
    notifications.appendleft(entry)
    print(f"📡 Foundry webhook received: {entry['message']}")
    return {"status": "received", "notificationId": entry["id"]}


@app.get("/api/notifications")
def get_notifications():
    """
    Returns the list of recent Foundry-sourced notifications.
    No authentication required (testing purposes — visible to all).
    iOS app polls this endpoint every 15 seconds.
    """
    return {"notifications": list(notifications)}


@app.delete("/api/notifications")
def clear_notifications():
    """
    Clears all stored notifications. Useful for testing.
    """
    notifications.clear()
    return {"status": "cleared", "count": 0}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
