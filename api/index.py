import os
import requests
from fastapi import FastAPI, HTTPException
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

app = FastAPI(title="Palantir Foundry Proxy Backend", version="1.0.0")

# --- Configuration ---
FOUNDRY_URL = os.getenv("FOUNDRY_URL")
FOUNDRY_TOKEN = os.getenv("FOUNDRY_TOKEN")
ONTOLOGY_RID = os.getenv("ONTOLOGY_RID")
OBJECT_TYPE = os.getenv("OBJECT_TYPE", "CleanCreditCardTransactions")

# --- Token Management ---
def get_foundry_token() -> str:
    """
    Returns the configured long-lived token.
    """
    if not FOUNDRY_TOKEN:
        raise HTTPException(status_code=500, detail="FOUNDRY_TOKEN is not configured in .env")
    return FOUNDRY_TOKEN

# --- Routes ---

@app.get("/")
def read_root():
    return {"message": "Palantir Foundry Proxy Backend is running with Token Auth."}

@app.get("/api/transactions")
def get_transactions(limit: int = 100):
    """
    Fetches records from the CleanCreditCardTransactions object type in Foundry.
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
def edit_transaction(payload: dict):
    """
    Proxies an action call to Foundry to edit a transaction.
    Expected payload: { "transactionRid": "...", "description": "...", "amount": ..., "city": "...", "type": "..." }
    """
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
